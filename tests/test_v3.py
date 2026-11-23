"""Tests of TaskFlow 3: sprints, Gantt, dependencies, repeating tasks, labels, trash, search, estimates,
integrations (iCal, Slack, Telegram, Web Push, outbox) and the site administrator back office."""

import base64
import gzip
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta

from support import PG_URL, Base, FakeNotifier

ADMIN = "admin@taskflow.demo"
TODAY = date.today()


def day(n):
    return (TODAY + timedelta(days=n)).isoformat()


class FakeResponse:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHttp:
    """Catches outgoing HTTP requests (Slack, Telegram, push services)."""
    def __init__(self):
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append((req.full_url, req.data))
        return FakeResponse()


class V3(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.ws = self.ws_of(ADMIN)[0]["id"]
        self.me = self.uid(ADMIN)

    def task(self, title="Write the v3 notes", **kw):
        f = {"title": title, "priority": "medium", "status": "todo", "due_date": day(5)}
        f.update(kw)
        return self.store.create_task(self.me, self.ws, f)


# ====================================================================== planning
class TestSprints(V3):
    def test_sprints_page_and_demo_sprints(self):
        html = self.html("/sprints")
        self.assertIn("Sprint 2", html)
        self.assertIn("Velocity", html)
        states = {s["status"] for s in self.store.sprints(self.ws)}
        self.assertEqual(states, {"active", "closed"})

    def test_create_sprint_and_add_tasks(self):
        r = self.post("/sprints", {"name": "Sprint 3", "goal": "Ship", "start_date": day(8), "end_date": day(21)})
        self.assertEqual(r.status_code, 302)
        sid = next(s["id"] for s in self.store.sprints(self.ws) if s["name"] == "Sprint 3")
        tid = self.task()
        self.post(f"/sprints/{sid}", {"action": "add_tasks", "task": str(tid)})
        self.assertEqual(self.store.task(tid)["sprint_id"], sid)
        self.assertIn("Write the v3 notes", self.html(f"/sprints/{sid}"))

    def test_sprint_rules(self):
        for bad in ({"name": "X", "start_date": day(1), "end_date": day(5)},
                    {"name": "Too long", "start_date": day(1), "end_date": day(80)},
                    {"name": "Backwards", "start_date": day(5), "end_date": day(1)}):
            self.post("/sprints", bad)
        self.assertEqual(len(self.store.sprints(self.ws)), 2)

    def test_only_one_active_sprint(self):
        sid = self.store.save_sprint(self.me, self.ws, {"name": "Next", "start_date": day(8), "end_date": day(20)})
        from core import TaskError
        with self.assertRaises(TaskError):
            self.store.set_sprint_state(self.me, self.ws, sid, "active")

    def test_close_sprint_moves_open_tasks(self):
        active = next(s for s in self.store.sprints(self.ws) if s["status"] == "active")
        nxt = self.store.save_sprint(self.me, self.ws, {"name": "Next", "start_date": day(8), "end_date": day(20)})
        open_before = [t for t in self.store.sprint_tasks(self.ws, active["id"]) if t["status"] != "done"]
        self.post(f"/sprints/{active['id']}", {"action": "closed", "move_to": str(nxt)})
        self.assertEqual(self.store.sprint(active["id"], self.ws)["status"], "closed")
        self.assertEqual(len(self.store.sprint_tasks(self.ws, nxt)), len(open_before))

    def test_burndown_and_velocity(self):
        active = next(s for s in self.store.sprints(self.ws) if s["status"] == "active")
        bd = self.store.sprint_burndown(self.ws, active["id"])
        self.assertEqual(bd["points"][0]["day"].isoformat(), active["start_date"])
        self.assertEqual(bd["points"][-1]["ideal"], 0)
        self.assertIn("polyline", self.html(f"/sprints/{active['id']}?metric=hours"))
        v = self.store.velocity(self.ws)
        self.assertEqual(len(v["sprints"]), 2)
        self.assertIsNotNone(v["average"])

    def test_member_cannot_change_sprints(self):
        c = self.new_client("anna@taskflow.demo")
        r = self.post("/sprints", {"name": "Mine", "start_date": day(1), "end_date": day(5)}, client=c)
        self.assertEqual(r.status_code, 403)

    def test_delete_sprint_keeps_tasks(self):
        active = next(s for s in self.store.sprints(self.ws) if s["status"] == "active")
        ids = [t["id"] for t in self.store.sprint_tasks(self.ws, active["id"])]
        self.post(f"/sprints/{active['id']}", {"action": "delete"})
        self.assertIsNone(self.store.sprint(active["id"], self.ws))
        self.assertTrue(all(self.store.task(i)["sprint_id"] is None for i in ids))


class TestDependencies(V3):
    def test_wait_and_block(self):
        a, b = self.task("First"), self.task("Second")
        self.post(f"/tasks/{b}/deps", {"blocked_by": str(a)})
        self.assertEqual([x["id"] for x in self.store.blockers(b)], [a])
        self.post(f"/tasks/{b}/status", {"status": "done"})
        self.assertEqual(self.store.task(b)["status"], "todo")          # still waits for "First"
        self.store.set_status(self.me, self.ws, a, "done")
        self.store.set_status(self.me, self.ws, b, "done")
        self.assertEqual(self.store.task(b)["status"], "done")

    def test_no_circles(self):
        from core import TaskError
        a, b, c = self.task("Task A"), self.task("Task B"), self.task("Task C")
        self.store.add_dependency(self.me, self.ws, b, a)
        self.store.add_dependency(self.me, self.ws, c, b)
        with self.assertRaises(TaskError):
            self.store.add_dependency(self.me, self.ws, a, c)
        with self.assertRaises(TaskError):
            self.store.add_dependency(self.me, self.ws, a, a)

    def test_remove_link(self):
        a, b = self.task("Task A"), self.task("Task B")
        self.store.add_dependency(self.me, self.ws, b, a)
        self.post(f"/tasks/{b}/deps", {"action": "remove", "blocked_by": str(a)})
        self.assertEqual(self.store.blockers(b), [])

    def test_bulk_done_skips_blocked(self):
        a, b = self.task("Task A"), self.task("Task B")
        self.store.add_dependency(self.me, self.ws, b, a)
        from core import TaskError
        with self.assertRaises(TaskError):
            self.store.bulk_update(self.me, self.ws, [b], "status", "done")
        self.assertEqual(self.store.task(b)["status"], "todo")
        self.store.bulk_update(self.me, self.ws, [a, b], "status", "done")     # the free one is done, b still waits
        self.assertEqual((self.store.task(a)["status"], self.store.task(b)["status"]), ("done", "todo"))


class TestRepeatAndGantt(V3):
    def test_repeating_task_makes_the_next_one(self):
        tid = self.task("Weekly report", repeat="weekly", due_date=day(1))
        self.store.save_label(self.me, self.ws, "report", "#4f46e5")
        self.store.set_status(self.me, self.ws, tid, "done")
        nxt = [t for t in self.store.tasks(self.ws) if t["title"] == "Weekly report" and t["status"] != "done"]
        self.assertEqual(len(nxt), 1)
        self.assertEqual(nxt[0]["due_date"], day(8))
        self.assertEqual(nxt[0]["repeat"], "weekly")

    def test_repeat_needs_due_date(self):
        from core import TaskError
        with self.assertRaises(TaskError):
            self.task("No date", repeat="daily", due_date="")

    def test_monthly_end_of_month(self):
        from planning import next_due
        self.assertEqual(next_due("2026-01-31", "monthly"), "2026-02-28")
        self.assertEqual(next_due("2028-01-31", "monthly"), "2028-02-29")
        self.assertEqual(next_due("2026-12-15", "monthly"), "2027-01-15")
        self.assertEqual(next_due("2026-03-10", "biweekly"), "2026-03-24")

    def test_start_after_due_is_refused(self):
        from core import TaskError
        with self.assertRaises(TaskError):
            self.task("Bad dates", start_date=day(9), due_date=day(2))

    def test_gantt(self):
        html = self.html("/gantt?days=28")
        self.assertIn("<svg class=\"gantt\"", html)
        g = self.store.gantt(self.ws, days=42)
        self.assertTrue(g["bars"])
        self.assertTrue(g["arrows"])                        # demo data has "waits for" links
        svg = self.store.gantt_svg(g)
        self.assertEqual(len(svg["arrows"]), len(g["arrows"]))
        self.assertEqual(self.c.get("/gantt?start=not-a-date").status_code, 200)


class TestLabelsSearchEstimate(V3):
    def test_labels_on_tasks_and_filter(self):
        lid = self.store.save_label(self.me, self.ws, "urgent-fix", "#ef4444")
        r = self.post("/tasks/new", {"title": "Fix login", "priority": "high", "status": "todo", "labels_sent": "1",
                                     "labels": str(lid)})
        self.assertEqual(r.status_code, 302)
        tid = next(t["id"] for t in self.store.tasks(self.ws) if t["title"] == "Fix login")
        self.assertEqual([x["name"] for x in self.store.task_labels([tid])[tid]], ["urgent-fix"])
        html = self.html(f"/tasks?label={lid}")
        self.assertIn("Fix login", html)
        self.assertNotIn("Dark mode", html)

    def test_label_rules(self):
        self.post("/labels", {"name": "bug", "color": "#000000"})               # already exists in the demo
        self.assertEqual(sum(1 for x in self.store.labels(self.ws) if x["name"] == "bug"), 1)
        c = self.new_client("anna@taskflow.demo")
        lid = self.store.labels(self.ws)[0]["id"]
        self.assertEqual(self.post("/labels", {"action": "delete", "id": str(lid)}, client=c).status_code, 403)
        self.post("/labels", {"action": "delete", "id": str(lid)})
        self.assertNotIn(lid, [x["id"] for x in self.store.labels(self.ws)])

    def test_search(self):
        html = self.html("/search?q=deploy")
        self.assertIn("Results found", html)
        res = self.store.search(self.ws, "anna")
        self.assertTrue(res["people"])
        self.assertIsNone(self.store.search(self.ws, "a"))

    def test_suggested_estimate(self):
        s = self.store.suggest_estimate(self.ws, "Write automated tests for the bank")
        self.assertIsNotNone(s)
        self.assertEqual(s["minutes"] % 5, 0)
        d = self.c.get("/api/estimate?title=Write+tests").get_json()
        self.assertTrue(d["ok"])
        self.assertFalse(self.c.get("/api/estimate?title=zzqx").get_json()["ok"])


class TestTrash(V3):
    def test_delete_restore_purge(self):
        tid = self.task("Temporary")
        self.post(f"/tasks/{tid}/delete")
        self.assertIsNone(self.store.task(tid))
        self.assertEqual(self.c.get(f"/tasks/{tid}").status_code, 404)
        self.assertIn("Temporary", self.html("/trash"))
        self.assertNotIn("Temporary", self.html("/tasks"))
        self.post("/trash", {"action": "restore", "id": str(tid)})
        self.assertEqual(self.store.task(tid)["workspace_id"], self.ws)
        self.post(f"/tasks/{tid}/delete")
        self.post("/trash", {"action": "purge", "id": str(tid)})
        self.assertIsNone(self.store.task(tid, trashed=True))

    def test_member_restores_only_own(self):
        tid = self.task("Admin task")
        self.store.delete_task(self.me, self.ws, tid)
        c = self.new_client("anna@taskflow.demo")
        self.post("/trash", {"action": "restore", "id": str(tid)}, client=c)
        self.assertIsNone(self.store.task(tid))

    def test_old_trash_is_emptied(self):
        tid = self.task("Old")
        self.store.delete_task(self.me, self.ws, tid)
        with self.store.db() as con:
            con.execute("UPDATE tasks SET deleted_at = ? WHERE id = ?", (day(-40) + " 10:00:00", tid))
        self.store.empty_old_trash()
        self.assertIsNone(self.store.task(tid, trashed=True))


# ====================================================================== data in and out
class TestImportExport(V3):
    def test_csv_import(self):
        csv_text = ("title,status,priority,due_date,assignee,project,labels,estimate\n"
                    "Imported one,in_progress,high,%s,anna@taskflow.demo,New CSV project,csv;bug,2h\n"
                    "Imported two,,,,,,,\n,missing title,,,,,,\n" % day(3))
        r = self.post("/workspace/import", {"file": (io.BytesIO(csv_text.encode()), "t.csv")},
                      content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        titles = {t["title"]: t for t in self.store.tasks(self.ws)}
        self.assertIn("Imported one", titles)
        self.assertIn("Imported two", titles)
        one = titles["Imported one"]
        self.assertEqual(one["estimate_minutes"], 120)
        self.assertEqual(one["assignee_name"], "Anna Petrova")
        self.assertIn("New CSV project", [p["name"] for p in self.store.projects(self.ws)])

    def test_export_workspace(self):
        r = self.c.get("/workspace/export.json")
        data = json.loads(r.data)
        self.assertTrue(data["tasks"])
        self.assertIn("sprints", data)
        self.assertNotIn("password_hash", r.get_data(as_text=True))

    def test_my_data(self):
        data = json.loads(self.c.get("/profile/data.json").data)
        self.assertEqual(data["account"]["email"], ADMIN)
        self.assertNotIn("password_hash", data["account"])


class TestIntegrations(V3):
    def test_ical_feed(self):
        tok = self.store.ical_token(self.me)
        r = self.c.get(f"/ical/{tok}.ics")
        body = r.get_data(as_text=True)
        self.assertEqual(r.mimetype, "text/calendar")
        self.assertTrue(body.startswith("BEGIN:VCALENDAR"))
        self.assertIn("BEGIN:VEVENT", body)
        self.assertTrue(all(len(line.encode()) <= 75 for line in body.split("\r\n")))
        self.assertEqual(self.app.test_client().get("/ical/wrong.ics").status_code, 404)
        new = self.store.ical_token(self.me, renew=True)
        self.assertNotEqual(new, tok)
        self.assertEqual(self.c.get(f"/ical/{tok}.ics").status_code, 404)

    def test_slack_and_telegram(self):
        self.store.http = FakeHttp()
        self.store.env["TELEGRAM_BOT_TOKEN"] = "123:abc"
        r = self.post("/workspace/settings", {"action": "chat", "slack_url": "https://hooks.slack.com/services/T0001/B0001/abcdefghijklmnop",
                                               "telegram_chat": "-1001234567890"})
        self.assertEqual(r.status_code, 302)
        self.task("Chat me")
        urls = [u for u, _ in self.store.http.calls]
        self.assertTrue(any(u.startswith("https://hooks.slack.com/") for u in urls))
        self.assertTrue(any("api.telegram.org" in u for u in urls))
        self.assertTrue(all(o["status"] == "sent" for o in self.store.outbox(10) if o["channel"] in ("slack", "telegram")))

    def test_bad_chat_settings(self):
        self.post("/workspace/settings", {"action": "chat", "slack_url": "https://evil.example/x"})
        self.assertFalse(self.store.workspace(self.ws)["slack_url"])

    def test_push_subscription_and_send(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = self.c.get("/push/key").get_json()["key"]
        self.assertTrue(key)
        browser = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")      # noqa: E731
        sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": b64(browser), "auth": b64(os.urandom(16))}}
        self.assertTrue(self.post_json("/push/subscribe", sub).get_json()["ok"])
        self.assertEqual(self.store.push_count(self.me), 1)
        self.store.http = FakeHttp()
        self.post("/push/test")
        self.assertEqual(self.store.http.calls[0][0], "https://push.example/abc")
        self.assertEqual([o["status"] for o in self.store.outbox(5, "push")], ["sent"])
        self.post_json("/push/unsubscribe", {"endpoint": sub["endpoint"]})
        self.assertEqual(self.store.push_count(self.me), 0)
        self.assertEqual(self.post_json("/push/subscribe", {"endpoint": "http://x"}).status_code, 400)

    def test_manifest_and_service_worker(self):
        guest = self.app.test_client()
        m = guest.get("/manifest.webmanifest").get_json()
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(guest.get("/sw.js").headers["Service-Worker-Allowed"], "/")
        self.assertEqual(guest.get("/brand/logo.png").mimetype, "image/png")

    def test_outbox_and_test_email(self):
        self.post("/admin/outbox", {"to": "someone@taskflow.demo"})
        self.assertEqual(self.store.outbox(1)[0]["status"], "not sent: demo or test address")
        self.post("/admin/outbox", {"to": "real.person@mail.io"})
        self.assertEqual(self.store.outbox(1)[0]["status"], "sent")
        self.assertIn("real.person@mail.io", self.html("/admin/outbox"))


# ====================================================================== site administrator
class TestSiteSettings(V3):
    def test_save_settings(self):
        r = self.post("/admin/site", {"site_name": "Team Board", "accent": "#0f766e", "announcement": "Hello team",
                                      "default_lang": "en", "langs_enabled": ["en", "fr", "ru"], "max_file_mb": "8",
                                      "trash_days": "10", "allow_registration": "1"})
        self.assertEqual(r.status_code, 302)
        html = self.html("/")
        self.assertIn("Team Board", html)
        self.assertIn("Hello team", html)
        self.assertIn("#0f766e", html)
        self.assertEqual(self.store.site_int("max_file_mb"), 8)
        self.assertEqual(set(self.store.site("langs_enabled").split(",")), {"en", "fr", "ru"})

    def test_bad_values(self):
        for bad in ({"max_file_mb": "500"}, {"accent": "red"}, {"site_name": "x"}, {"default_lang": "de"},
                    {"default_lang": "zh", "langs_enabled": ["en", "fr"]}):
            from core import TaskError
            with self.assertRaises(TaskError):
                self.store.save_site(self.me, bad)

    def test_switched_off_language(self):
        self.store.save_site(self.me, {"langs_enabled": "en,fr"})
        self.c.get("/lang/ru")
        self.assertNotIn('lang="ru"', self.html("/"))
        self.c.get("/lang/fr")
        self.assertIn('lang="fr"', self.html("/"))

    def test_registration_off(self):
        self.store.save_site(self.me, {"allow_registration": ""})
        guest = self.app.test_client()
        self.post("/register", {"name": "New Person", "email": "new@teamtest.io", "password": "Secret123",
                                "confirm": "Secret123"}, client=guest)
        self.assertIsNone(self.store.user_by_email("new@teamtest.io"))

    def test_maintenance_mode(self):
        self.store.save_site(self.me, {"maintenance": "1", "maintenance_text": "Back at noon"})
        c = self.new_client("anna@taskflow.demo")
        r = c.get("/")
        self.assertEqual(r.status_code, 503)
        self.assertIn("Back at noon", r.get_data(as_text=True))
        self.assertEqual(self.c.get("/").status_code, 200)                      # the site admin still works
        self.assertEqual(self.app.test_client().get("/login").status_code, 200)

    def test_file_limit_setting(self):
        self.store.save_site(self.me, {"max_files": "1"})
        tid = self.task()
        for n in range(2):
            self.post(f"/tasks/{tid}/files", {"file": (io.BytesIO(b"hello"), f"n{n}.txt")},
                      content_type="multipart/form-data")
        self.assertEqual(len(self.store.attachments(tid)), 1)

    def test_member_cannot_open_back_office(self):
        c = self.new_client("anna@taskflow.demo")
        for url in ("/admin/site", "/admin/users", "/admin/workspaces", "/admin/translations", "/admin/audit"):
            self.assertIn(c.get(url).status_code, (302, 403), url)


class TestAccounts(V3):
    def test_pages(self):
        for url in ("/admin/users", "/admin/users?q=anna", f"/admin/users/{self.me}", "/admin/workspaces",
                    f"/admin/workspaces/{self.ws}", "/admin/resumes", "/admin/translations?lang=ar",
                    "/admin/audit?q=task", "/admin"):
            self.html(url)

    def test_create_change_and_delete_account(self):
        self.post("/admin/users", {"name": "Grace Mutoni", "email": "grace@teamtest.io", "password": "Secret123",
                                   "workspace": str(self.ws), "ws_role": "member"})
        g = self.store.user_by_email("grace@teamtest.io")
        self.assertIsNotNone(g)
        self.assertEqual([w["id"] for w in self.store.memberships(g["id"])], [self.ws])
        self.post(f"/admin/users/{g['id']}", {"action": "details", "name": "Grace M.", "email": "grace2@teamtest.io",
                                              "phone": "+250 788 000 000", "bio": "Tester", "lang": "rw",
                                              "job_title": "QA"})
        g = self.store.user(g["id"])
        self.assertEqual((g["name"], g["email"], g["lang"], g["bio"]), ("Grace M.", "grace2@teamtest.io", "rw", "Tester"))
        self.post(f"/admin/users/{g['id']}", {"action": "password", "password": "NewPass123", "password2": "NewPass123"})
        self.assertEqual(self.login("grace2@teamtest.io", "NewPass123", client=self.app.test_client()).status_code, 302)
        self.post(f"/admin/users/{g['id']}", {"action": "delete", "confirm": "wrong@x.io"})
        self.assertIsNotNone(self.store.user(g["id"]))
        self.post(f"/admin/users/{g['id']}", {"action": "delete", "confirm": "grace2@teamtest.io"})
        self.assertIsNone(self.store.user(g["id"]))

    def test_site_admin_rights(self):
        anna = self.uid("anna@taskflow.demo")
        self.post(f"/admin/users/{anna}", {"action": "site_admin", "on": "1"})
        self.assertEqual(self.store.user(anna)["role"], "admin")
        self.post(f"/admin/users/{anna}", {"action": "site_admin", "on": "0"})
        self.assertNotEqual(self.store.user(anna)["role"], "admin")
        self.post(f"/admin/users/{self.me}", {"action": "site_admin", "on": "0"})   # the last one keeps the right
        self.assertEqual(self.store.user(self.me)["role"], "admin")

    def test_sign_out_everywhere(self):
        c = self.new_client("anna@taskflow.demo")
        self.assertEqual(c.get("/").status_code, 200)
        self.post(f"/admin/users/{self.uid('anna@taskflow.demo')}", {"action": "sign_out"})
        self.assertEqual(c.get("/").status_code, 302)

    def test_admin_changes_own_details_and_photo(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (300, 200), "#4f46e5").save(buf, "PNG")
        self.post("/profile", {"form": "photo", "photo": (io.BytesIO(buf.getvalue()), "me.png")},
                  content_type="multipart/form-data")
        r = self.c.get(f"/admin/users/{self.me}/photo.jpg")
        self.assertEqual(r.mimetype, "image/jpeg")
        self.assertEqual(Image.open(io.BytesIO(r.data)).size[0], Image.open(io.BytesIO(r.data)).size[1])
        self.post("/profile", {"form": "profile", "name": "Jean Michel M.", "email": ADMIN, "phone": "+7 900 000 00 00",
                               "bio": "Builder", "job_title": "Lead", "lang": "en", "color": "#10b981"})
        u = self.store.user(self.me)
        self.assertEqual((u["name"], u["phone"], u["bio"], u["color"]), ("Jean Michel M.", "+7 900 000 00 00", "Builder",
                                                                       "#10b981"))

    def test_email_change_needs_password(self):
        self.post("/profile", {"form": "profile", "name": "Jean", "email": "new.admin@teamtest.io"})
        self.assertEqual(self.store.user(self.me)["email"], ADMIN)
        self.post("/profile", {"form": "profile", "name": "Jean", "email": "new.admin@teamtest.io",
                               "password": "Admin@1234"})
        self.assertEqual(self.store.user(self.me)["email"], "new.admin@teamtest.io")

    def test_own_sign_out_everywhere(self):
        other = self.new_client(ADMIN, "Admin@1234")
        self.post("/profile", {"form": "sign_out_all"})
        self.assertEqual(other.get("/").status_code, 302)

    def test_bad_phone(self):
        self.post("/profile", {"form": "profile", "name": "Jean", "email": ADMIN, "phone": "call me"})
        self.assertFalse(self.store.user(self.me)["phone"])


class TestWorkspacesAndResumes(V3):
    def test_manage_workspace(self):
        sfu = self.ws_of(ADMIN)[1]["id"]
        anna = self.uid("anna@taskflow.demo")
        self.post(f"/admin/workspaces/{sfu}", {"action": "add", "email": "anna@taskflow.demo", "role": "admin"})
        self.assertIn(sfu, [w["id"] for w in self.store.memberships(anna)])
        self.post(f"/admin/workspaces/{sfu}", {"action": "remove", "user": str(anna)})
        self.assertNotIn(sfu, [w["id"] for w in self.store.memberships(anna)])
        self.post(f"/admin/workspaces/{sfu}", {"action": "details", "name": "SFU Group", "description": "",
                                               "color": "#10b981", "default_priority": "high"})
        self.assertEqual(self.store.workspace(sfu)["name"], "SFU Group")
        self.post(f"/admin/workspaces/{sfu}", {"action": "delete", "confirm": "SFU Group"})
        self.assertIsNone(self.store.workspace(sfu))

    def test_site_admin_opens_any_workspace(self):
        anna = self.uid("anna@taskflow.demo")
        c = self.new_client("anna@taskflow.demo")
        r = self.post("/workspaces", {"name": "Anna private", "description": "", "color": "#4f46e5"}, client=c)
        self.assertEqual(r.status_code, 302)
        ws = next(w["id"] for w in self.store.memberships(anna) if w["name"] == "Anna private")
        self.post(f"/admin/workspaces/{ws}", {"action": "open"})
        self.assertIn("Anna private", self.html("/"))

    def test_workspace_limit(self):
        self.store.save_site(self.me, {"max_workspaces": "1"})
        c = self.new_client("anna@taskflow.demo")
        self.post("/workspaces", {"name": "One", "color": "#4f46e5"}, client=c)
        self.post("/workspaces", {"name": "Two", "color": "#4f46e5"}, client=c)
        names = [w["name"] for w in self.store.memberships(self.uid("anna@taskflow.demo"))]
        self.assertIn("One", names)
        self.assertNotIn("Two", names)

    def test_resume_visibility_and_pdf(self):
        r = self.store.resumes_admin()[0]
        self.post("/admin/resumes", {"id": str(r["id"]), "visibility": "private"})
        self.assertEqual(self.app.test_client().get(f"/r/{r['slug']}").status_code, 404)
        pdf = self.c.get(f"/r/{r['slug']}/resume.pdf")
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))

    def test_resume_pdf_all_themes_and_scripts(self):
        import resume_pdf
        import resumes
        for theme in resumes.THEMES:
            for slug in ("jean-michel-mujyambere", "fatima-haddad", "li-wei"):
                r = self.store.resume_by_slug(slug)
                if not r:
                    continue
                r = dict(r, theme=theme)
                self.assertTrue(resume_pdf.make_pdf(r, None, tr=lambda s: s).startswith(b"%PDF"), (theme, slug))


class TestTranslationsAuditRestore(V3):
    def test_own_translation_wins(self):
        self.post("/admin/translations", {"lang": "ru", "english": "Dashboard", "text": "Пульт"})
        self.c.get("/lang/ru")
        self.assertIn("Пульт", self.html("/"))
        self.post("/admin/translations", {"lang": "ru", "english": "Dashboard", "text": ""})
        self.assertNotIn("Пульт", self.html("/"))

    def test_translation_keeps_gaps(self):
        from core import TaskError
        with self.assertRaises(TaskError):
            self.store.save_ui_override(self.me, "fr", "{0} tasks", "tâches")

    def test_audit_log(self):
        self.store.save_site(self.me, {"site_name": "Audit Me"})
        rows = self.store.audit("setting_changed")
        self.assertTrue(rows)
        self.assertIn("setting_changed", self.html("/admin/audit?q=setting"))

    def test_backup_and_restore(self):
        blob = self.c.get("/admin/backup.json.gz").data
        self.task("Made after the backup")
        r = self.post("/admin/restore", {"confirm": "RESTORE", "file": (io.BytesIO(blob), "b.json.gz")},
                      content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        titles = [t["title"] for t in self.store.tasks(self.ws)]
        self.assertNotIn("Made after the backup", titles)
        self.assertTrue(titles)
        self.assertEqual(self.login().status_code, 302)

    def test_restore_needs_confirm_and_good_file(self):
        self.post("/admin/restore", {"confirm": "no", "file": (io.BytesIO(b"{}"), "b.json")},
                  content_type="multipart/form-data")
        self.assertTrue(self.store.tasks(self.ws))
        from core import TaskError
        with self.assertRaises(TaskError):
            self.store.restore_backup(self.me, gzip.compress(b'{"hello": 1}'))


@unittest.skipIf(PG_URL, "the old-version file test is for SQLite")
class TestUpgradeFromVersion2(unittest.TestCase):
    def test_v2_database_gets_new_columns(self):
        """A database made by TaskFlow 2 (tests/fixtures/taskflow_v2.sql) opens in version 3 without losing data."""
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        con = sqlite3.connect(path)
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "taskflow_v2.sql"), encoding="utf-8") as f:
            con.executescript(f.read())
        con.close()
        from store import Store
        s = Store(path, notifier=FakeNotifier(), env={})
        with s.db() as c:
            cols = c.columns("tasks")
            for col in ("start_date", "sprint_id", "repeat", "deleted_at"):
                self.assertIn(col, cols)
            self.assertIn("phone", c.columns("users"))
            self.assertIn("slack_url", c.columns("workspaces"))
        self.assertIsNotNone(s.user_by_email("old@teamtest.io"))
        self.assertEqual(s.site("site_name"), "TaskFlow")


if __name__ == "__main__":
    unittest.main()
