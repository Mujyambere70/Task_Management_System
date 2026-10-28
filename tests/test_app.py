import os
import re
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from store import TaskError

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(os.path.join(self.tmp.name, "t.db"), seed=True, testing=True)
        self.store = self.app.store
        self.c = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def token(self):
        html = self.c.get("/login" if not self.logged_in() else "/profile").get_data(as_text=True)
        return re.search(r'name="csrf-token" content="(\w+)"', html).group(1)

    def logged_in(self):
        with self.c.session_transaction() as s:
            return "uid" in s

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data["csrf_token"] = self.token()
        return self.c.post(url, data=data, **kw)

    def login(self, email="admin@taskflow.demo", password="Admin@1234"):
        return self.post("/login", {"email": email, "password": password})

    def member(self):
        return self.store.authenticate("anna@taskflow.demo", "Demo@1234")


class TestAuth(Base):
    def test_pages_need_login(self):
        for url in ["/", "/board", "/tasks", "/calendar", "/reports", "/team", "/profile"]:
            r = self.c.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login", r.location)

    def test_login_and_logout(self):
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.c.get("/").status_code, 200)
        self.post("/logout")
        self.assertEqual(self.c.get("/").status_code, 302)

    def test_wrong_password(self):
        r = self.login(password="nope")
        self.assertEqual(r.status_code, 401)
        self.assertIn("Wrong email or password", r.get_data(as_text=True))

    def test_post_without_csrf_is_rejected(self):
        r = self.c.post("/login", data={"email": "admin@taskflow.demo", "password": "Admin@1234"})
        self.assertEqual(r.status_code, 400)

    def test_register(self):
        r = self.post("/register", {"name": "New Person", "email": "new@x.com", "password": "Secret123",
                                    "confirm": "Secret123"})
        self.assertEqual(r.status_code, 302)
        u = self.store.authenticate("new@x.com", "Secret123")
        self.assertEqual(u["role"], "member")

    def test_register_errors(self):
        bad = [{"name": "A B", "email": "admin@taskflow.demo", "password": "Secret123", "confirm": "Secret123"},
               {"name": "A B", "email": "x@y.com", "password": "short", "confirm": "short"},
               {"name": "A B", "email": "x@y.com", "password": "Secret123", "confirm": "Other1234"},
               {"name": "A B", "email": "not-an-email", "password": "Secret123", "confirm": "Secret123"}]
        for form in bad:
            self.assertEqual(self.post("/register", form).status_code, 400, form)

    def test_safe_redirect_after_login(self):
        r = self.post("/login?next=//evil.com", {"email": "admin@taskflow.demo", "password": "Admin@1234"})
        self.assertEqual(r.location, "/")


class TestTasks(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def new_task(self, **kw):
        data = {"title": "Write the test plan", "description": "All modules", "status": "todo",
                "priority": "high", "assignee_id": "2", "project_id": "1", "due_date": TOMORROW}
        data.update(kw)
        return self.post("/tasks/new", data)

    def test_all_pages_render(self):
        for url in ["/", "/board", "/board?project=1&priority=high", "/tasks", "/tasks?status=open&due=week&sort=priority",
                    "/tasks?q=crash", "/tasks/1", "/tasks/1/edit", "/tasks/new", "/calendar", "/calendar?m=2026-01",
                    "/projects", "/team", "/reports", "/profile"]:
            self.assertEqual(self.c.get(url).status_code, 200, url)

    def test_create_task(self):
        r = self.new_task()
        self.assertEqual(r.status_code, 302)
        tid = int(r.location.rsplit("/", 1)[1])
        t = self.store.task(tid)
        self.assertEqual((t["title"], t["priority"], t["assignee_name"]), ("Write the test plan", "high", "Anna Petrova"))

    def test_create_task_validation(self):
        before = len(self.store.tasks())
        self.new_task(title="ab")
        self.new_task(due_date="2000-01-01")
        self.new_task(assignee_id="999")
        self.new_task(priority="extreme")
        self.assertEqual(len(self.store.tasks()), before)

    def test_search_and_filters(self):
        self.assertTrue(all("crash" in t["title"].lower() or "crash" in t["description"].lower()
                            for t in self.store.tasks(q="crash")))
        overdue = self.store.tasks(due="overdue")
        self.assertTrue(overdue)
        self.assertTrue(all(t["status"] != "done" and t["due_date"] < date.today().isoformat() for t in overdue))
        self.assertTrue(all(t["status"] == "done" for t in self.store.tasks(status="done")))

    def test_status_change_sets_completed(self):
        tid = int(self.new_task().location.rsplit("/", 1)[1])
        self.post(f"/tasks/{tid}/status", {"status": "done"})
        self.assertIsNotNone(self.store.task(tid)["completed_at"])
        self.post(f"/tasks/{tid}/status", {"status": "in_progress"})
        self.assertIsNone(self.store.task(tid)["completed_at"])

    def test_drag_and_drop_api(self):
        r = self.c.post("/api/tasks/5/status", json={"status": "review"}, headers={"X-CSRF-Token": self.token()})
        self.assertEqual(r.get_json()["ok"], True)
        self.assertEqual(self.store.task(5)["status"], "review")
        r = self.c.post("/api/tasks/5/status", json={"status": "nope"}, headers={"X-CSRF-Token": self.token()})
        self.assertEqual(r.status_code, 400)

    def test_edit_task(self):
        r = self.post("/tasks/1/edit", {"title": "New name", "status": "review", "priority": "low",
                                        "assignee_id": "", "project_id": "", "due_date": ""})
        self.assertEqual(r.status_code, 302)
        t = self.store.task(1)
        self.assertEqual((t["title"], t["assignee_id"], t["project_id"], t["due_date"]), ("New name", None, None, None))

    def test_comment(self):
        self.post("/tasks/3/comment", {"body": "Looks great!"})
        self.assertEqual(self.store.comments(3)[-1]["body"], "Looks great!")
        n = len(self.store.comments(3))
        self.post("/tasks/3/comment", {"body": "   "})
        self.assertEqual(len(self.store.comments(3)), n)

    def test_delete(self):
        self.post("/tasks/2/delete")
        self.assertIsNone(self.store.task(2))
        self.assertEqual(self.c.get("/tasks/2").status_code, 404)

    def test_activity_is_logged(self):
        self.new_task(title="Logged task")
        self.assertEqual(self.store.activity(1)[0]["action"], "task_created")

    def test_csv_export(self):
        r = self.c.get("/reports/tasks.csv")
        self.assertEqual(r.mimetype, "text/csv")
        self.assertIn("Renew domain and SSL certificate", r.get_data(as_text=True))


class TestPermissions(Base):
    def setUp(self):
        super().setUp()
        self.login("anna@taskflow.demo", "Demo@1234")
        self.anna = self.member()
        # a task Anna did not create and is not assigned to
        self.other = next(t for t in self.store.tasks() if not self.store.can_edit(self.anna, t))

    def test_member_cannot_edit_others_task(self):
        self.assertEqual(self.c.get(f"/tasks/{self.other['id']}/edit").status_code, 403)
        r = self.c.post(f"/api/tasks/{self.other['id']}/status", json={"status": "done"},
                        headers={"X-CSRF-Token": self.token()})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.post(f"/tasks/{self.other['id']}/delete").status_code, 403)

    def test_member_can_change_own_task(self):
        mine = self.store.tasks(assignee=self.anna["id"], status="open")[0]
        r = self.c.post(f"/api/tasks/{mine['id']}/status", json={"status": "done"},
                        headers={"X-CSRF-Token": self.token()})
        self.assertEqual(r.status_code, 200)

    def test_member_cannot_use_admin_actions(self):
        self.assertEqual(self.post("/projects/save", {"id": "1", "name": "Hack"}).status_code, 403)
        self.assertEqual(self.post("/projects/1/delete", {"mode": "keep"}).status_code, 403)
        self.assertEqual(self.post("/projects/1/archive").status_code, 403)
        self.assertEqual(self.post("/team/add", {"name": "X Y", "email": "x@y.com", "password": "Secret123"}).status_code, 403)
        self.assertEqual(self.post("/team/1/role", {"role": "member"}).status_code, 403)


class TestAdmin(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_add_member_and_deactivate(self):
        self.post("/team/add", {"name": "Olga Kim", "email": "olga@x.com", "password": "Temp12345", "role": "member"})
        u = self.store.authenticate("olga@x.com", "Temp12345")
        self.post(f"/team/{u['id']}/toggle")
        with self.assertRaises(TaskError):
            self.store.authenticate("olga@x.com", "Temp12345")

    def test_admin_cannot_demote_self(self):
        self.post("/team/1/role", {"role": "member"})
        self.assertEqual(self.store.user(1)["role"], "admin")

    def test_reset_password(self):
        self.post("/team/2/reset", {"password": "Brandnew99"})
        self.assertTrue(self.store.authenticate("anna@taskflow.demo", "Brandnew99"))

    def test_projects(self):
        self.post("/projects/save", {"name": "Research", "description": "R&D", "color": "#10b981"})
        p = [p for p in self.store.projects() if p["name"] == "Research"][0]
        self.post(f"/projects/{p['id']}/archive")
        self.assertNotIn("Research", [p["name"] for p in self.store.projects()])

    def test_profile_and_password(self):
        self.post("/profile", {"form": "profile", "name": "Jean M.", "email": "boss@taskflow.demo", "job_title": "CEO"})
        self.assertEqual(self.store.user(1)["name"], "Jean M.")
        self.post("/profile", {"form": "password", "old": "Admin@1234", "new": "Better1234", "confirm": "Better1234"})
        self.assertTrue(self.store.authenticate("boss@taskflow.demo", "Better1234"))

    def test_reports_numbers(self):
        s = self.store.stats()
        self.assertEqual(s["total"], sum(s["by_status"].values()))
        self.assertEqual(s["open"], s["total"] - s["done"])
        people = self.store.user_report()
        self.assertEqual(sum(p["assigned"] for p in people), len([t for t in self.store.tasks() if t["assignee_id"]]))


class TestAdminManagement(Base):
    def setUp(self):
        super().setUp()
        self.login()

    def test_admin_pages(self):
        for url in ["/admin", "/admin?tab=data&table=users", "/admin?tab=data&table=activity", "/admin?tab=maintenance"]:
            r = self.c.get(url)
            self.assertEqual(r.status_code, 200, url)
        self.assertNotIn("pbkdf2", self.c.get("/admin?tab=data&table=users").get_data(as_text=True))
        self.assertNotIn("scrypt", self.c.get("/admin/data/users.csv").get_data(as_text=True))

    def test_member_cannot_open_admin(self):
        self.post("/logout")
        self.login("anna@taskflow.demo", "Demo@1234")
        self.assertEqual(self.c.get("/admin").status_code, 403)
        self.assertEqual(self.c.get("/admin/data/users.csv").status_code, 403)
        self.assertEqual(self.post("/tasks/bulk", {"ids": "1", "action": "delete"}).status_code, 403)

    def test_edit_other_user(self):
        self.post("/team/2/edit", {"name": "Anna P. Smith", "email": "anna.smith@taskflow.demo",
                                   "job_title": "Lead Frontend", "color": "#ec4899"})
        u = self.store.user(2)
        self.assertEqual((u["name"], u["email"], u["job_title"], u["color"]),
                         ("Anna P. Smith", "anna.smith@taskflow.demo", "Lead Frontend", "#ec4899"))
        self.post("/team/2/edit", {"name": "Anna", "email": "david@taskflow.demo"})   # email taken
        self.assertEqual(self.store.user(2)["email"], "anna.smith@taskflow.demo")

    def test_delete_user(self):
        assigned = [t["id"] for t in self.store.tasks(assignee=3)]
        self.post("/team/3/delete")
        self.assertIsNone(self.store.user(3))
        self.assertTrue(all(self.store.task(i)["assignee_id"] is None for i in assigned))
        self.post("/team/1/delete")                       # cannot delete yourself
        self.assertIsNotNone(self.store.user(1))

    def test_admin_changes_own_details(self):
        self.post("/profile", {"form": "profile", "name": "Jean Michel M.", "email": "jm@taskflow.demo",
                               "job_title": "Head of Projects", "color": "#10b981"})
        u = self.store.user(1)
        self.assertEqual((u["name"], u["email"], u["color"]), ("Jean Michel M.", "jm@taskflow.demo", "#10b981"))

    def test_delete_project_keep_or_delete_tasks(self):
        keep = [t["id"] for t in self.store.tasks(project=1)]
        self.post("/projects/1/delete", {"mode": "keep"})
        self.assertIsNone(self.store.project(1))
        self.assertTrue(all(self.store.task(i)["project_id"] is None for i in keep))
        gone = [t["id"] for t in self.store.tasks(project=2)]
        self.post("/projects/2/delete", {"mode": "tasks"})
        self.assertTrue(all(self.store.task(i) is None for i in gone))

    def test_delete_comment(self):
        c = self.store.comments(9)[0]
        self.post(f"/comments/{c['id']}/delete")
        self.assertNotIn(c["id"], [x["id"] for x in self.store.comments(9)])

    def test_member_deletes_only_own_comment(self):
        self.post("/logout")
        self.login("anna@taskflow.demo", "Demo@1234")
        other = self.store.comments(9)[0]                     # written by David
        self.assertEqual(self.post(f"/comments/{other['id']}/delete").status_code, 403)
        self.post("/tasks/9/comment", {"body": "mine"})
        mine = self.store.comments(9)[-1]
        self.post(f"/comments/{mine['id']}/delete")
        self.assertNotIn(mine["id"], [x["id"] for x in self.store.comments(9)])

    def test_bulk_actions(self):
        self.post("/tasks/bulk", {"ids": ["1", "2"], "action": "status", "value": "done"})
        self.assertTrue(all(self.store.task(i)["status"] == "done" and self.store.task(i)["completed_at"] for i in (1, 2)))
        self.post("/tasks/bulk", {"ids": ["1", "2"], "action": "assignee", "value": "4"})
        self.assertEqual({self.store.task(i)["assignee_id"] for i in (1, 2)}, {4})
        self.post("/tasks/bulk", {"ids": ["1", "2"], "action": "priority", "value": "urgent"})
        self.assertEqual(self.store.task(1)["priority"], "urgent")
        self.post("/tasks/bulk", {"ids": ["1", "2"], "action": "delete"})
        self.assertIsNone(self.store.task(1))

    def test_settings(self):
        self.post("/admin/settings", {"workspace_name": "Acme Team", "default_priority": "high"})
        self.assertIn("Acme Team", self.c.get("/").get_data(as_text=True))
        self.post("/logout")
        r = self.c.get("/register")
        self.assertEqual(r.status_code, 302)                   # sign-up turned off

    def test_maintenance(self):
        self.post("/admin/maintenance", {"action": "clear_activity", "days": "0"})
        self.assertEqual(len(self.store.activity(100)), 1)    # only the "log cleared" entry
        self.post("/admin/maintenance", {"action": "clean_tasks", "days": "0"})
        self.assertEqual(self.store.tasks(status="done"), [])
        self.post("/admin/maintenance", {"action": "reset_demo", "confirm": "nope"})
        self.assertEqual(self.store.tasks(status="done"), [])
        self.post("/admin/maintenance", {"action": "reset_demo", "confirm": "RESET"})
        self.assertTrue(self.store.tasks(status="done"))
        self.assertEqual(self.c.get("/").status_code, 302)     # signed out


class TestUserOwnership(Base):
    """Normal members create and fully manage their own things."""

    def setUp(self):
        super().setUp()
        self.login("anna@taskflow.demo", "Demo@1234")
        self.anna = self.member()

    def test_member_creates_edits_archives_deletes_own_project(self):
        self.post("/projects/save", {"name": "Anna's Research", "description": "UI study", "color": "#ec4899"})
        p = next(p for p in self.store.projects() if p["name"] == "Anna's Research")
        self.assertEqual(p["created_by"], self.anna["id"])
        self.post("/projects/save", {"id": str(p["id"]), "name": "Anna's UI Research", "color": "#10b981"})
        self.assertEqual(self.store.project(p["id"])["name"], "Anna's UI Research")
        self.post(f"/projects/{p['id']}/archive")
        self.assertEqual(self.store.project(p["id"])["archived"], 1)
        self.post(f"/projects/{p['id']}/delete", {"mode": "tasks"})
        self.assertIsNone(self.store.project(p["id"]))

    def test_member_full_task_life_cycle(self):
        r = self.post("/tasks/new", {"title": "Design settings page", "status": "todo", "priority": "medium",
                                     "assignee_id": "", "project_id": "", "due_date": TOMORROW})
        tid = int(r.location.rsplit("/", 1)[1])
        self.post(f"/tasks/{tid}/edit", {"title": "Design settings page v2", "status": "in_progress",
                                         "priority": "high", "assignee_id": "", "project_id": "", "due_date": ""})
        self.assertEqual(self.store.task(tid)["title"], "Design settings page v2")
        self.assertIn(tid, [t["id"] for t in self.store.tasks(creator=self.anna["id"])])
        self.post(f"/tasks/{tid}/delete")
        self.assertIsNone(self.store.task(tid))

    def test_checklist(self):
        tid = self.store.tasks(assignee=self.anna["id"], status="open")[0]["id"]
        self.post(f"/tasks/{tid}/checklist", {"title": "Write the HTML"})
        sub = self.store.subtasks(tid)[-1]
        self.post(f"/checklist/{sub['id']}/toggle")
        self.assertEqual(self.store.subtask(sub["id"])["done"], 1)
        self.post(f"/checklist/{sub['id']}/edit", {"title": "Write the HTML and CSS"})
        self.assertEqual(self.store.subtask(sub["id"])["title"], "Write the HTML and CSS")
        r = self.c.post(f"/checklist/{sub['id']}/toggle", data={"csrf_token": self.token()},
                        headers={"X-Requested-With": "fetch"})
        self.assertTrue(r.get_json()["ok"])
        self.post(f"/checklist/{sub['id']}/delete")
        self.assertIsNone(self.store.subtask(sub["id"]))

    def test_cannot_change_checklist_of_others_task(self):
        other = next(t for t in self.store.tasks() if not self.store.can_edit(self.anna, t))
        self.assertEqual(self.post(f"/tasks/{other['id']}/checklist", {"title": "Hack"}).status_code, 403)

    def test_edit_own_comment_only(self):
        self.post("/tasks/3/comment", {"body": "First version"})
        mine = self.store.comments(3)[-1]
        self.post(f"/comments/{mine['id']}/edit", {"body": "Better version"})
        c = self.store.comments(3)[-1]
        self.assertEqual((c["body"], bool(c["edited_at"])), ("Better version", True))
        other = next(c for c in self.store.comments(9) if c["user_id"] != self.anna["id"])
        self.assertEqual(self.post(f"/comments/{other['id']}/edit", {"body": "x"}).status_code, 403)

    def test_profile_and_own_account(self):
        self.post("/profile", {"form": "profile", "name": "Anna Petrova-Kim", "email": "anna.k@taskflow.demo",
                               "job_title": "Senior Frontend", "color": "#14b8a6"})
        self.assertEqual(self.store.user(self.anna["id"])["name"], "Anna Petrova-Kim")
        self.assertEqual(self.c.get("/profile").status_code, 200)
        self.post("/profile", {"form": "delete", "password": "wrong"})
        self.assertIsNotNone(self.store.user(self.anna["id"]))
        self.post("/profile", {"form": "delete", "password": "Demo@1234"})
        self.assertIsNone(self.store.user(self.anna["id"]))
        self.assertEqual(self.c.get("/").status_code, 302)

    def test_admin_cannot_delete_own_account(self):
        self.post("/logout")
        self.login()
        self.post("/profile", {"form": "delete", "password": "Admin@1234"})
        self.assertIsNotNone(self.store.user(1))


class TestDemoData(Base):
    def test_portfolio_projects_from_cv(self):
        names = " ".join(p["name"] for p in self.store.projects())
        for word in ["Yo Delivery", "PyBank", "TaskFlow", "SFU"]:
            self.assertIn(word, names)
        self.assertEqual(self.store.user(1)["name"], "Jean Michel Mujyambere")
        self.assertIn("Jean Michel Mujyambere", self.c.get("/login").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
