"""Tests of TaskFlow 4: board columns and WIP limits, custom fields, automations, saved views, templates, REST API and
keys, webhooks and outbox retries, weekly digest, calendar import, better estimate, S3 storage, translation
suggestions, sign-in limits, single sign-on, security headers, compression, dark mode, live bus, offline page."""

import gzip
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import tempfile
import time
import unittest
import urllib.parse
from datetime import date, timedelta

from support import PG_URL, Base, FakeNotifier

ADMIN = "admin@taskflow.demo"
TODAY = date.today()
PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 443))]
PRIVATE = [(2, 1, 6, "", ("10.0.0.7", 443))]


def day(n):
    return (TODAY + timedelta(days=n)).isoformat()


class Resp:
    def __init__(self, status=200, body=b""):
        self.status, self.body = status, body

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeWeb:
    """Catches every outgoing request. routes: list of (url start, status, body)."""
    def __init__(self, routes=()):
        self.routes, self.calls = list(routes), []

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        for start, status, body in self.routes:
            if req.full_url.startswith(start):
                if status >= 300:
                    raise OSError(f"answered {status}")
                return Resp(status, body() if callable(body) else body)
        return Resp(200, b"{}")


class V4(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.ws = self.first_ws()
        self.me = self.uid(ADMIN)
        self.store.resolver = lambda host, port: PUBLIC
        self.web = FakeWeb()
        self.store.http = self.web

    def task(self, title="Write the v4 notes", **kw):
        f = {"title": title, "priority": "medium", "status": "todo", "due_date": day(5)}
        f.update(kw)
        return self.store.create_task(self.me, self.ws, f)

    def open_ws(self, ws):
        self.post(f"/workspaces/{ws}/open")


# ====================================================================== board columns
class TestColumns(V4):
    def test_demo_board_has_own_columns(self):
        names = [c["name"] for c in self.store.columns(self.ws)]
        self.assertIn("Testing", names)
        self.assertIn("Code review", names)
        html = self.html("/board")
        self.assertIn("Testing", html)
        self.assertIn('data-wip="3"', html)

    def test_start_columns_in_another_workspace(self):
        ws2 = self.second_ws()
        self.open_ws(ws2)
        self.assertEqual(self.store.columns(ws2), [])
        self.post("/workspace/tools/board", {"action": "start"})
        self.assertEqual([c["base_status"] for c in self.store.columns(ws2)], ["todo", "in_progress", "review", "done"])
        self.post("/workspace/tools/board", {"action": "save", "name": "Backlog", "base_status": "todo", "wip_limit": "0"})
        self.assertIn("Backlog", [c["name"] for c in self.store.columns(ws2)])
        self.post("/workspace/tools/board", {"action": "stop"})
        self.assertEqual(self.store.columns(ws2), [])

    def test_move_card_to_custom_column(self):
        testing = next(c for c in self.store.columns(self.ws) if c["name"] == "Testing")
        tid = self.task()
        r = self.post_json(f"/api/tasks/{tid}/status", {"status": "review", "column": testing["id"]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["label"], "Testing")
        t = self.store.task(tid)
        self.assertEqual((t["status"], t["column_id"]), ("review", testing["id"]))

    def test_wip_limit_stops_a_full_column(self):
        col = self.store.save_column(self.me, self.ws, "Tiny", "in_progress", 1)
        a, b = self.task("First task here"), self.task("Second task here")
        self.store.move_to_column(self.me, self.ws, a, col)
        r = self.post_json(f"/api/tasks/{b}/status", {"status": "in_progress", "column": col})
        self.assertEqual(r.status_code, 400)
        self.assertIn("WIP limit 1", r.get_json()["error"])
        self.assertEqual(self.store.task(b)["status"], "todo")

    def test_every_state_keeps_a_column(self):
        done = [c for c in self.store.columns(self.ws) if c["base_status"] == "done"]
        self.assertEqual(len(done), 1)
        self.post("/workspace/tools/board", {"action": "delete", "column_id": done[0]["id"]})
        self.assertEqual(len([c for c in self.store.columns(self.ws) if c["base_status"] == "done"]), 1)

    def test_task_whose_state_changed_moves_to_first_column_of_state(self):
        testing = next(c for c in self.store.columns(self.ws) if c["name"] == "Testing")
        tid = self.task()
        self.store.move_to_column(self.me, self.ws, tid, testing["id"])
        self.store.set_status(self.me, self.ws, tid, "done")
        placed = self.store.place_in_columns(self.store.columns(self.ws), [self.store.task(tid)])
        done_col = next(c for c in self.store.columns(self.ws) if c["base_status"] == "done")
        self.assertEqual([t["id"] for t in placed[done_col["id"]]], [tid])

    def test_member_cannot_change_columns(self):
        anna = self.new_client("anna@taskflow.demo")
        self.assertEqual(anna.get("/workspace/tools/board").status_code, 403)


# ====================================================================== custom fields
class TestCustomFields(V4):
    def test_create_task_with_fields(self):
        f = {x["name"]: x for x in self.store.fields(self.ws)}
        r = self.post("/tasks/new", {"title": "Task with fields", "status": "todo", "priority": "low",
                                     f"cf_{f['Story points']['id']}": "8", f"cf_{f['Area']['id']}": "Database",
                                     f"cf_{f['Customer approved']['id']}": "1"})
        self.assertEqual(r.status_code, 302)
        tid = int(r.headers["Location"].rsplit("/", 1)[1])
        vals = self.store.field_values([tid])[tid]
        self.assertEqual(vals[f["Story points"]["id"]], "8")
        self.assertEqual(vals[f["Area"]["id"]], "Database")
        html = self.html(f"/tasks/{tid}")
        self.assertIn("Story points", html)
        self.assertIn("Database", html)

    def test_wrong_values_are_refused(self):
        f = {x["name"]: x for x in self.store.fields(self.ws)}
        for bad in ({f"cf_{f['Story points']['id']}": "many"}, {f"cf_{f['Area']['id']}": "Kitchen"}):
            r = self.post("/tasks/new", dict({"title": "Should not exist", "status": "todo", "priority": "low"}, **bad))
            self.assertEqual(r.status_code, 200)
        self.assertFalse(any(t["title"] == "Should not exist" for t in self.store.tasks(self.ws)))

    def test_required_field(self):
        self.store.save_field(self.me, self.ws, "Ticket number", "text", required=True)
        r = self.post("/tasks/new", {"title": "No ticket", "status": "todo", "priority": "low"})
        self.assertIn("is required", r.get_data(as_text=True))

    def test_field_rules(self):
        from core import TaskError
        with self.assertRaises(TaskError):
            self.store.save_field(self.me, self.ws, "Size", "select", "only one")
        with self.assertRaises(TaskError):
            self.store.save_field(self.me, self.ws, "Area", "text")                 # same name
        fid = self.store.save_field(self.me, self.ws, "Due week", "date")
        self.store.delete_field(self.me, self.ws, fid)
        self.assertNotIn(fid, [x["id"] for x in self.store.fields(self.ws)])

    def test_edit_keeps_values(self):
        points = next(x for x in self.store.fields(self.ws) if x["name"] == "Story points")
        tid = self.task()
        self.store.set_field_values(self.ws, tid, {points["id"]: "2"})
        self.assertIn('value="2"', self.html(f"/tasks/{tid}/edit"))


# ====================================================================== automations
class TestAutomations(V4):
    def test_demo_rule_assigns_review_to_tester(self):
        elena = self.uid("elena@taskflow.demo")
        tid = self.task()
        self.store.set_status(self.me, self.ws, tid, "review")
        self.assertEqual(self.store.task(tid)["assignee_id"], elena)

    def test_rule_in_one_project_only(self):
        proj = self.store.projects(self.ws)
        label = self.store.labels(self.ws)[0]
        self.store.save_automation(self.me, self.ws, {"trigger_kind": "task_created", "project_id": str(proj[0]["id"]),
                                                      "action_kind": "label", "action_value": str(label["id"])})
        a = self.task("Inside the project", project_id=str(proj[0]["id"]))
        b = self.task("Outside the project", project_id=str(proj[1]["id"]))
        labels = self.store.task_labels([a, b])
        self.assertEqual([x["id"] for x in labels[a]], [label["id"]])
        self.assertEqual(labels[b], [])

    def test_priority_and_due_actions(self):
        self.store.save_automation(self.me, self.ws, {"trigger_kind": "commented", "action_kind": "priority",
                                                      "action_value": "urgent"})
        tid = self.task()
        self.store.add_comment(self.me, self.ws, tid, "Please hurry")
        self.assertEqual(self.store.task(tid)["priority"], "urgent")

    def test_notify_action_and_run_counter(self):
        rule = next(r for r in self.store.automations(self.ws) if r["name"] == "Finished work: tell Jean")
        before = len(self.store.notifications(self.me))
        anna = self.uid("anna@taskflow.demo")
        tid = self.store.create_task(anna, self.ws, {"title": "Anna finishes this", "status": "todo"})
        self.store.set_status(anna, self.ws, tid, "done")
        self.assertGreater(len(self.store.notifications(self.me)), before)
        again = next(r for r in self.store.automations(self.ws) if r["id"] == rule["id"])
        self.assertEqual(again["runs"], rule["runs"] + 1)

    def test_paused_rule_does_not_run(self):
        rule = next(r for r in self.store.automations(self.ws) if r["name"] == "New tasks are due in 2 days")
        self.assertEqual(rule["active"], 0)
        tid = self.task(due_date=day(20))
        self.assertEqual(self.store.task(tid)["due_date"], day(20))
        self.store.toggle_automation(self.me, self.ws, rule["id"])
        tid = self.task("Now the rule runs", due_date=day(20))
        self.assertEqual(self.store.task(tid)["due_date"], day(2))

    def test_no_endless_loops(self):
        from core import TaskError
        with self.assertRaises(TaskError):
            self.store.save_automation(self.me, self.ws, {"trigger_kind": "moved_to", "trigger_value": "review",
                                                          "action_kind": "status", "action_value": "done"})
        # a status rule on "created" runs once and does not start the "moved to" rules
        self.store.save_automation(self.me, self.ws, {"trigger_kind": "task_created", "action_kind": "status",
                                                      "action_value": "review"})
        tid = self.task()
        t = self.store.task(tid)
        self.assertEqual(t["status"], "review")
        self.assertEqual(t["assignee_id"], None)          # the Review -> Elena rule was not started by a rule

    def test_rules_page(self):
        r = self.post("/workspace/tools/automations", {"name": "Bad", "trigger_kind": "moved_to", "trigger_value": "x",
                                                       "action_kind": "assign", "action_value": "1"})
        self.assertEqual(r.status_code, 302)
        html = self.html("/workspace/tools/automations")
        self.assertIn("Testing goes to Elena (QA)", html)
        self.assertNotIn(">Bad<", html)


# ====================================================================== templates and saved views
class TestTemplatesAndViews(V4):
    def test_task_from_template_has_checklist(self):
        tpl = next(t for t in self.store.templates(self.ws) if t["name"] == "Release a new version")
        r = self.post(f"/templates/{tpl['id']}/use", {"due_date": day(3)})
        tid = int(r.headers["Location"].rsplit("/", 1)[1])
        t = self.store.task(tid)
        self.assertEqual((t["title"], t["priority"], t["estimate_minutes"]), (tpl["title"], "high", 180))
        self.assertEqual(len(self.store.subtasks(tid)), 5)

    def test_new_task_form_from_template(self):
        tpl = next(t for t in self.store.templates(self.ws) if t["name"] == "Bug report")
        html = self.html(f"/tasks/new?template={tpl['id']}")
        self.assertIn("Steps to repeat", html)

    def test_save_and_delete_template(self):
        self.post("/templates", {"name": "Onboarding", "title": "Welcome a new teammate", "priority": "low",
                                 "checklist": "Laptop\nAccounts\n\nFirst task"})
        tpl = next(t for t in self.store.templates(self.ws) if t["name"] == "Onboarding")
        self.assertEqual(tpl["steps"], ["Laptop", "Accounts", "First task"])
        anna = self.new_client("anna@taskflow.demo")
        self.assertEqual(self.post(f"/templates/{tpl['id']}/delete", client=anna).status_code, 403)
        self.post(f"/templates/{tpl['id']}/delete")
        self.assertIsNone(self.store.template(self.ws, tpl["id"]))

    def test_saved_view_in_sidebar(self):
        self.post("/views/save", {"name": "High in TaskFlow", "page": "tasks", "query": "priority=high&evil=1&q=v4"})
        v = next(x for x in self.store.saved_views(self.me, self.ws) if x["name"] == "High in TaskFlow")
        self.assertEqual(v["query"], "priority=high&q=v4")
        html = self.html("/")
        self.assertIn("High in TaskFlow", html)
        self.assertIn("Urgent tasks", html)
        self.post(f"/views/{v['id']}/delete")
        self.assertNotIn("High in TaskFlow", self.html("/"))

    def test_views_are_private(self):
        anna = self.uid("anna@taskflow.demo")
        self.assertEqual(self.store.saved_views(anna, self.ws), [])


# ====================================================================== REST API
class TestApi(V4):
    def key(self, scope="read"):
        html = self.post("/developer", {"name": "Test script", "scope": scope}).get_data(as_text=True)
        return re.search(r'value="(tf_[\w-]+)"', html).group(1)

    def api(self, method, url, token, body=None):
        return self.app.test_client().open(url, method=method, json=body, headers={"Authorization": f"Bearer {token}"})

    def test_read_key(self):
        token = self.key()
        me = self.api("GET", "/api/v1/me", token).get_json()
        self.assertEqual((me["email"], me["workspace"]["id"], me["scope"]), (ADMIN, self.ws, "read"))
        tasks = self.api("GET", "/api/v1/tasks?limit=3", token).get_json()
        self.assertEqual(len(tasks["tasks"]), 3)
        self.assertIn("fields", tasks["tasks"][0])
        self.assertEqual(self.api("POST", "/api/v1/tasks", token, {"title": "No"}).status_code, 403)
        self.assertTrue(self.api("GET", "/api/v1/projects", token).get_json()["projects"])

    def test_write_key_full_cycle(self):
        token = self.key("write")
        r = self.api("POST", "/api/v1/tasks", token, {"title": "Made by the API", "priority": "high", "due_date": day(4)})
        self.assertEqual(r.status_code, 201)
        tid = r.get_json()["task"]["id"]
        self.assertEqual(self.api("PATCH", f"/api/v1/tasks/{tid}", token, {"status": "in_progress"}).get_json()["task"]["status"],
                         "in_progress")
        t = self.api("PATCH", f"/api/v1/tasks/{tid}", token, {"title": "Renamed by the API"}).get_json()["task"]
        self.assertEqual((t["title"], t["priority"], t["due_date"]), ("Renamed by the API", "high", day(4)))
        self.assertEqual(self.api("POST", f"/api/v1/tasks/{tid}/comments", token, {"body": "From a script"}).status_code, 201)
        self.assertEqual(self.api("DELETE", f"/api/v1/tasks/{tid}", token).status_code, 200)
        self.assertEqual(self.api("GET", f"/api/v1/tasks/{tid}", token).status_code, 404)

    def test_bad_and_revoked_keys(self):
        self.assertEqual(self.api("GET", "/api/v1/me", "tf_nothing").status_code, 401)
        self.assertEqual(self.app.test_client().get("/api/v1/me").status_code, 401)
        token = self.key()
        row = self.store.api_tokens(self.me)[0]
        self.assertTrue(token.startswith(row["prefix"]))
        with self.store.db() as con:                       # only a hash is kept
            self.assertEqual(con.execute("SELECT COUNT(*) FROM api_tokens WHERE token_hash = ?",
                                         (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()[0], 1)
        self.post("/developer", {"action": "revoke", "token_id": row["id"]})
        self.assertEqual(self.api("GET", "/api/v1/me", token).status_code, 401)

    def test_member_rights_and_validation(self):
        anna = self.new_client("anna@taskflow.demo")
        html = self.post("/developer", {"name": "Anna key", "scope": "write"}, client=anna).get_data(as_text=True)
        token = re.search(r'value="(tf_[\w-]+)"', html).group(1)
        theirs = self.task("Admin's private task")
        self.assertEqual(self.api("PATCH", f"/api/v1/tasks/{theirs}", token, {"title": "Hacked"}).status_code, 403)
        self.assertEqual(self.api("POST", "/api/v1/tasks", token, {"title": "x"}).status_code, 400)

    def test_rate_limit(self):
        import v4_views
        old = v4_views.API_PER_MINUTE
        v4_views.API_PER_MINUTE = 3
        try:
            token = self.key()
            codes = [self.api("GET", "/api/v1/me", token).status_code for _ in range(4)]
            self.assertEqual(codes, [200, 200, 200, 429])
        finally:
            v4_views.API_PER_MINUTE = old

    def test_api_needs_no_csrf_but_pages_do(self):
        token = self.key("write")
        r = self.app.test_client().post("/api/v1/tasks", json={"title": "No cookie needed"},
                                        headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.app.test_client().post("/tasks/new", data={"title": "x"}).status_code, 400)


# ====================================================================== webhooks and outbox retries
class TestWebhooks(V4):
    def hook(self, events=("task.created",)):
        return self.store.save_webhook(self.me, self.ws, "https://hooks.example.org/taskflow", list(events))

    def test_only_public_https(self):
        from core import TaskError
        for url in ("http://hooks.example.org/x", "https://user:pw@hooks.example.org/x", "ftp://x"):
            with self.assertRaises(TaskError):
                self.store.save_webhook(self.me, self.ws, url, ["task.created"])
        self.store.resolver = lambda h, p: PRIVATE
        with self.assertRaises(TaskError):
            self.store.save_webhook(self.me, self.ws, "https://intranet.example.org/x", ["task.created"])

    def test_signed_call_on_task_created(self):
        _hid, secret = self.hook()
        self.task("Webhook please")
        call = self.web.calls[-1]
        self.assertEqual(call.full_url, "https://hooks.example.org/taskflow")
        body = call.data
        self.assertEqual(json.loads(body)["task"]["title"], "Webhook please")
        want = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertEqual(call.get_header("X-taskflow-signature"), want)
        self.assertEqual(call.get_header("X-taskflow-event"), "task.created")
        self.assertEqual(self.store.outbox(5, "webhook")[0]["status"], "sent")

    def test_failed_call_is_tried_again_with_backoff(self):
        self.hook()
        self.web.routes = [("https://hooks.example.org", 500, b"")]
        self.task("Server is down")
        row = self.store.outbox(5, "webhook")[0]
        self.assertTrue(row["status"].startswith("failed"))
        self.assertEqual(row["attempts"], 1)
        self.assertIsNotNone(row["next_try"])
        self.assertEqual(self.store.retry_outbox(), [])               # not yet: waits 1 minute
        with self.store.db() as con:
            con.execute("UPDATE outbox SET next_try = '2000-01-01 00:00:00' WHERE id = ?", (row["id"],))
        self.web.routes = []
        self.assertEqual(self.store.retry_outbox(), ["sent"])
        self.assertEqual(self.store.outbox(5, "webhook")[0]["attempts"], 2)

    def test_gives_up_after_five_tries(self):
        self.hook()
        self.web.routes = [("https://hooks.example.org", 500, b"")]
        self.task("Never works")
        oid = self.store.outbox(5, "webhook")[0]["id"]
        for _ in range(6):
            with self.store.db() as con:
                con.execute("UPDATE outbox SET next_try = NULL WHERE id = ?", (oid,))
            self.store.retry_outbox()
        row = self.store.outbox(5, "webhook")[0]
        self.assertEqual(row["attempts"], 5)
        self.assertIn("gave up", row["status"])

    def test_events_filter_and_admin_retry_button(self):
        self.hook(["task.completed"])
        tid = self.task("Only finished counts")
        self.assertEqual(self.store.outbox(5, "webhook"), [])
        self.web.routes = [("https://hooks.example.org", 503, b"")]
        self.store.set_status(self.me, self.ws, tid, "done")
        row = self.store.outbox(5, "webhook")[0]
        self.assertEqual(row["subject"], "task.completed")
        self.assertIn("Send again", self.html("/admin/outbox?channel=webhook"))
        self.web.routes = []
        self.post(f"/admin/outbox/{row['id']}/retry")
        self.assertEqual(self.store.outbox(5, "webhook")[0]["status"], "sent")

    def test_failed_email_is_retried(self):
        class Broken(FakeNotifier):
            def send_email(self, to, subject, text, attachment=None):
                raise OSError("mail server down")
        with self.store.db() as con:
            oid = self.store.outbox_add(con, "email", "someone@mail.example.net", "Hi", "Text", "failed: earlier")
        self.store.notifier = Broken()
        import v4_store
        old = v4_store.is_example_email
        v4_store.is_example_email = lambda e: False           # pretend it is a real address (nothing leaves the test)
        try:
            self.assertTrue(self.store.retry_outbox()[0].startswith("failed: mail server down"))
            with self.store.db() as con:
                con.execute("UPDATE outbox SET next_try = NULL WHERE id = ?", (oid,))
            self.store.notifier = self.mail
            self.assertEqual(self.store.retry_outbox(), ["sent"])
        finally:
            v4_store.is_example_email = old
        self.assertEqual(self.mail.sent[-1][0], "someone@mail.example.net")

    def test_demo_addresses_never_sent_on_retry(self):
        with self.store.db() as con:
            self.store.outbox_add(con, "email", "anna@taskflow.demo", "Hi", "Text", "failed: earlier")
        n = len(self.mail.sent)
        self.assertEqual(self.store.retry_outbox(), ["not sent: demo or test address"])
        self.assertEqual(len(self.mail.sent), n)


# ====================================================================== weekly digest
class TestDigest(V4):
    def test_digest_text_lists_week(self):
        tid = self.task("Due soon for the digest", due_date=day(3), assignee_id=str(self.me))
        subject, text = self.store.digest_text(self.me)
        self.assertEqual(subject, "Your TaskFlow week")
        self.assertIn("Due soon for the digest", text)
        self.assertIn("Due in the next 7 days", text)
        self.assertTrue(tid)

    def test_monday_only_and_demo_skipped(self):
        self.post("/settings/digest", {"digest": "1"})
        self.assertEqual(self.store.user(self.me)["digest"], 1)
        monday = TODAY - timedelta(days=TODAY.weekday())
        self.assertEqual(self.store.send_digests(monday + timedelta(days=1)), 0)      # Tuesday
        self.assertEqual(self.store.send_digests(monday), 0)                        # demo address: skipped
        self.assertEqual(self.store.user(self.me)["digest_at"], monday.isoformat())

    def test_digest_sent_once_per_week(self):
        import v4_store
        self.store.set_digest(self.me, True)
        self.task("Digest item", due_date=day(2), assignee_id=str(self.me))
        monday = TODAY - timedelta(days=TODAY.weekday())
        old = v4_store.is_example_email
        v4_store.is_example_email = lambda e: False
        try:
            self.assertEqual(self.store.send_digests(monday), 1)
            self.assertEqual(self.store.send_digests(monday), 0)
        finally:
            v4_store.is_example_email = old
        self.assertEqual(self.mail.sent[-1][1], "Your TaskFlow week")


# ====================================================================== calendar import
ICS = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:evt-1@calendar.example\r\nSUMMARY:Team planning\\, Q1\r\n"
       "DTSTART;VALUE=DATE:{d1}\r\nDESCRIPTION:Bring the\\nnumbers\r\nEND:VEVENT\r\nBEGIN:VTODO\r\nUID:todo-7@calendar.example\r\n"
       "SUMMARY:Send the invoice to the client about the lo\r\n ng project\r\nDUE:{d2}T170000Z\r\nSTATUS:COMPLETED\r\nEND:VTODO\r\n"
       "END:VCALENDAR\r\n")


class TestCalendarImport(V4):
    def ics(self, d1=None, d2=None):
        return ICS.replace("{d1}", (d1 or day(6)).replace("-", "")).replace("{d2}", (d2 or day(2)).replace("-", ""))

    def test_parse_ics(self):
        from v4_store import parse_ics
        items = parse_ics(self.ics())
        self.assertEqual(items[0]["title"], "Team planning, Q1")
        self.assertEqual(items[0]["description"], "Bring the\nnumbers")
        self.assertEqual(items[0]["due"], day(6))
        self.assertEqual(items[1]["title"], "Send the invoice to the client about the long project")
        self.assertTrue(items[1]["done"])

    def test_import_twice_updates_instead_of_copies(self):
        r = self.store.import_ics(self.me, self.ws, self.ics())
        self.assertEqual((r["created"], r["updated"]), (2, 0))
        r = self.store.import_ics(self.me, self.ws, self.ics(d1=day(9)))
        self.assertEqual((r["created"], r["updated"]), (0, 1))
        t = next(t for t in self.store.tasks(self.ws) if t["title"] == "Team planning, Q1")
        self.assertEqual(t["due_date"], day(9))

    def test_moved_feed_event_changes_task_due_date(self):
        tid = self.task("Exported to the calendar", due_date=day(5))
        ics = f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:task-{tid}@taskflow\r\nSUMMARY:x\r\nDTSTART;VALUE=DATE:{day(12).replace('-', '')}\r\nEND:VEVENT\r\nEND:VCALENDAR"
        self.assertEqual(self.store.import_ics(self.me, self.ws, ics)["updated"], 1)
        self.assertEqual(self.store.task(tid)["due_date"], day(12))

    def test_upload_page(self):
        r = self.post("/calendar/import", {"action": "file", "file": (io.BytesIO(self.ics().encode()), "cal.ics")},
                      content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(any(t["title"] == "Team planning, Q1" for t in self.store.tasks(self.ws)))

    def test_follow_a_calendar_address(self):
        self.web.routes = [("https://calendar.example.org/", 200, self.ics().encode())]
        self.post("/calendar/import", {"action": "follow", "name": "Team", "url": "webcal://calendar.example.org/team.ics"})
        sub = self.store.calendar_subs(self.me, self.ws)[0]
        self.assertEqual(sub["url"], "https://calendar.example.org/team.ics")
        self.assertIn("2 new", sub["last_status"])
        self.assertEqual(self.store.sync_due_calendars(), [])                   # read again only after 6 hours

    def test_private_addresses_refused(self):
        self.store.resolver = lambda h, p: [(2, 1, 6, "", ("127.0.0.1", 443))]
        self.post("/calendar/import", {"action": "follow", "url": "https://localhost.example/cal.ics"})
        self.assertEqual(self.store.calendar_subs(self.me, self.ws), [])

    def test_empty_calendar(self):
        from core import TaskError
        with self.assertRaises(TaskError):
            self.store.import_ics(self.me, self.ws, "BEGIN:VCALENDAR\r\nEND:VCALENDAR")


# ====================================================================== suggested estimate
class TestEstimate(V4):
    def test_assignee_and_labels_count(self):
        rows = self.store._estimate_rows(self.ws)
        self.assertTrue(rows)
        r = rows[0]
        s = self.store.suggest_estimate(self.ws, r["title"], r["project_id"], exclude=r["id"], assignee_id=r["assignee_id"],
                                        labels=list(r["labels"]), priority=r["priority"])
        if s:
            self.assertTrue(all(e["id"] != r["id"] for e in s["examples"]))
        from v4_store import V4Mixin
        q = {"title": "Deploy the app", "project_id": 1, "assignee_id": 2, "labels": [5], "priority": "high"}
        same = {"id": 1, "title": "Deploy the bank app", "project_id": 1, "assignee_id": 2, "labels": {5}, "priority": "high",
                "minutes": 60, "words": __import__("collections").Counter(["deploy", "bank", "app"])}
        other = dict(same, id=2, project_id=9, assignee_id=7, labels=set(), priority="low", minutes=600)
        s = V4Mixin._knn([same, other], q)
        self.assertEqual(s["examples"][0]["id"], 1)
        self.assertLess(s["minutes"], 330)                     # closer to the similar task than a plain average

    def test_accuracy_report(self):
        acc = self.store.estimate_accuracy(self.ws)
        self.assertGreater(acc["tasks"], 0)
        self.assertIn("error", acc["v4"])
        html = self.html("/reports/estimates")
        self.assertIn("Version 4", html)

    def test_estimate_api_uses_new_inputs(self):
        r = self.c.get("/api/estimate?title=Deploy+to+Fly.io&assignee=1&priority=high&label=3").get_json()
        self.assertIn("ok", r)


# ====================================================================== files in S3 storage
class TestS3(V4):
    def test_signature_matches_aws_examples(self):
        import s3
        sig = s3.signature_v4("GET", "https://example.amazonaws.com/", {"X-Amz-Date": "20150830T123600Z"}, s3.EMPTY_SHA256,
                              "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "us-east-1", "service",
                              "20150830T123600Z")
        self.assertTrue(sig.endswith("Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"))
        sig = s3.signature_v4("GET", "https://iam.amazonaws.com/?Action=ListUsers&Version=2010-05-08",
                              {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                               "X-Amz-Date": "20150830T123600Z"}, s3.EMPTY_SHA256, "AKIDEXAMPLE",
                              "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "us-east-1", "iam", "20150830T123600Z")
        self.assertTrue(sig.endswith("Signature=5d672d79c15b13162d9279b0855cfba6789a8edb4c82c400e06b5924a6f2b5d7"))

    def test_files_go_to_object_storage(self):
        import s3
        stored = {}

        def web(req, timeout=None):
            key = req.full_url
            if req.get_method() == "PUT":
                stored[key] = req.data
            elif req.get_method() == "DELETE":
                stored.pop(key, None)
            self.assertIn("AWS4-HMAC-SHA256", req.get_header("Authorization"))
            return Resp(200, stored.get(key, b""))
        self.store.objects = s3.ObjectStore({"S3_BUCKET": "tf-files", "S3_ENDPOINT": "https://s3.storage.example",
                                             "S3_ACCESS_KEY": "AK", "S3_SECRET_KEY": "SK"}, http=web)
        tid = self.task()
        aid = self.store.add_attachment(self.me, tid, "notes.txt", b"hello from S3")
        a = self.store.attachment(aid, with_data=True)
        self.assertEqual(a["storage"], "s3")
        self.assertEqual(a["bytes"], b"hello from S3")
        self.assertTrue(next(iter(stored)).startswith("https://s3.storage.example/tf-files/ws"))
        with self.store.db() as con:
            self.assertEqual(con.execute("SELECT data FROM attachments WHERE id = ?", (aid,)).fetchone()[0], "")
        self.assertEqual(self.store.storage_summary()["s3"][0], 1)
        self.store.delete_attachment(self.me, aid)
        self.assertEqual(stored, {})

    def test_without_bucket_files_stay_in_database(self):
        tid = self.task()
        aid = self.store.add_attachment(self.me, tid, "notes.txt", b"in the database")
        self.assertEqual(self.store.attachment(aid, with_data=True)["storage"], "db")
        self.assertFalse(self.store.objects.enabled)


# ====================================================================== translation suggestions
class TestTranslationSuggestions(V4):
    def test_suggest_and_approve(self):
        import i18n
        samuel = self.new_client("samuel@taskflow.demo")
        r = self.post("/translate/suggest", {"lang": "rw", "english": "New task", "text": "Igikorwa gishya cyane"}, client=samuel)
        self.assertEqual(r.status_code, 302)
        before = len(self.store.translation_suggestions())
        row = self.store.translation_suggestions()[0]                 # newest first
        self.assertEqual((row["user_name"], row["english"]), ("Samuel Habimana", "New task"))
        self.assertIn("Igikorwa gishya cyane", self.html("/admin/suggestions"))
        self.post("/admin/suggestions", {"sid": row["id"], "action": "approve", "text": "Igikorwa gishya cyane"})
        self.assertEqual(i18n.translate_text("New task", "rw"), "Igikorwa gishya cyane")
        self.assertEqual(len(self.store.translation_suggestions()), before - 1)

    def test_gaps_must_match_and_reject(self):
        from core import TaskError
        with self.assertRaises(TaskError):
            self.store.suggest_translation(self.me, "fr", "Hello {0},", "Bonjour,")
        with self.assertRaises(TaskError):
            self.store.suggest_translation(self.me, "en", "New task", "New task")
        sid = self.store.suggest_translation(self.me, "fr", "Hello {0},", "Salut {0},")
        self.store.decide_suggestion(self.me, sid, False)
        self.assertEqual(self.store.translation_suggestions("rejected")[0]["id"], sid)
        with self.assertRaises(TaskError):
            self.store.decide_suggestion(self.me, sid, True)

    def test_only_site_admin_decides(self):
        anna = self.new_client("anna@taskflow.demo")
        self.assertEqual(anna.get("/admin/suggestions").status_code, 403)


# ====================================================================== sign-in limits
class TestLoginLimits(Base):
    def test_five_wrong_passwords_then_wait(self):
        for _ in range(5):
            self.assertEqual(self.login(ADMIN, "wrong-password").status_code, 401)
        r = self.login(ADMIN, "Admin@1234")
        self.assertEqual(r.status_code, 429)
        self.assertIn("Please wait", r.get_data(as_text=True))
        self.assertEqual(self.login("anna@taskflow.demo", "Demo@1234").status_code, 302)    # another account works

    def test_success_clears_count(self):
        for _ in range(4):
            self.login(ADMIN, "wrong-password")
        self.assertEqual(self.login(ADMIN, "Admin@1234").status_code, 302)
        self.assertEqual(self.app.store.login_wait(ADMIN, "127.0.0.1"), 0)

    def test_one_address_many_accounts(self):
        for i in range(20):
            self.login(f"nobody{i}@taskflow.demo", "wrong")
        self.assertGreater(self.app.store.login_wait("anna@taskflow.demo", "127.0.0.1"), 0)
        self.assertEqual(self.app.store.login_wait("anna@taskflow.demo", "10.1.1.1"), 0)


# ====================================================================== single sign-on
def make_key():
    from cryptography.hazmat.primitives.asymmetric import rsa
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwt(key, claims, kid="k1"):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    import oidc
    head = oidc.b64url(json.dumps({"alg": "RS256", "kid": kid, "typ": "JWT"}).encode())
    body = oidc.b64url(json.dumps(claims).encode())
    sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{oidc.b64url(sig)}"


def jwks(key, kid="k1"):
    import oidc
    n = key.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "kid": kid, "n": oidc.b64url(n.n.to_bytes(256, "big")), "e": oidc.b64url(n.e.to_bytes(3, "big"))}]}


class TestSSO(Base):
    env = {"GOOGLE_CLIENT_ID": "client-123.apps.example", "GOOGLE_CLIENT_SECRET": "not-a-real-secret"}
    KEY = None

    def setUp(self):
        super().setUp()
        if TestSSO.KEY is None:
            TestSSO.KEY = make_key()
        self.claims = {}
        self.web = FakeWeb([("https://oauth2.googleapis.com/token", 200, lambda: json.dumps({"id_token": jwt(self.KEY, self.claims)}).encode()),
                            ("https://www.googleapis.com/oauth2/v3/certs", 200, json.dumps(jwks(self.KEY)).encode())])
        self.store.http = self.web

    def sign_in(self, email, **extra):
        r = self.c.get("/auth/sso/google")
        self.assertEqual(r.status_code, 302)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.headers["Location"]).query))
        self.assertEqual(q["code_challenge_method"], "S256")
        self.claims = dict({"iss": "https://accounts.google.com", "aud": "client-123.apps.example", "sub": "g-" + email,
                            "email": email, "email_verified": True, "name": "Someone Example", "exp": time.time() + 600,
                            "nonce": q["nonce"]}, **extra)
        return self.c.get(f"/auth/sso/google/callback?code=abc&state={q['state']}")

    def test_login_page_shows_button(self):
        self.assertIn("Sign in with Google", self.c.get("/login").get_data(as_text=True))

    def test_existing_account_is_linked(self):
        r = self.sign_in("anna@taskflow.demo")
        self.assertEqual(r.status_code, 302)
        with self.c.session_transaction() as s:
            self.assertEqual(s["uid"], self.uid("anna@taskflow.demo"))
        self.assertEqual(self.store.user(self.uid("anna@taskflow.demo"))["sso_provider"], "google")
        body = urllib.parse.parse_qs(self.web.calls[0].data.decode())
        self.assertIn("code_verifier", body)

    def test_new_account_when_signup_open(self):
        self.sign_in("new.person@company.example")
        u = self.store.user_by_email("new.person@company.example")
        self.assertIsNotNone(u)
        self.assertEqual(u["name"], "Someone Example")

    def test_bad_tokens_are_refused(self):
        for extra in ({"aud": "someone-else"}, {"exp": time.time() - 3600}, {"iss": "https://evil.example"},
                      {"email_verified": False}, {"nonce": "other"}):
            c = self.app.test_client()
            self.c = c
            r = self.sign_in("anna@taskflow.demo", **extra)
            self.assertEqual(r.headers["Location"], "/login", extra)
            with c.session_transaction() as s:
                self.assertNotIn("uid", s)

    def test_forged_signature(self):
        import oidc
        cfg = oidc.config(self.env, "google")
        other = make_key()
        token = jwt(other, {"iss": "https://accounts.google.com", "aud": cfg["client_id"], "nonce": "n",
                            "exp": time.time() + 60, "email": "anna@taskflow.demo", "email_verified": True})
        with self.assertRaises(oidc.SSOError):
            oidc.verify_id_token(cfg, token, "n", jwks(self.KEY))

    def test_wrong_state(self):
        self.c.get("/auth/sso/google")
        r = self.c.get("/auth/sso/google/callback?code=abc&state=forged")
        self.assertEqual(r.headers["Location"], "/login")

    def test_provider_off_without_keys(self):
        self.assertEqual(self.c.get("/auth/sso/microsoft").status_code, 404)


# ====================================================================== security, speed, look
class TestSecurityAndSpeed(V4):
    def test_security_headers(self):
        r = self.c.get("/board")
        csp = r.headers["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'self'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertEqual(r.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertIn("camera=()", r.headers["Permissions-Policy"])

    def test_gzip(self):
        r = self.c.get("/board", headers={"Accept-Encoding": "gzip, br"})
        self.assertEqual(r.headers["Content-Encoding"], "gzip")
        page = gzip.decompress(r.data).decode()
        self.assertIn("</html>", page)
        self.assertLess(len(r.data), len(page) / 3)
        self.assertNotIn("Content-Encoding", self.c.get("/board").headers)

    def test_healthz(self):
        r = self.app.test_client().get("/healthz")
        self.assertEqual(r.get_json()["database"], "ok")
        self.assertEqual(r.get_json()["version"], "4.0")

    def test_theme(self):
        self.post("/settings/theme", {"theme": "dark"})
        self.assertIn('data-theme="dark"', self.html("/"))
        self.assertEqual(self.post("/settings/theme", {"theme": "pink"}).status_code, 400)

    def test_offline_and_install_pages(self):
        self.assertIn("You are offline", self.app.test_client().get("/offline").get_data(as_text=True))
        self.assertIn("Add to Home Screen", self.app.test_client().get("/install").get_data(as_text=True))
        sw = self.c.get("/sw.js").get_data(as_text=True)
        self.assertIn("caches.match(OFFLINE)", sw)

    def test_palette_and_shortcuts_on_page(self):
        html = self.html("/")
        self.assertIn('id="palette"', html)
        self.assertIn("v4.js", html)
        self.assertIn("Workspace tools", html)

    def test_system_page(self):
        html = self.html("/admin/system")
        self.assertIn("one process (SQLite)" if not PG_URL else "LISTEN", html)
        self.assertIn("File storage", html)


class TestJobClaim(Base):
    def test_only_one_worker_runs_jobs(self):
        self.assertTrue(self.store.claim_jobs())
        self.assertFalse(self.store.claim_jobs())                 # the second worker skips
        with self.store.db() as con:
            con.execute("UPDATE settings SET value = '2000-01-01 00:00:00' WHERE key = 'jobs_claim'")
        self.assertTrue(self.store.claim_jobs())


class TestCron(Base):
    env = {"CRON_KEY": "cron-key-for-tests"}

    def test_cron_output_has_v4_jobs(self):
        out = self.c.get("/cron/run?key=cron-key-for-tests").get_json()
        for k in ("outbox_retried", "digests", "calendars", "reminders"):
            self.assertIn(k, out)


# ====================================================================== live updates between processes
class TestLiveBus(V4):
    def test_publish_only_on_postgres(self):
        import livebus
        seen = []

        class Con:
            is_postgres = True

            def execute(self, sql, params=()):
                seen.append((sql, params))
        livebus.publish(Con(), 7)
        self.assertEqual(seen, [("SELECT pg_notify(?, ?)", ("taskflow_live", "7"))])
        Con.is_postgres = False
        livebus.publish(Con(), 7)
        self.assertEqual(len(seen), 1)

    def test_listener_wakes_boards(self):
        import livebus
        woke = []

        class Conn:
            def __init__(self):
                self.sql = []

            def execute(self, sql):
                self.sql.append(sql)

            def notifies(self):
                yield "first"
                yield "second"
        conn = Conn()
        lis = livebus.Listener(lambda: conn, lambda: woke.append(1))
        lis.run_once(conn, max_messages=2)
        self.assertEqual(conn.sql, ["LISTEN taskflow_live"])
        self.assertEqual(len(woke), 2)

    def test_direct_url_for_listen(self):
        import livebus
        self.assertEqual(livebus.direct_url("postgres://u:p@ep-cool-1-pooler.eu.aws.neon.tech/db?sslmode=require"),
                         "postgresql://u:p@ep-cool-1.eu.aws.neon.tech/db?sslmode=require")

    @unittest.skipUnless(PG_URL, "needs PostgreSQL")
    def test_changes_notify_on_postgres(self):
        self.task("Notify the other servers")          # pg_notify runs inside the same transaction without error


# ====================================================================== upgrade and demo data
@unittest.skipIf(PG_URL, "the old-version file test is for SQLite")
class TestUpgradeFromVersion3(unittest.TestCase):
    def test_v3_database_gets_v4_columns(self):
        path = os.path.join(tempfile.mkdtemp(), "old3.db")
        con = sqlite3.connect(path)
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "taskflow_v3.sql"), encoding="utf-8") as f:
            con.executescript(f.read())
        con.close()
        from store import Store
        s = Store(path, notifier=FakeNotifier(), env={})
        with s.db() as c:
            self.assertIn("column_id", c.columns("tasks"))
            self.assertIn("attempts", c.columns("outbox"))
            self.assertIn("theme", c.columns("users"))
            self.assertIn("board_columns", c.tables())
        t = s.tasks(s.memberships(s.user_by_email("old3@teamtest.example")["id"])[0]["id"])
        self.assertEqual(t[0]["title"], "Task from version 3")


class TestDemoData(V4):
    def test_v4_demo_items(self):
        self.assertEqual(len(self.store.automations(self.ws)), 3)
        self.assertGreaterEqual(len(self.store.templates(self.ws)), 2)
        self.assertEqual(len(self.store.fields(self.ws)), 3)
        self.assertEqual(len(self.store.saved_views(self.me, self.ws)), 2)
        self.assertTrue(any(t["title"] == "Sign in with Google and Microsoft" for t in self.store.tasks(self.ws)))


if __name__ == "__main__":
    unittest.main()
