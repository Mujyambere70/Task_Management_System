"""Core features from version 1, now inside workspaces: sign-in, tasks, board, permissions, admin, projects."""

import unittest
from datetime import date, timedelta

from support import Base

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


class TestAuth(Base):
    def test_pages_need_login(self):
        for url in ["/", "/board", "/tasks", "/calendar", "/reports", "/team", "/profile", "/resumes",
                    "/notifications", "/workspaces"]:
            r = self.c.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login", r.location)

    def test_public_pages_without_login(self):
        for url in ["/login", "/register", "/forgot", "/about", "/explore", "/r/jean-michel-mujyambere"]:
            self.assertEqual(self.c.get(url).status_code, 200, url)

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

    def test_register_gives_own_workspace(self):
        r = self.post("/register", {"name": "New Person", "email": "new@teamtest.io", "password": "Secret123",
                                    "confirm": "Secret123"})
        self.assertEqual(r.status_code, 302)
        u = self.store.authenticate("new@teamtest.io", "Secret123")
        self.assertEqual(u["role"], "member")                       # not the site administrator
        mine = self.store.memberships(u["id"])
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["role"], "admin")                  # admin of the own new workspace
        self.assertEqual(mine[0]["name"], "New's workspace")
        self.assertIn("New&#39;s workspace", self.html("/"))

    def test_register_errors(self):
        bad = [{"name": "A B", "email": "admin@taskflow.demo", "password": "Secret123", "confirm": "Secret123"},
               {"name": "A B", "email": "x@teamtest.io", "password": "short", "confirm": "short"},
               {"name": "A B", "email": "x@teamtest.io", "password": "Secret123", "confirm": "Other1234"},
               {"name": "A B", "email": "not-an-email", "password": "Secret123", "confirm": "Secret123"}]
        for form in bad:
            self.assertEqual(self.post("/register", form).status_code, 400, form)

    def test_safe_redirect_after_login(self):
        r = self.post("/login?next=https://evil.example/x", {"email": "admin@taskflow.demo", "password": "Admin@1234"})
        self.assertEqual(r.location, "/")
        self.post("/logout")
        r = self.post("/login?next=/calendar", {"email": "admin@taskflow.demo", "password": "Admin@1234"})
        self.assertEqual(r.location, "/calendar")

    def test_registration_can_be_turned_off(self):
        self.login()
        self.post("/admin/settings", {})
        self.post("/logout")
        r = self.c.get("/register")
        self.assertEqual(r.status_code, 302)


class TestTasks(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.ws = self.first_ws()

    def new_task(self, **kw):
        form = {"title": "Write the release notes", "description": "For version 2", "priority": "high",
                "status": "todo", "assignee_id": str(self.uid("anna@taskflow.demo")), "due_date": TOMORROW}
        form.update(kw)
        r = self.post("/tasks/new", form)
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:500])
        return int(r.location.rstrip("/").split("/")[-1])

    def test_all_pages_render(self):
        for url in ["/", "/board", "/tasks", "/tasks?status=open&sort=priority", "/tasks/new", "/calendar",
                    "/calendar?m=2026-01", "/projects", "/team", "/reports", "/profile", "/notifications",
                    "/workspaces", "/workspace/settings", "/about"]:
            self.html(url)

    def test_create_task_in_current_workspace(self):
        tid = self.new_task()
        t = self.store.task(tid)
        self.assertEqual(t["workspace_id"], self.ws)
        self.assertEqual(t["priority"], "high")
        self.assertIn("Write the release notes", self.html(f"/tasks/{tid}"))

    def test_create_task_validation(self):
        r = self.post("/tasks/new", {"title": "ab", "status": "todo", "priority": "low"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("at least 3", r.get_data(as_text=True))
        r = self.post("/tasks/new", {"title": "Valid title", "status": "todo", "priority": "low",
                                     "due_date": "2020-01-01"})
        self.assertIn("past", r.get_data(as_text=True))

    def test_cannot_assign_someone_outside_the_workspace(self):
        li = self.uid("li@taskflow.demo")                      # member of the second workspace only
        r = self.post("/tasks/new", {"title": "Valid title", "status": "todo", "priority": "low", "assignee_id": li})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.store.tasks(self.ws, q="Valid title"), [])

    def test_search_and_filters(self):
        self.new_task(title="Unique zebra task")
        html = self.html("/tasks?q=zebra")
        self.assertIn("Unique zebra task", html)
        self.assertNotIn("Build product catalogue", html)
        self.assertIn("Unique zebra task", self.html("/tasks?q=ZEBRA"))
        html = self.html("/tasks?due=overdue")
        self.assertIn("Renew domain", html)

    def test_status_change_sets_completed(self):
        tid = self.new_task()
        self.post(f"/tasks/{tid}/status", {"status": "done"})
        self.assertIsNotNone(self.store.task(tid)["completed_at"])
        self.post(f"/tasks/{tid}/status", {"status": "todo"})
        self.assertIsNone(self.store.task(tid)["completed_at"])

    def test_drag_and_drop_api(self):
        tid = self.new_task()
        r = self.post_json(f"/api/tasks/{tid}/status", {"status": "review"})
        self.assertEqual(r.get_json()["ok"], True)
        self.assertEqual(self.store.task(tid)["status"], "review")
        r = self.post_json(f"/api/tasks/{tid}/status", {"status": "nonsense"})
        self.assertEqual(r.status_code, 400)
        r = self.c.post(f"/api/tasks/{tid}/status", json={"status": "done"})        # no CSRF header
        self.assertEqual(r.status_code, 400)

    def test_edit_task_with_estimate(self):
        tid = self.new_task()
        r = self.post(f"/tasks/{tid}/edit", {"title": "Renamed task", "status": "in_progress", "priority": "low",
                                              "estimate": "1h 30m", "assignee_id": "", "due_date": TOMORROW})
        self.assertEqual(r.status_code, 302)
        t = self.store.task(tid)
        self.assertEqual((t["title"], t["status"], t["estimate_minutes"], t["assignee_id"]),
                         ("Renamed task", "in_progress", 90, None))
        self.assertIn('value="1h 30m"', self.html(f"/tasks/{tid}/edit"))

    def test_comment(self):
        tid = self.new_task()
        self.post(f"/tasks/{tid}/comment", {"body": "Looks good"})
        self.assertEqual(self.store.comments(tid)[0]["body"], "Looks good")

    def test_delete(self):
        tid = self.new_task()
        self.post(f"/tasks/{tid}/delete")
        self.assertIsNone(self.store.task(tid))

    def test_activity_is_logged(self):
        tid = self.new_task(title="Logged task")
        acts = self.store.activity(self.ws, 5)
        self.assertTrue(any(a["action"] == "task_created" and a["task_id"] == tid for a in acts))

    def test_csv_export(self):
        r = self.c.get("/reports/tasks.csv")
        self.assertEqual(r.mimetype, "text/csv")
        text = r.get_data(as_text=True)
        self.assertIn("Estimate (h)", text)
        self.assertIn("Build product catalogue", text)
        self.assertNotIn("Computational statistics", text)            # other workspace

    def test_calendar_and_board_show_only_this_workspace(self):
        board = self.html("/board")
        self.assertIn("Two-step fund transfer", board)
        self.assertNotIn("Computational statistics", board)

    def test_checklist(self):
        tid = self.new_task()
        self.post(f"/tasks/{tid}/checklist", {"title": "Step one"})
        sub = self.store.subtasks(tid)[0]
        r = self.post(f"/checklist/{sub['id']}/toggle", headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.get_json(), {"ok": True, "done": 1, "total": 1})
        self.post(f"/checklist/{sub['id']}/delete")
        self.assertEqual(self.store.subtasks(tid), [])


class TestPermissions(Base):
    def setUp(self):
        super().setUp()
        self.login("anna@taskflow.demo", "Demo@1234")
        self.ws = self.first_ws()
        admin = self.uid("admin@taskflow.demo")
        self.others = self.store.create_task(admin, self.ws, {"title": "Admin only task", "status": "todo",
                                                              "priority": "low", "assignee_id": str(admin)})

    def test_member_cannot_edit_others_task(self):
        self.assertEqual(self.c.get(f"/tasks/{self.others}/edit").status_code, 403)
        self.assertEqual(self.post(f"/tasks/{self.others}/status", {"status": "done"}).status_code, 403)
        self.assertEqual(self.post_json(f"/api/tasks/{self.others}/status", {"status": "done"}).status_code, 403)
        self.assertEqual(self.post(f"/tasks/{self.others}/delete").status_code, 403)

    def test_member_can_comment_and_attach_on_any_task_of_the_workspace(self):
        self.post(f"/tasks/{self.others}/comment", {"body": "Can I help?"})
        self.assertEqual(len(self.store.comments(self.others)), 1)

    def test_member_cannot_use_admin_actions(self):
        for url in ["/workspace/settings", "/admin", "/admin?tab=data"]:
            self.assertEqual(self.c.get(url).status_code, 403, url)
        self.assertEqual(self.post("/tasks/bulk", {"ids": self.others, "action": "delete"}).status_code, 403)
        self.assertEqual(self.post("/team/add", {"name": "X Y", "email": "x@teamtest.io",
                                                 "password": "Secret123"}).status_code, 403)

    def test_task_of_a_workspace_i_am_not_in_is_hidden(self):
        sfu = self.second_ws()
        t = self.store.tasks(sfu)[0]
        self.assertEqual(self.c.get(f"/tasks/{t['id']}").status_code, 404)
        self.assertEqual(self.post_json(f"/api/tasks/{t['id']}/status", {"status": "done"}).status_code, 404)
        self.assertEqual(self.post(f"/workspaces/{sfu}/open").status_code, 403)


class TestAdmin(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.ws = self.first_ws()

    def test_add_member_new_and_existing_account(self):
        self.post("/team/add", {"name": "Kofi Mensah", "email": "kofi@teamtest.io", "password": "Welcome123",
                                "role": "member"})
        kofi = self.uid("kofi@teamtest.io")
        self.assertEqual(self.store.member_role(self.ws, kofi), "member")
        li = self.uid("li@taskflow.demo")                       # already has an account (other workspace)
        self.post("/team/add", {"name": "", "email": "li@taskflow.demo", "password": "", "role": "admin"})
        self.assertEqual(self.store.member_role(self.ws, li), "admin")
        self.assertTrue(self.store.authenticate("li@taskflow.demo", "Demo@1234"))   # password not changed

    def test_admin_cannot_change_own_role(self):
        me = self.uid("admin@taskflow.demo")
        self.post(f"/team/{me}/role", {"role": "member"})
        self.assertEqual(self.store.member_role(self.ws, me), "admin")

    def test_site_admin_tools(self):
        anna = self.uid("anna@taskflow.demo")
        self.post(f"/team/{anna}/reset", {"password": "NewPass123"})
        self.assertTrue(self.store.authenticate("anna@taskflow.demo", "NewPass123"))
        self.post(f"/team/{anna}/toggle")
        self.assertFalse(self.store.user(anna)["active"])
        self.post(f"/team/{anna}/edit", {"name": "Anna P. Petrova", "email": "anna@taskflow.demo",
                                         "job_title": "Lead", "color": "#10b981"})
        self.assertEqual(self.store.user(anna)["name"], "Anna P. Petrova")

    def test_workspace_admin_who_is_not_site_admin(self):
        david = self.new_client("david@taskflow.demo")             # admin of the first workspace only
        self.assertEqual(david.get("/workspace/settings").status_code, 200)
        self.assertEqual(david.get("/admin").status_code, 403)
        anna = self.uid("anna@taskflow.demo")
        self.assertEqual(self.post(f"/team/{anna}/reset", {"password": "X1234567x"}, client=david).status_code, 403)
        self.post(f"/team/{anna}/role", {"role": "admin"}, client=david)
        self.assertEqual(self.store.member_role(self.ws, anna), "admin")

    def test_projects(self):
        self.post("/projects/save", {"name": "Marketing site", "description": "New pages", "color": "#10b981"})
        p = [p for p in self.store.projects(self.ws) if p["name"] == "Marketing site"][0]
        self.post(f"/projects/{p['id']}/archive")
        self.assertTrue(self.store.project(p["id"])["archived"])
        self.post(f"/projects/{p['id']}/delete", {"mode": "keep"})
        self.assertIsNone(self.store.project(p["id"]))
        self.assertEqual(self.store.project(p["id"]), None)

    def test_project_of_other_workspace_is_not_reachable(self):
        sfu_project = self.store.projects(self.second_ws())[0]
        r = self.post(f"/projects/{sfu_project['id']}/delete", {"mode": "tasks"})
        self.assertEqual(r.status_code, 404)

    def test_bulk_actions(self):
        ids = [t["id"] for t in self.store.tasks(self.ws, status="open")][:3]
        self.post("/tasks/bulk", {"ids": ids, "action": "priority", "value": "urgent", "next": "/tasks"})
        self.assertTrue(all(self.store.task(i)["priority"] == "urgent" for i in ids))

    def test_reports_numbers(self):
        s = self.store.stats(self.ws)
        self.assertEqual(s["total"], s["done"] + s["open"])
        html = self.html("/reports")
        self.assertIn("Burndown chart", html)
        self.assertIn("Time tracking", html)

    def test_admin_pages(self):
        for url in ["/admin", "/admin?tab=data&table=users", "/admin?tab=data&table=resumes", "/admin?tab=maintenance"]:
            html = self.html(url)
            self.assertNotIn("pbkdf2", html)
            self.assertNotIn("scrypt", html)

    def test_table_csv_hides_secrets(self):
        text = self.c.get("/admin/data/users.csv").get_data(as_text=True)
        self.assertNotIn("password_hash", text)
        self.assertNotIn("totp_secret", text)
        secret = self.store.secret_key()
        self.assertNotIn(secret, self.c.get("/admin/data/settings.csv").get_data(as_text=True))
        self.assertNotIn(secret, self.html("/admin?tab=data&table=settings"))

    def test_reset_demo(self):
        self.post("/admin/maintenance", {"action": "reset_demo", "confirm": "nope"})
        self.assertEqual(self.c.get("/").status_code, 200)
        self.post("/admin/maintenance", {"action": "reset_demo", "confirm": "RESET"})
        self.assertEqual(self.c.get("/").status_code, 302)
        self.assertEqual(self.store.count_users(), 10)


class TestOwnAccount(Base):
    def setUp(self):
        super().setUp()
        self.login("elena@taskflow.demo", "Demo@1234")

    def test_profile_language_and_password(self):
        self.post("/profile", {"form": "profile", "name": "Elena Smirnova", "email": "elena@taskflow.demo",
                               "job_title": "QA Lead", "color": "#ef4444", "lang": "fr"})
        u = self.store.user(self.uid("elena@taskflow.demo"))
        self.assertEqual((u["job_title"], u["lang"]), ("QA Lead", "fr"))
        self.post("/profile", {"form": "password", "old": "Demo@1234", "new": "Changed123", "confirm": "Changed123"})
        self.assertTrue(self.store.authenticate("elena@taskflow.demo", "Changed123"))

    def test_delete_own_account(self):
        uid = self.uid("elena@taskflow.demo")
        self.post("/profile", {"form": "delete", "password": "wrong"})
        self.assertIsNotNone(self.store.user(uid))
        self.post("/profile", {"form": "delete", "password": "Demo@1234"})
        self.assertIsNone(self.store.user(uid))

    def test_site_admin_cannot_delete_own_account(self):
        c = self.new_client("admin@taskflow.demo", "Admin@1234")
        self.post("/profile", {"form": "delete", "password": "Admin@1234"}, client=c)
        self.assertIsNotNone(self.store.user_by_email("admin@taskflow.demo"))


class TestDemoData(Base):
    def test_two_workspaces_with_cv_projects(self):
        mine = self.store.memberships(self.uid("admin@taskflow.demo"))
        self.assertEqual({w["name"] for w in mine}, {"Jean's Dev Studio", "SFU Data Analysis Group"})
        names = {p["name"] for p in self.store.projects(self.first_ws())}
        for p in ("Yo Delivery — E-commerce", "PyBank — Online Banking", "TaskFlow — Task Manager",
                  "CalcWise — Finance Calculator"):
            self.assertIn(p, names)

    def test_public_resume_has_no_phone_number(self):
        r = self.store.resume_by_slug("jean-michel-mujyambere")
        self.assertEqual(r["visibility"], "public")
        self.assertEqual(r["content"]["phone"], "")
        html = self.html("/r/jean-michel-mujyambere")
        self.assertNotIn("996", html)
        self.assertNotIn("+250", html)

    def test_languages_of_demo_people(self):
        self.assertEqual(self.store.user_by_email("fatima@taskflow.demo")["lang"], "ar")
        self.assertEqual(self.store.user_by_email("li@taskflow.demo")["lang"], "zh")


if __name__ == "__main__":
    unittest.main()
