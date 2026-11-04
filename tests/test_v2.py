"""Version 2: workspaces, invites, files, time tracking, burndown, reminders, notifications, live events,
languages, password reset, two-factor login and the resume builder."""

import io
import json
import time
import unittest
from datetime import date, datetime, timedelta

from support import Base, FakeNotifier, target, DRIVER

import otp
from core import TaskError, fmt_minutes, parse_duration
from store import Store

TODAY = date.today()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()


def png_bytes(w=600, h=300, color="#4f46e5"):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, "PNG")
    return out.getvalue()


class TestWorkspaces(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_create_and_switch_workspace(self):
        self.post("/workspaces", {"name": "Marketing team", "description": "Ads", "color": "#ec4899"})
        mine = {w["name"]: w for w in self.store.memberships(self.uid("admin@taskflow.demo"))}
        self.assertEqual(mine["Marketing team"]["role"], "admin")
        html = self.html("/board")                                   # the new workspace is open now
        self.assertNotIn("Two-step fund transfer", html)
        self.post(f"/workspaces/{self.first_ws()}/open")
        self.assertIn("Two-step fund transfer", self.html("/board"))

    def test_data_of_workspaces_is_separate(self):
        a, b = self.first_ws(), self.second_ws()
        titles_a = {t["title"] for t in self.store.tasks(a)}
        titles_b = {t["title"] for t in self.store.tasks(b)}
        self.assertFalse(titles_a & titles_b)
        self.assertTrue(all(t["workspace_id"] == b for t in self.store.tasks(b)))
        self.assertNotEqual(self.store.stats(a)["total"], self.store.stats(b)["total"])

    def test_link_to_task_of_other_workspace_switches(self):
        sfu_task = self.store.tasks(self.second_ws())[0]
        r = self.c.get(f"/tasks/{sfu_task['id']}")
        self.assertEqual(r.status_code, 302)
        self.assertIn(sfu_task["title"].split(" (")[0][:20], self.html(f"/tasks/{sfu_task['id']}"))

    def test_invite_link_for_new_person(self):
        ws = self.store.workspace(self.first_ws())
        self.assertIn("/join/" + ws["invite_code"], self.html("/workspace/settings"))
        visitor = self.app.test_client()
        self.assertIn(ws["name"].replace("'", "&#39;"), visitor.get("/join/" + ws["invite_code"]).get_data(as_text=True))
        r = self.post("/register?invite=" + ws["invite_code"], {"name": "Kofi Mensah", "email": "kofi@teamtest.io",
                      "password": "Welcome123", "confirm": "Welcome123", "invite": ws["invite_code"]}, client=visitor)
        self.assertEqual(r.status_code, 302)
        kofi = self.uid("kofi@teamtest.io")
        self.assertEqual([w["id"] for w in self.store.memberships(kofi)], [ws["id"]])
        self.assertEqual(self.store.member_role(ws["id"], kofi), "member")

    def test_invite_link_for_existing_account(self):
        ws = self.store.workspace(self.first_ws())
        li = self.new_client("li@taskflow.demo")
        self.post("/join/" + ws["invite_code"], client=li)
        self.assertEqual(self.store.member_role(ws["id"], self.uid("li@taskflow.demo")), "member")

    def test_new_invite_link_stops_the_old_one(self):
        old = self.store.workspace(self.first_ws())["invite_code"]
        self.post("/workspace/settings", {"action": "invite"})
        new = self.store.workspace(self.first_ws())["invite_code"]
        self.assertNotEqual(old, new)
        self.assertEqual(self.c.get("/join/" + old).status_code, 302)
        self.assertIsNone(self.store.workspace_by_invite(old))
        with self.assertRaises(TaskError):
            self.store.register("X Y", "xy@teamtest.io", "Welcome123", invite=old)

    def test_invite_works_when_sign_up_is_closed(self):
        self.post("/admin/settings", {})                             # sign-up off
        code = self.store.workspace(self.first_ws())["invite_code"]
        visitor = self.app.test_client()
        self.assertEqual(visitor.get("/register").status_code, 302)
        self.assertEqual(visitor.get("/register?invite=" + code).status_code, 200)

    def test_workspace_settings_and_delete(self):
        ws = self.first_ws()
        self.post("/workspace/settings", {"name": "Dev Studio", "description": "d", "color": "#10b981",
                                          "default_priority": "high"})
        w = self.store.workspace(ws)
        self.assertEqual((w["name"], w["default_priority"]), ("Dev Studio", "high"))
        self.assertIn('value="high" selected', self.html("/tasks/new").replace("  ", " "))
        self.post("/workspace/settings", {"action": "delete", "confirm": "wrong name"})
        self.assertIsNotNone(self.store.workspace(w["id"]))
        self.post("/workspace/settings", {"action": "delete", "confirm": "Dev Studio"})
        self.assertIsNone(self.store.workspace(w["id"]))
        self.assertEqual(self.store.tasks(w["id"]), [])
        self.assertEqual(self.c.get("/").status_code, 200)           # the other workspace opens

    def test_leave_and_last_admin(self):
        sam = self.new_client("samuel@taskflow.demo")
        self.post("/workspace/leave", client=sam)
        self.assertEqual(len(self.store.memberships(self.uid("samuel@taskflow.demo"))), 1)
        with self.assertRaises(TaskError):
            self.store.remove_member(self.uid("admin@taskflow.demo"), self.second_ws(), self.uid("admin@taskflow.demo"))

    def test_remove_member_unassigns_tasks(self):
        anna = self.uid("anna@taskflow.demo")
        self.assertTrue(self.store.tasks(self.first_ws(), assignee=anna))
        self.post(f"/team/{anna}/remove")
        self.assertIsNone(self.store.member_role(self.first_ws(), anna))
        self.assertEqual(self.store.tasks(self.first_ws(), assignee=anna), [])
        self.assertIsNotNone(self.store.user(anna))                  # the account stays

    def test_version1_database_is_moved_into_a_workspace(self):
        path = target(False)
        s = Store(path, driver=DRIVER, notifier=FakeNotifier(), env={})
        uid = s.create_user("Old Admin", "old@teamtest.io", "Secret123")
        with s.db() as con:
            con.execute("DELETE FROM workspaces")
            con.execute("DELETE FROM memberships")
            con.execute("INSERT INTO tasks (title, status, priority, created_by, created_at, updated_at) "
                        "VALUES ('Old task', 'todo', 'low', ?, '2025-01-01 10:00:00', '2025-01-01 10:00:00')", (uid,))
        s2 = Store(path, driver=DRIVER, notifier=FakeNotifier(), env={})
        ws = s2.memberships(uid)[0]
        self.assertEqual(ws["role"], "admin")
        self.assertEqual([t["title"] for t in s2.tasks(ws["id"])], ["Old task"])


class TestFiles(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.tid = self.store.tasks(self.first_ws(), q="Two-step fund transfer")[0]["id"]

    def upload(self, name, data, client=None):
        return self.post(f"/tasks/{self.tid}/files", {"file": (io.BytesIO(data), name)}, client=client,
                         content_type="multipart/form-data")

    def test_upload_download_delete(self):
        self.upload("screen shot.png", png_bytes())
        f = self.store.attachments(self.tid)[0]
        self.assertEqual((f["name"], f["mime"]), ("screen shot.png", "image/png"))
        r = self.c.get(f"/files/{f['id']}")
        self.assertEqual(r.data, png_bytes())
        self.assertIn("default-src 'none'", r.headers["Content-Security-Policy"])
        self.assertIn("screen shot.png", self.html(f"/tasks/{self.tid}"))
        self.post(f"/files/{f['id']}/delete")
        self.assertEqual(self.store.attachments(self.tid), [])

    def test_pdf_download_and_text_attachment(self):
        self.upload("notes.txt", b"hello")
        r = self.c.get(f"/files/{self.store.attachments(self.tid)[0]['id']}")
        self.assertIn("attachment", r.headers["Content-Disposition"])           # text is downloaded, not shown

    def test_wrong_files_are_refused(self):
        for name, data in [("virus.exe", b"MZ..."), ("fake.png", b"not a picture"), ("empty.pdf", b""),
                           ("big.zip", b"PK" + b"0" * (5 * 1024 * 1024))]:
            self.upload(name, data)
        self.assertEqual(self.store.attachments(self.tid), [])

    def test_path_in_name_is_removed(self):
        self.upload("../../etc/passwd.txt", b"x")
        self.assertEqual(self.store.attachments(self.tid)[0]["name"], "passwd.txt")

    def test_at_most_ten_files(self):
        for i in range(11):
            self.upload(f"f{i}.txt", b"x")
        self.assertEqual(len(self.store.attachments(self.tid)), 10)

    def test_people_of_other_workspaces_cannot_download(self):
        self.upload("secret.txt", b"secret")
        fid = self.store.attachments(self.tid)[0]["id"]
        li = self.new_client("li@taskflow.demo")
        self.assertEqual(li.get(f"/files/{fid}").status_code, 404)
        anna = self.new_client("anna@taskflow.demo")                  # same workspace
        self.assertEqual(anna.get(f"/files/{fid}").status_code, 200)
        self.assertEqual(self.post(f"/files/{fid}/delete", client=anna).status_code, 403)


class TestTimeTracking(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.ws = self.first_ws()
        self.me = self.uid("admin@taskflow.demo")
        self.t1 = self.store.create_task(self.me, self.ws, {"title": "Timer task one", "status": "todo",
                                                            "priority": "low", "estimate": "2h"})
        self.t2 = self.store.create_task(self.me, self.ws, {"title": "Timer task two", "status": "todo",
                                                            "priority": "low"})

    def test_parse_duration(self):
        for text, minutes in [("1.5", 90), ("1,5", 90), ("90m", 90), ("2h", 120), ("1h 30m", 90), ("1:30", 90),
                              ("45 min", 45)]:
            self.assertEqual(parse_duration(text), minutes, text)
        self.assertIsNone(parse_duration(""))
        for bad in ["abc", "0", "-2", "inf", "nan", "100000"]:
            with self.assertRaises(TaskError):
                parse_duration(bad)
        self.assertEqual((fmt_minutes(95), fmt_minutes(60), fmt_minutes(20)), ("1h 35m", "1h", "20m"))

    def test_timer_start_stop(self):
        self.post(f"/tasks/{self.t1}/timer")
        running = self.store.running_timer(self.me)
        self.assertEqual(running["task_id"], self.t1)
        self.assertIn("timer-chip", self.html("/"))
        start = datetime.strptime(running["started_at"], "%Y-%m-%d %H:%M:%S")
        minutes = self.store.stop_timer(self.me, at=(start + timedelta(minutes=90)).strftime("%Y-%m-%d %H:%M:%S"))
        self.assertEqual(minutes, 90)
        self.assertIsNone(self.store.running_timer(self.me))
        self.assertEqual(self.store.task(self.t1)["logged_minutes"], 90)

    def test_starting_a_second_timer_stops_the_first(self):
        self.store.start_timer(self.me, self.t1)
        self.store.start_timer(self.me, self.t2)
        self.assertEqual(self.store.running_timer(self.me)["task_id"], self.t2)
        self.assertEqual(len([e for e in self.store.time_entries(self.t1) if e["ended_at"]]), 1)
        self.assertIsNone(self.store.stop_timer(self.me + 1000))    # nothing to stop

    def test_forgotten_timer_counts_at_most_12_hours(self):
        self.store.start_timer(self.me, self.t1)
        start = datetime.strptime(self.store.running_timer(self.me)["started_at"], "%Y-%m-%d %H:%M:%S")
        self.assertEqual(self.store.stop_timer(self.me, at=(start + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")),
                         720)

    def test_log_time_by_hand(self):
        self.post(f"/tasks/{self.t1}/time", {"time": "1h 15m", "day": TODAY.isoformat(), "note": "Coding"})
        e = self.store.time_entries(self.t1)[0]
        self.assertEqual((e["minutes"], e["note"]), (75, "Coding"))
        self.post(f"/tasks/{self.t1}/time", {"time": "1h", "day": TOMORROW})          # future day
        self.post(f"/tasks/{self.t1}/time", {"time": "lots"})
        self.assertEqual(len(self.store.time_entries(self.t1)), 1)

    def test_only_own_entries_can_be_deleted_by_members(self):
        self.store.log_time(self.me, self.t1, 30)
        eid = self.store.time_entries(self.t1)[0]["id"]
        anna = self.new_client("anna@taskflow.demo")
        self.assertEqual(self.post(f"/time/{eid}/delete", client=anna).status_code, 403)
        self.post(f"/time/{eid}/delete")
        self.assertEqual(self.store.time_entries(self.t1), [])

    def test_time_report_and_csv(self):
        self.store.log_time(self.me, self.t1, 60, note="Report check")
        rep = self.store.time_report(self.ws)
        me = [p for p in rep["people"] if p["id"] == self.me][0]
        self.assertGreaterEqual(me["minutes"], 60)
        self.assertEqual(rep["total"], sum(p["minutes"] for p in rep["people"]))
        self.assertIn("Report check", self.c.get("/reports/time.csv").get_data(as_text=True))

    def test_burndown_numbers(self):
        s = Store(target(False), driver=DRIVER, notifier=FakeNotifier(), env={})
        uid = s.create_user("Burn Down", "burn@teamtest.io", "Secret123")
        ws = s.create_workspace(uid, "Sprint")
        today = date(2026, 3, 20)
        d = lambda n: (today - timedelta(days=n)).strftime("%Y-%m-%d 10:00:00")     # noqa: E731
        with s.db() as con:
            for created, completed, est in [(9, 5, 120), (9, 2, 60), (9, None, 180), (4, None, 60)]:
                con.execute("INSERT INTO tasks (title, status, priority, created_by, created_at, updated_at, completed_at, "
                            "workspace_id, estimate_minutes) VALUES ('t', ?, 'low', ?, ?, ?, ?, ?, ?)",
                            ("done" if completed else "todo", uid, d(created), d(created),
                             d(completed) if completed else None, ws, est))
        bd = s.burndown(ws, days=10, today=today)
        by_day = {p["day"]: p for p in bd["points"]}
        self.assertEqual(by_day[today - timedelta(days=9)]["remaining"], 3)
        self.assertEqual(by_day[today - timedelta(days=5)]["remaining"], 2)
        self.assertEqual(by_day[today - timedelta(days=4)]["remaining"], 3)        # new work was added
        self.assertEqual(by_day[today]["remaining"], 2)
        self.assertEqual(bd["points"][0]["ideal"], 3)
        self.assertEqual(bd["points"][-1]["ideal"], 0)
        hours = s.burndown(ws, days=10, metric="hours", today=today)
        self.assertEqual(hours["remaining"], 4.0)                                # 180 + 60 minutes still open
        svg = s.chart_svg_points(bd)
        self.assertEqual(len(svg["remaining"].split()), len(bd["points"]))

    def test_reports_page_with_options(self):
        p = self.store.projects(self.ws)[0]
        for q in ["", "?metric=hours", f"?project={p['id']}&days=30", "?days=60&metric=tasks", "?days=abc"]:
            self.assertIn("<svg class=\"burndown\"", self.html("/reports" + q))


class TestRemindersAndNotifications(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.ws = self.first_ws()
        self.me = self.uid("admin@taskflow.demo")
        self.post("/team/add", {"name": "Kofi Mensah", "email": "kofi@teamtest.io", "password": "Welcome123",
                                "role": "member"})
        self.kofi = self.uid("kofi@teamtest.io")
        self.mail.sent.clear()

    def test_assigning_a_task_notifies_and_emails(self):
        tid = self.store.create_task(self.me, self.ws, {"title": "Fix the login page", "status": "todo",
                                                        "priority": "high", "assignee_id": str(self.kofi)})
        notes = self.store.notifications(self.kofi)
        self.assertIn("Fix the login page", notes[0]["text"])
        self.assertEqual(notes[0]["task_id"], tid)
        self.assertEqual(self.mail.sent[-1][0], "kofi@teamtest.io")
        self.assertEqual(notes[0]["emailed"], "sent")

    def test_demo_addresses_are_never_emailed(self):
        anna = self.uid("anna@taskflow.demo")
        self.store.create_task(self.me, self.ws, {"title": "For Anna", "status": "todo", "priority": "low",
                                                  "assignee_id": str(anna)})
        self.assertEqual(self.mail.sent, [])
        self.assertTrue(self.store.notifications(anna))                       # still on the site

    def test_email_can_be_switched_off(self):
        self.store.save_notify_settings(self.kofi, False, 1)
        self.store.create_task(self.me, self.ws, {"title": "Quiet task", "status": "todo", "priority": "low",
                                                  "assignee_id": str(self.kofi)})
        self.assertEqual(self.mail.sent, [])

    def test_reminder_one_day_before_once(self):
        tid = self.store.create_task(self.me, self.ws, {"title": "Send the invoice", "status": "todo",
                                                        "priority": "low", "assignee_id": str(self.kofi),
                                                        "due_date": TOMORROW})
        self.mail.sent.clear()
        self.assertGreaterEqual(self.store.send_reminders(), 1)
        self.assertTrue(any("Send the invoice" in m[2] and "tomorrow" in m[2] for m in self.mail.sent))
        before = len(self.mail.sent)
        self.store.send_reminders()
        self.assertEqual(len(self.mail.sent), before)                            # only once per due date
        later = (TODAY + timedelta(days=1)).isoformat()
        self.store.update_task(self.me, self.ws, tid, {"title": "Send the invoice", "status": "todo", "priority": "low",
                                                       "assignee_id": str(self.kofi), "due_date": later})
        self.assertEqual(self.store.task(tid)["reminded_for"], later)            # same date: no new reminder

    def test_reminder_settings(self):
        due2 = (TODAY + timedelta(days=2)).isoformat()
        self.store.create_task(self.me, self.ws, {"title": "Two days left", "status": "todo", "priority": "low",
                                                  "assignee_id": str(self.kofi), "due_date": due2})
        self.store.send_reminders()
        reminders = lambda: [m for m in self.mail.sent if m[1] == "Deadline reminder" and "Two days left" in m[2]]  # noqa
        self.assertEqual(reminders(), [])                                         # default: one day before
        self.store.save_notify_settings(self.kofi, True, 2)
        self.store.send_reminders()
        self.assertEqual(len(reminders()), 1)
        self.store.save_notify_settings(self.kofi, True, 0)
        with self.assertRaises(TaskError):
            self.store.save_notify_settings(self.kofi, True, 9)

    def test_done_tasks_get_no_reminder(self):
        self.store.create_task(self.me, self.ws, {"title": "Already done", "status": "done", "priority": "low",
                                                  "assignee_id": str(self.kofi), "due_date": TOMORROW})
        self.store.send_reminders()
        self.assertFalse(any(m[1] == "Deadline reminder" and "Already done" in m[2] for m in self.mail.sent))

    def test_mentions_in_comments(self):
        tid = self.store.tasks(self.ws)[0]["id"]
        self.post(f"/tasks/{tid}/comment", {"body": "@Kofi please look at this"})
        self.assertTrue(any(n["kind"] == "mention" for n in self.store.notifications(self.kofi)))

    def test_bell_and_notifications_page(self):
        kofi = self.new_client("kofi@teamtest.io", "Welcome123")
        self.store.create_task(self.me, self.ws, {"title": "Bell task", "status": "todo", "priority": "low",
                                                  "assignee_id": str(self.kofi)})
        self.assertGreaterEqual(self.store.unseen_count(self.kofi), 1)
        self.assertIn("Bell task", self.html("/notifications", client=kofi))
        self.assertEqual(self.store.unseen_count(self.kofi), 0)

    def test_cron_endpoint_needs_the_key(self):
        self.assertEqual(self.c.get("/cron/run").status_code, 404)
        self.app.view_functions                                              # noqa: B018
        from app import create_app
        app = create_app(self.store.database.target, seed=False, testing=True, notifier=self.mail, driver=DRIVER,
                         env={"CRON_KEY": "k3y"})
        c = app.test_client()
        self.assertEqual(c.get("/cron/run?key=wrong").status_code, 404)
        r = c.post("/cron/run?key=k3y")
        self.assertEqual(r.get_json()["ok"], True)


class TestLiveEvents(Base):
    def setUp(self):
        super().setUp()
        self.ws = self.first_ws()

    def test_events_reach_the_workspace_only(self):
        anna = self.new_client("anna@taskflow.demo")
        li = self.new_client("li@taskflow.demo")                           # other workspace
        anna.get("/lang/en")
        last = anna.get("/api/events").get_json()["last"]
        last_li = li.get("/api/events").get_json()["last"]
        admin = self.uid("admin@taskflow.demo")
        tid = self.store.create_task(admin, self.ws, {"title": "Live task", "status": "todo", "priority": "low"})
        self.store.set_status(admin, self.ws, tid, "in_progress")
        events = anna.get(f"/api/events?after={last}").get_json()["events"]
        texts = [e["text"] for e in events]
        self.assertIn("Jean Michel Mujyambere created “Live task”", texts)
        self.assertIn("Jean Michel Mujyambere moved “Live task” to In Progress", texts)
        self.assertFalse(any(e["me"] for e in events))
        self.assertEqual(li.get(f"/api/events?after={last_li}").get_json()["events"], [])
        anna.get("/lang/ru")                                                  # every viewer reads it in own language
        texts = [e["text"] for e in anna.get(f"/api/events?after={last}").get_json()["events"]]
        self.assertIn("Jean Michel Mujyambere переместил(а) «Live task» в «В работе»", texts)

    def test_personal_notification_event(self):
        anna_id = self.uid("anna@taskflow.demo")
        anna = self.new_client("anna@taskflow.demo")
        david = self.new_client("david@taskflow.demo")
        last = anna.get("/api/events").get_json()["last"]
        self.store.create_task(self.uid("admin@taskflow.demo"), self.ws,
                               {"title": "For you", "status": "todo", "priority": "low", "assignee_id": str(anna_id)})
        kinds = [e["kind"] for e in anna.get(f"/api/events?after={last}").get_json()["events"]]
        self.assertIn("notification", kinds)
        self.assertNotIn("notification", [e["kind"] for e in david.get(f"/api/events?after={last}").get_json()["events"]])

    def test_websocket_route_exists(self):
        self.assertTrue(self.app.config["WEBSOCKETS"])
        self.assertIn("/ws", [r.rule for r in self.app.url_map.iter_rules()])

    def test_events_need_login(self):
        self.assertEqual(self.c.get("/api/events").status_code, 302)


class TestPasswordResetAnd2FA(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.post("/team/add", {"name": "Kofi Mensah", "email": "kofi@teamtest.io", "password": "Welcome123",
                                "role": "member"})
        self.post("/logout")
        self.mail.sent.clear()

    def reset_link(self):
        self.post("/forgot", {"email": "kofi@teamtest.io"})
        text = self.mail.sent[-1][2]
        return "/reset/" + text.split("/reset/")[1].split()[0]

    def test_reset_by_email_works_once(self):
        link = self.reset_link()
        self.assertIn("Choose a new password", self.html(link))
        self.post(link, {"new": "BrandNew123", "confirm": "BrandNew123"})
        self.assertTrue(self.store.authenticate("kofi@teamtest.io", "BrandNew123"))
        self.post(link, {"new": "Another123", "confirm": "Another123"})
        self.assertFalse(self.store.authenticate("kofi@teamtest.io", "Another123"))

    def test_reset_for_unknown_or_demo_email_sends_nothing(self):
        for email in ["nobody@teamtest.io", "anna@taskflow.demo"]:
            r = self.post("/forgot", {"email": email})
            self.assertEqual(r.status_code, 302)
        self.assertEqual(self.mail.sent, [])
        self.assertIn("expired", self.html("/reset/not-a-real-token"))

    def test_expired_link(self):
        link = self.reset_link()
        with self.store.db() as con:
            con.execute("UPDATE password_resets SET expires_at = '2000-01-01 00:00:00'")
        self.post(link, {"new": "BrandNew123", "confirm": "BrandNew123"})
        self.assertFalse(self.store.authenticate("kofi@teamtest.io", "BrandNew123"))

    def test_two_factor_login(self):
        self.login("kofi@teamtest.io", "Welcome123")
        self.html("/profile")
        with self.c.session_transaction() as s:
            secret = s["totp_setup"]
        self.post("/profile", {"form": "2fa_on", "code": "000000"})
        self.assertFalse(self.store.user(self.uid("kofi@teamtest.io"))["totp_enabled"])
        self.post("/profile", {"form": "2fa_on", "code": otp.totp(secret)})
        self.assertTrue(self.store.user(self.uid("kofi@teamtest.io"))["totp_enabled"])
        self.post("/logout")
        r = self.login("kofi@teamtest.io", "Welcome123")
        self.assertIn("/login/verify", r.location)
        self.assertEqual(self.c.get("/").status_code, 302)                      # not signed in yet
        self.assertEqual(self.post("/login/verify", {"code": "123456"}).status_code, 401)
        r = self.post("/login/verify", {"code": otp.totp(secret, at=time.time() + 30)})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/").status_code, 200)

    def test_site_admin_can_reset_2fa(self):
        kofi = self.uid("kofi@teamtest.io")
        secret = otp.new_secret()
        self.store.enable_2fa(kofi, secret, otp.totp(secret))
        self.login()
        self.post(f"/team/{kofi}/reset2fa")
        self.assertFalse(self.store.user(kofi)["totp_enabled"])


class TestResumes(Base):
    def setUp(self):
        super().setUp()
        self.login("anna@taskflow.demo", "Demo@1234")
        self.anna = self.uid("anna@taskflow.demo")

    def make(self):
        r = self.post("/resumes/new", {"title": "Job hunt", "theme": "classic", "accent": "#0f766e", "lang": "en"})
        self.assertEqual(r.status_code, 302)
        return int(r.location.split("/")[-2])

    def payload(self, rid, **kw):
        r = self.store.resume(rid)
        p = {"title": r["title"], "slug": r["slug"], "theme": r["theme"], "font": r["font"], "lang": r["lang"],
             "visibility": r["visibility"], "accent": r["accent"], "content": r["content"]}
        p.update(kw)
        return p

    def test_new_resume_starts_from_profile(self):
        rid = self.make()
        r = self.store.resume(rid)
        self.assertEqual((r["content"]["name"], r["content"]["email"], r["visibility"]),
                         ("Anna Petrova", "anna@taskflow.demo", "private"))
        self.assertEqual(r["slug"], "anna-petrova-2")                      # the demo resume has "anna-petrova"
        self.assertIn("rzData", self.html(f"/resumes/{rid}/edit"))

    def test_save_from_the_editor(self):
        rid = self.make()
        content = self.store.resume(rid)["content"]
        content["headline"] = "Frontend Developer"
        content["links"] = [{"label": "GitHub", "url": "github.com/anna"}]
        content["sections"].append({"id": "x1", "type": "custom", "title": "Volunteering", "visible": True,
                                    "items": [{"title": "Code club mentor", "bullets": "Taught HTML\nTaught CSS"}]})
        r = self.post_json(f"/resumes/{rid}/save", self.payload(rid, content=content, slug="anna-frontend",
                                                                  visibility="link", theme="compact"))
        self.assertEqual(r.get_json()["ok"], True, r.get_json())
        saved = self.store.resume(rid)
        self.assertEqual((saved["slug"], saved["theme"], saved["visibility"]), ("anna-frontend", "compact", "link"))
        self.assertEqual(saved["content"]["links"][0]["url"], "https://github.com/anna")
        html = self.new_client().get("/r/anna-frontend").get_data(as_text=True)
        self.assertIn("Code club mentor", html)
        self.assertIn("<li>Taught CSS</li>", html)

    def test_editor_errors(self):
        rid = self.make()
        good = self.store.resume(rid)["content"]
        for kw in [{"slug": "Bad Slug"}, {"slug": "explore"}, {"slug": "jean-michel-mujyambere"},
                   {"content": dict(good, website="javascript:alert(1)")}, {"content": dict(good, name="")},
                   {"content": dict(good, email="not-an-email")},
                   {"content": dict(good, sections=[{"type": "evil", "items": []}])}]:
            r = self.post_json(f"/resumes/{rid}/save", self.payload(rid, **kw))
            self.assertEqual(r.status_code, 400, kw)
            self.assertFalse(r.get_json()["ok"])
        self.assertEqual(self.post_json(f"/resumes/{rid}/save", "not an object").status_code, 400)

    def test_visibility(self):
        rid = self.make()
        slug = self.store.resume(rid)["slug"]
        guest = self.new_client()
        self.assertEqual(guest.get(f"/r/{slug}").status_code, 404)                 # private
        self.assertEqual(self.c.get(f"/r/{slug}").status_code, 200)                # the owner sees it
        self.post_json(f"/resumes/{rid}/save", self.payload(rid, visibility="link"))
        self.assertEqual(guest.get(f"/r/{slug}").status_code, 200)
        self.assertNotIn(f"/r/{slug}\"", guest.get("/explore").get_data(as_text=True))
        self.post_json(f"/resumes/{rid}/save", self.payload(rid, visibility="public"))
        self.assertIn(f"/r/{slug}\"", guest.get("/explore").get_data(as_text=True))

    def test_views_are_counted_for_visitors_only(self):
        before = self.store.resume_by_slug("jean-michel-mujyambere")["views"]
        self.c.get("/r/jean-michel-mujyambere")
        self.new_client().get("/r/jean-michel-mujyambere")
        self.c.get("/r/anna-petrova")                                              # Anna's own resume
        self.assertEqual(self.store.resume_by_slug("jean-michel-mujyambere")["views"], before + 2)
        self.assertEqual(self.store.resume_by_slug("anna-petrova")["views"], 41)

    def test_others_cannot_edit(self):
        jean = self.store.resume_by_slug("jean-michel-mujyambere")
        self.assertEqual(self.c.get(f"/resumes/{jean['id']}/edit").status_code, 403)
        self.assertEqual(self.post_json(f"/resumes/{jean['id']}/save", {"content": {}}).status_code, 403)
        self.assertEqual(self.post(f"/resumes/{jean['id']}/delete").status_code, 403)
        self.assertEqual(self.c.get(f"/resumes/{jean['id']}/export.json").status_code, 403)

    def test_photo_is_cut_to_a_square(self):
        rid = self.make()
        self.post(f"/resumes/{rid}/photo", {"photo": (io.BytesIO(png_bytes(800, 500)), "me.png")},
                  content_type="multipart/form-data")
        r = self.store.resume(rid)
        self.assertTrue(r["has_photo"])
        from PIL import Image
        img = Image.open(io.BytesIO(self.c.get(f"/r/{r['slug']}/photo.jpg").data))
        self.assertEqual((img.format, img.size), ("JPEG", (400, 400)))
        r2 = self.post(f"/resumes/{rid}/photo", {"photo": (io.BytesIO(b"no picture"), "x.png")},
                       content_type="multipart/form-data", headers={"X-Requested-With": "fetch"})
        self.assertEqual(r2.status_code, 400)
        self.post(f"/resumes/{rid}/photo", {"remove": "1"})
        self.assertFalse(self.store.resume(rid)["has_photo"])

    def test_duplicate_export_import(self):
        rid = self.make()
        self.post(f"/resumes/{rid}/duplicate")
        self.assertEqual(len(self.store.resumes_of(self.anna)), 3)
        exported = self.c.get(f"/resumes/{rid}/export.json").get_data(as_text=True)
        self.post("/resumes/import", {"text": exported})
        self.assertEqual(len(self.store.resumes_of(self.anna)), 4)
        json_resume = {"basics": {"name": "Anna JSON", "label": "Dev", "email": "anna@taskflow.demo",
                                  "profiles": [{"network": "GitHub", "url": "https://github.com/anna"}]},
                       "work": [{"name": "Acme", "position": "Dev", "startDate": "2020", "highlights": ["Did X"]}],
                       "skills": [{"name": "Web", "keywords": ["HTML", "CSS"]}],
                       "languages": [{"language": "Russian", "fluency": "Native"}]}
        self.post("/resumes/import", {"file": (io.BytesIO(json.dumps(json_resume).encode()), "resume.json")},
                  content_type="multipart/form-data")
        imported = [r for r in self.store.resumes_of(self.anna) if r["content"]["name"] == "Anna JSON"][0]
        kinds = [s["type"] for s in imported["content"]["sections"]]
        self.assertEqual(kinds, ["experience", "skills", "languages"])
        self.post("/resumes/import", {"text": "{broken"})
        self.assertEqual(len(self.store.resumes_of(self.anna)), 5)

    def test_at_most_ten_resumes(self):
        for _ in range(12):
            self.post("/resumes/new", {"title": "Many"})
        self.assertEqual(len(self.store.resumes_of(self.anna)), 10)

    def test_html_in_resume_is_escaped(self):
        rid = self.make()
        content = self.store.resume(rid)["content"]
        content["summary"] = "<script>alert(1)</script>"
        self.post_json(f"/resumes/{rid}/save", self.payload(rid, content=content))
        html = self.html(f"/r/{self.store.resume(rid)['slug']}")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_explore_search(self):
        guest = self.new_client()
        html = guest.get("/explore?q=pandas").get_data(as_text=True)
        self.assertIn("Li Wei", html)
        self.assertNotIn("Fatima Haddad", html)

    def test_site_admin_can_remove_a_public_resume(self):
        admin = self.new_client("admin@taskflow.demo", "Admin@1234")
        rid = self.store.resume_by_slug("fatima-haddad")["id"]
        self.post(f"/resumes/{rid}/delete", client=admin)
        self.assertIsNone(self.store.resume(rid))

    def test_rtl_resume(self):
        html = self.new_client().get("/r/fatima-haddad").get_data(as_text=True)
        self.assertIn('dir="rtl"', html)
        self.assertIn('translate="no"', html)


class TestLanguages(Base):
    def test_language_switch_and_rtl(self):
        self.login()
        self.c.get("/lang/ar")
        html = self.html("/")
        self.assertIn('<html lang="ar" dir="rtl">', html)
        self.assertEqual(self.store.user(self.uid("admin@taskflow.demo"))["lang"], "ar")
        self.c.get("/lang/zh")
        self.assertIn('<html lang="zh">', self.html("/board"))
        self.assertEqual(self.c.get("/lang/xx").status_code, 302)

    def test_translated_text_does_not_stick_to_the_template(self):
        """Jinja may compute a filter on a constant once and keep the result: the language must still change."""
        self.login()
        self.c.get("/lang/ar")
        self.assertIn("المهام المفتوحة", self.html("/"))
        self.c.get("/lang/en")
        html = self.html("/")
        self.assertIn("Open tasks", html)
        self.assertNotIn("المهام المفتوحة", html)
        self.c.get("/lang/ru")
        self.assertIn("Открытые задачи", self.html("/"))

    def test_catalogs_translate_the_main_pages(self):
        self.login()
        for lang, word in [("ru", "Главная"), ("fr", "Tableau de bord"), ("es", "Panel"), ("ar", "لوحة التحكم"),
                           ("zh", "仪表板"), ("rw", "Ahabanza")]:
            self.c.get("/lang/" + lang)
            self.assertIn(word, self.html("/board"), lang)

    def test_resume_headings_in_the_resume_language(self):
        html = self.html("/r/jean-michel-ru")
        self.assertIn("Опыт работы", html)
        self.assertIn("Образование", html)
        self.assertIn("Results-driven Software Engineer", html)            # the content itself is not translated

    def test_notifications_in_the_language_of_the_person(self):
        fatima = self.uid("fatima@taskflow.demo")
        admin = self.uid("admin@taskflow.demo")
        self.store.create_task(admin, self.second_ws(), {"title": "Prepare slides", "status": "todo",
                                                         "priority": "low", "assignee_id": str(fatima)})
        text = self.store.notifications(fatima)[0]["text"]
        self.assertIn("Prepare slides", text)
        self.assertIn("أسند", text)

    def test_hours_in_every_language(self):
        from core import fmt_minutes
        self.assertEqual(fmt_minutes(95, "ru"), "1 ч 35 мин")
        self.assertEqual(fmt_minutes(60, "zh"), "1小时")
        self.assertEqual(fmt_minutes(20, "en"), "20m")

    def test_browser_language_for_visitors(self):
        html = self.c.get("/login", headers={"Accept-Language": "fr-FR,fr;q=0.9"}).get_data(as_text=True)
        self.assertIn('<html lang="fr">', html)

    def test_every_page_in_every_language(self):
        self.login()
        tid = self.store.tasks(self.first_ws())[0]["id"]
        for lang in ["ru", "fr", "es", "ar", "zh", "rw"]:
            self.c.get("/lang/" + lang)
            for url in ["/", "/board", f"/tasks/{tid}", "/reports", "/team", "/profile", "/resumes",
                        "/r/jean-michel-mujyambere", "/explore", "/calendar", "/notifications"]:
                self.assertEqual(self.c.get(url).status_code, 200, (lang, url))


if __name__ == "__main__":
    unittest.main()
