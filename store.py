"""
store.py - database and business rules of TaskFlow.

The data is kept in SQLite on a computer, or in PostgreSQL when DATABASE_URL is set (online),
through the small db.py layer. Every function that changes data also writes a line to the
activity log; changes to tasks are also written as "events" that open boards receive live.

Several teams can use one TaskFlow: every team has its own workspace with its own projects,
tasks, members and roles. A person can be a member of several workspaces.

The Store class is built from parts (mixins) in other files:
    files.py      attachments on tasks, pictures
    timetrack.py  timers, logged time, estimates, burndown charts
    notices.py    notifications, email reminders, live events, password reset, two-factor login
    resumes.py    the resume builder
    planning.py   (v3) labels, sprints, dependencies, recurring tasks, Gantt, suggested estimates, search, trash
    admin_store.py (v3) site settings, every account / workspace / resume, interface words, restore, profiles
    integrations.py (v3) outbox, Slack and Telegram, Web Push, iCal feed
"""

import os
import secrets
import threading
from datetime import date, datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from core import TaskError, count_case, now, parse_due, parse_duration
from db import Database
from admin_store import AdminMixin
from files import FilesMixin
from integrations import IntegrationsMixin
from notices import NoticesMixin
from planning import REPEATS, PlanningMixin
from resumes import ResumesMixin
from timetrack import TimeMixin

STATUSES = [("todo", "To Do"), ("in_progress", "In Progress"), ("review", "In Review"), ("done", "Done")]
PRIORITIES = [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("urgent", "Urgent")]
STATUS_NAMES = dict(STATUSES)
PRIORITY_NAMES = dict(PRIORITIES)
PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
COLORS = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#14b8a6"]
MIN_PASSWORD = 8
ROLES = ("admin", "member")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    job_title TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#4f46e5',
    invite_code TEXT NOT NULL DEFAULT '',
    default_priority TEXT NOT NULL DEFAULT 'medium',
    created_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, user_id)
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo',
    priority TEXT NOT NULL DEFAULT 'medium',
    assignee_id INTEGER REFERENCES users(id),
    created_by INTEGER NOT NULL REFERENCES users(id),
    due_date TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    edited_at TEXT
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    task_id INTEGER,
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subtasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL,
    user_id INTEGER REFERENCES users(id),
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS time_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    minutes INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id INTEGER,
    task_id INTEGER,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0,
    emailed TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER,
    to_user INTEGER,
    user_id INTEGER,
    kind TEXT NOT NULL,
    task_id INTEGER,
    text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS resumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    theme TEXT NOT NULL DEFAULT 'modern',
    accent TEXT NOT NULL DEFAULT '#4f46e5',
    font TEXT NOT NULL DEFAULT 'inter',
    lang TEXT NOT NULL DEFAULT 'en',
    visibility TEXT NOT NULL DEFAULT 'private',
    data TEXT NOT NULL,
    photo TEXT NOT NULL DEFAULT '',
    views INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_labels (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    label_id INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, label_id)
);
CREATE TABLE IF NOT EXISTS sprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS task_deps (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    blocked_by INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, blocked_by)
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'waiting',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_subs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ui_overrides (
    lang TEXT NOT NULL,
    english TEXT NOT NULL,
    text TEXT NOT NULL,
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (lang, english)
);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id);
CREATE INDEX IF NOT EXISTS idx_labels_ws ON labels(workspace_id);
CREATE INDEX IF NOT EXISTS idx_sprints_ws ON sprints(workspace_id);
CREATE INDEX IF NOT EXISTS idx_push_user ON push_subs(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_activity_time ON activity(created_at);
CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_time_task ON time_entries(task_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_events_ws ON events(workspace_id);
CREATE INDEX IF NOT EXISTS idx_attachments_task ON attachments(task_id);
"""

# columns added in version 2 (also added to databases made by version 1)
MIGRATIONS = {
    "users": [("lang", "TEXT NOT NULL DEFAULT 'en'"), ("notify_email", "INTEGER NOT NULL DEFAULT 1"),
              ("remind_days", "INTEGER NOT NULL DEFAULT 1"), ("totp_secret", "TEXT NOT NULL DEFAULT ''"),
              ("totp_enabled", "INTEGER NOT NULL DEFAULT 0"), ("totp_last_step", "INTEGER NOT NULL DEFAULT 0"),
              ("last_ws", "INTEGER"),
              # version 3
              ("phone", "TEXT NOT NULL DEFAULT ''"), ("bio", "TEXT NOT NULL DEFAULT ''"),
              ("photo", "TEXT NOT NULL DEFAULT ''"), ("session_ver", "INTEGER NOT NULL DEFAULT 1"),
              ("ical_token", "TEXT NOT NULL DEFAULT ''"), ("notify_push", "INTEGER NOT NULL DEFAULT 1")],
    "projects": [("workspace_id", "INTEGER")],
    "tasks": [("workspace_id", "INTEGER"), ("estimate_minutes", "INTEGER"), ("reminded_for", "TEXT NOT NULL DEFAULT ''"),
              # version 3
              ("start_date", "TEXT"), ("sprint_id", "INTEGER"), ("repeat", "TEXT NOT NULL DEFAULT ''"),
              ("repeat_parent", "INTEGER"), ("deleted_at", "TEXT"), ("deleted_by", "INTEGER")],
    "workspaces": [("slack_url", "TEXT NOT NULL DEFAULT ''"), ("telegram_chat", "TEXT NOT NULL DEFAULT ''")],
    "activity": [("workspace_id", "INTEGER")],
    "comments": [("edited_at", "TEXT")],
}

# activity that open boards receive live, and how the message reads ({0} person, {1} task, {2} detail)
LIVE = {
    "task_created": "{0} created “{1}”",
    "task_updated": "{0} updated “{1}”",
    "status_changed": "{0} moved “{1}” to {2}",
    "task_completed": "{0} moved “{1}” to {2}",
    "task_deleted": "{0} deleted “{1}”",
    "commented": "{0} commented on “{1}”",
    "comment_edited": "{0} commented on “{1}”",
    "comment_deleted": "{0} updated “{1}”",
    "checklist_added": "{0} updated the checklist of “{1}”",
    "checklist_done": "{0} updated the checklist of “{1}”",
    "checklist_removed": "{0} updated the checklist of “{1}”",
    "attachment_added": "{0} attached a file to “{1}”",
    "attachment_deleted": "{0} removed a file from “{1}”",
    "time_logged": "{0} logged time on “{1}”",
    "bulk_update": "{0} changed several tasks",
    "project_created": "{0} changed the projects",
    "project_updated": "{0} changed the projects",
    "project_deleted": "{0} changed the projects",
    "project_archived": "{0} changed the projects",
    "project_restored": "{0} changed the projects",
    "task_restored": "{0} restored “{1}”",
    "sprint_saved": "{0} changed the sprints",
    "sprint_active": "{0} started the sprint “{1}”",
    "sprint_closed": "{0} closed the sprint “{1}”",
    "sprint_deleted": "{0} changed the sprints",
    "labels_changed": "{0} changed the labels",
}

# one Condition per process: an open WebSocket waits on it, so a change made in the same process
# arrives at once; changes made by other processes (or other servers) are found by reading the events table.
LIVE_SIGNAL = threading.Condition()


def _wake_listeners():
    with LIVE_SIGNAL:
        LIVE_SIGNAL.notify_all()


class Store(FilesMixin, TimeMixin, NoticesMixin, ResumesMixin, PlanningMixin, AdminMixin, IntegrationsMixin):
    def __init__(self, target, driver=None, notifier=None, env=None):
        """target: path of an SQLite file, or a postgresql:// URL."""
        self.env = os.environ if env is None else env
        self.database = Database(target, driver=driver)
        self.database.init_schema(SCHEMA)
        self.is_postgres = self.database.is_postgres
        with self.db() as con:
            for table, cols in MIGRATIONS.items():
                have = set(con.columns(table))
                for col, sql in cols:
                    if col not in have:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sql}")
        self._adopt_version1_data()
        if notifier is None:
            from notify import Notifier
            notifier = Notifier(env=self.env)
        self.notifier = notifier
        self.http = None                                  # tests put a fake HTTP opener here

    def db(self):
        return self.database.connect()

    def describe_database(self):
        return self.database.describe()

    def _adopt_version1_data(self):
        """A database from version 1 had one team and no workspaces: put everything into one workspace."""
        with self.db() as con:
            if con.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]:
                return
            users = con.execute("SELECT id, role FROM users ORDER BY id").fetchall()
            if not users:
                return
            row = con.execute("SELECT value FROM settings WHERE key = 'workspace_name'").fetchone()
            ws = con.execute("INSERT INTO workspaces (name, invite_code, created_by, created_at) VALUES (?,?,?,?)",
                             (row[0] if row else "TaskFlow", secrets.token_urlsafe(9), users[0][0], now())).lastrowid
            for uid, role in users:
                con.execute("INSERT INTO memberships (workspace_id, user_id, role, created_at) VALUES (?,?,?,?)",
                            (ws, uid, role if role in ROLES else "member", now()))
            for table in ("projects", "tasks", "activity"):
                con.execute(f"UPDATE {table} SET workspace_id = ? WHERE workspace_id IS NULL", (ws,))

    # ================================================================ activity + live events
    def log(self, con, user_id, action, details="", task_id=None, ws=None, title=None):
        con.execute("INSERT INTO activity (user_id, task_id, action, details, created_at, workspace_id) "
                    "VALUES (?,?,?,?,?,?)", (user_id, task_id, action, details, now(), ws))
        if ws and action in LIVE:
            name = ""
            if user_id:
                r = con.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
                name = r[0] if r else ""
            if title is None and task_id:
                r = con.execute("SELECT title FROM tasks WHERE id = ?", (task_id,)).fetchone()
                title = r[0] if r else details
            extra = details.split("→")[-1].strip() if "→" in details else ""
            text = LIVE[action].replace("{0}", name or "Someone").replace("{1}", title or details or "") \
                .replace("{2}", extra)
            con.execute("INSERT INTO events (workspace_id, user_id, kind, task_id, text, created_at) VALUES (?,?,?,?,?,?)",
                        (ws, user_id, action, task_id, text, now()))
            con.after_commit.append(_wake_listeners)
            if action in ("task_created", "task_completed"):
                self.chat_post(con, ws, action, text)

    # ================================================================ users
    def count_users(self):
        with self.db() as con:
            return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, name, email, password, role=None, job_title="", by=None, lang="en"):
        """A new account. The very first account is the site administrator."""
        name, email = " ".join((name or "").split()), (email or "").strip().lower()
        if len(name) < 2:
            raise TaskError("Please enter your full name.")
        if len(name) > 80:
            raise TaskError("The name is too long.")
        if "@" not in email or "." not in email.split("@")[-1] or len(email) > 120:
            raise TaskError("Please enter a valid email address.")
        if len(password or "") < MIN_PASSWORD:
            raise TaskError(f"Password must have at least {MIN_PASSWORD} characters.")
        from world import LANGUAGES
        with self.db() as con:
            if con.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise TaskError("An account with this email already exists.")
            count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            role = "admin" if count == 0 else (role if role in ROLES else "member")
            cur = con.execute(
                "INSERT INTO users (name, email, password_hash, role, job_title, color, created_at, lang) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (name, email, generate_password_hash(password), role, (job_title or "").strip()[:80],
                 COLORS[count % len(COLORS)], now(), lang if lang in LANGUAGES else "en"))
            self.log(con, by or cur.lastrowid, "user_added", f"{name} joined")
            return cur.lastrowid

    def register(self, name, email, password, job_title="", lang="en", invite=""):
        """Sign-up: join the workspace of the invite link, or get an own new workspace."""
        ws = self.workspace_by_invite(invite) if invite else None
        if invite and not ws:
            raise TaskError("This invite link is not valid any more. Ask for a new one.")
        uid = self.create_user(name, email, password, job_title=job_title, lang=lang)
        if ws:
            self.add_member(ws["id"], uid, "member", by=uid)
            return uid, ws["id"]
        first = " ".join(name.split()).split()[0]
        return uid, self.create_workspace(uid, f"{first}'s workspace")

    def authenticate(self, email, password):
        with self.db() as con:
            u = con.execute("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)).fetchone()
            if not u or not check_password_hash(u["password_hash"], password or ""):
                return None
            if not u["active"]:
                raise TaskError("This account is deactivated. Please contact your admin.")
            self.log(con, u["id"], "logged_in")
            return dict(u)

    def user(self, user_id):
        with self.db() as con:
            r = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return dict(r) if r else None

    def user_by_email(self, email):
        with self.db() as con:
            r = con.execute("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)).fetchone()
            return dict(r) if r else None

    def users(self, ws, active_only=False):
        """People of one workspace, with their role there."""
        sql = ("SELECT u.*, m.role AS ws_role FROM users u JOIN memberships m ON m.user_id = u.id "
               "WHERE m.workspace_id = ?" + (" AND u.active = 1" if active_only else "") + " ORDER BY lower(u.name)")
        with self.db() as con:
            return [dict(r) for r in con.execute(sql, (ws,))]

    def all_users(self):
        with self.db() as con:
            return [dict(r) for r in con.execute("SELECT * FROM users ORDER BY lower(name)")]

    def update_profile(self, user_id, name, email, job_title, color=None, lang=None):
        name, email = " ".join((name or "").split()), (email or "").strip().lower()
        if len(name) < 2:
            raise TaskError("Please enter your full name.")
        if "@" not in email or "." not in email.split("@")[-1]:
            raise TaskError("Please enter a valid email address.")
        from world import LANGUAGES
        with self.db() as con:
            if con.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, user_id)).fetchone():
                raise TaskError("This email is used by another account.")
            con.execute("UPDATE users SET name = ?, email = ?, job_title = ? WHERE id = ?",
                        (name[:80], email, (job_title or "").strip()[:80], user_id))
            if color in COLORS:
                con.execute("UPDATE users SET color = ? WHERE id = ?", (color, user_id))
            if lang in LANGUAGES:
                con.execute("UPDATE users SET lang = ? WHERE id = ?", (lang, user_id))
            self.log(con, user_id, "profile_updated")

    def set_language(self, user_id, lang):
        from world import LANGUAGES
        if lang in LANGUAGES:
            with self.db() as con:
                con.execute("UPDATE users SET lang = ? WHERE id = ?", (lang, user_id))

    def change_password(self, user_id, old, new, confirm):
        u = self.user(user_id)
        if not check_password_hash(u["password_hash"], old or ""):
            raise TaskError("Your current password is wrong.")
        if new != confirm:
            raise TaskError("New passwords do not match.")
        if len(new or "") < MIN_PASSWORD:
            raise TaskError(f"Password must have at least {MIN_PASSWORD} characters.")
        with self.db() as con:
            con.execute("UPDATE users SET password_hash = ?, session_ver = session_ver + 1 WHERE id = ?",
                        (generate_password_hash(new), user_id))
            self.log(con, user_id, "password_changed")

    def admin_toggle_active(self, admin_id, user_id):
        if admin_id == user_id:
            raise TaskError("You cannot deactivate yourself.")
        with self.db() as con:
            u = con.execute("SELECT name, active FROM users WHERE id = ?", (user_id,)).fetchone()
            con.execute("UPDATE users SET active = ? WHERE id = ?", (0 if u["active"] else 1, user_id))
            self.log(con, admin_id, "user_deactivated" if u["active"] else "user_activated", u["name"])

    def admin_reset_password(self, admin_id, user_id, new):
        if len(new or "") < MIN_PASSWORD:
            raise TaskError(f"Password must have at least {MIN_PASSWORD} characters.")
        with self.db() as con:
            con.execute("UPDATE users SET password_hash = ?, session_ver = session_ver + 1 WHERE id = ?",
                        (generate_password_hash(new), user_id))
            name = con.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()["name"]
            self.log(con, admin_id, "password_reset", name)

    def admin_update_user(self, admin_id, user_id, name, email, job_title, color=None):
        name, email = " ".join((name or "").split()), (email or "").strip().lower()
        if len(name) < 2:
            raise TaskError("Please enter a full name.")
        if "@" not in email or "." not in email.split("@")[-1]:
            raise TaskError("Please enter a valid email address.")
        with self.db() as con:
            if con.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, user_id)).fetchone():
                raise TaskError("This email is used by another account.")
            old = con.execute("SELECT color FROM users WHERE id = ?", (user_id,)).fetchone()
            con.execute("UPDATE users SET name = ?, email = ?, job_title = ?, color = ? WHERE id = ?",
                        (name[:80], email, (job_title or "").strip()[:80], color if color in COLORS else old["color"],
                         user_id))
            self.log(con, admin_id, "user_updated", name)

    def delete_own_account(self, user_id, password):
        u = self.user(user_id)
        if not check_password_hash(u["password_hash"], password or ""):
            raise TaskError("Password is wrong - your account was not deleted.")
        if u["role"] == "admin":
            raise TaskError("The site administrator cannot delete this account.")
        for m in self.memberships(user_id):
            if m["role"] == "admin" and self._admin_count(m["id"]) == 1 and self._member_count(m["id"]) > 1:
                raise TaskError(f"You are the only admin of “{m['name']}”. Make someone else admin first.")
        with self.db() as con:
            admin = con.execute("SELECT id FROM users WHERE role = 'admin' AND active = 1 ORDER BY id").fetchone()
        self._remove_user(admin["id"], user_id, "account_closed")

    def delete_user(self, admin_id, user_id):
        """Remove an account. Their tasks become unassigned, tasks they created move to the admin."""
        if admin_id == user_id:
            raise TaskError("You cannot delete your own account.")
        self._remove_user(admin_id, user_id, "user_deleted")

    def _remove_user(self, admin_id, user_id, action):
        with self.db() as con:
            u = con.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
            if not u:
                raise TaskError("User not found.")
            con.execute("UPDATE tasks SET assignee_id = NULL WHERE assignee_id = ?", (user_id,))
            con.execute("UPDATE tasks SET created_by = ? WHERE created_by = ?", (admin_id, user_id))
            con.execute("UPDATE projects SET created_by = ? WHERE created_by = ?", (admin_id, user_id))
            con.execute("DELETE FROM comments WHERE user_id = ?", (user_id,))
            con.execute("UPDATE activity SET user_id = NULL WHERE user_id = ?", (user_id,))
            con.execute("UPDATE subtasks SET created_by = NULL WHERE created_by = ?", (user_id,))
            con.execute("UPDATE attachments SET user_id = NULL WHERE user_id = ?", (user_id,))
            con.execute("UPDATE tasks SET deleted_by = NULL WHERE deleted_by = ?", (user_id,))
            con.execute("UPDATE sprints SET created_by = NULL WHERE created_by = ?", (user_id,))
            for table in ("memberships", "time_entries", "notifications", "password_resets", "resumes", "push_subs"):
                con.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            # workspaces with nobody left are removed with everything in them
            for (ws,) in con.execute("SELECT id FROM workspaces w WHERE NOT EXISTS "
                                     "(SELECT 1 FROM memberships m WHERE m.workspace_id = w.id)").fetchall():
                self._delete_workspace_rows(con, ws)
            con.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self.log(con, None if action == "account_closed" else admin_id, action, u["name"])

    # ================================================================ site settings (whole TaskFlow)
    DEFAULT_SETTINGS = {"allow_registration": "1", "site_name": "TaskFlow"}

    def settings(self):
        with self.db() as con:
            saved = {r[0]: r[1] for r in con.execute("SELECT key, value FROM settings").fetchall()}
        return {**self.DEFAULT_SETTINGS, **saved}

    def _put_setting(self, con, key, value):
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))

    def save_site_settings(self, admin_id, allow_registration):            # version 2 (kept for old forms)
        with self.db() as con:
            self._put_setting(con, "allow_registration", "1" if allow_registration else "0")
            self.log(con, admin_id, "settings_updated")

    def secret_key(self):
        """The key that signs login cookies. Kept in the database, so logins survive a restart of the server."""
        with self.db() as con:
            r = con.execute("SELECT value FROM settings WHERE key = 'secret_key'").fetchone()
            if r:
                return r[0]
            key = secrets.token_hex(32)
            con.execute("INSERT INTO settings (key, value) VALUES ('secret_key', ?) ON CONFLICT (key) DO NOTHING", (key,))
            return con.execute("SELECT value FROM settings WHERE key = 'secret_key'").fetchone()[0]

    # ================================================================ workspaces
    def create_workspace(self, user_id, name, description="", color=None):
        name = " ".join((name or "").split())
        if not 2 <= len(name) <= 40:
            raise TaskError("Workspace name must have 2-40 characters.")
        with self.db() as con:
            ws = con.execute("INSERT INTO workspaces (name, description, color, invite_code, created_by, created_at) "
                             "VALUES (?,?,?,?,?,?)", (name, (description or "").strip()[:200],
                                                      color if color in COLORS else COLORS[0],
                                                      secrets.token_urlsafe(9), user_id, now())).lastrowid
            con.execute("INSERT INTO memberships (workspace_id, user_id, role, created_at) VALUES (?,?,?,?)",
                        (ws, user_id, "admin", now()))
            con.execute("UPDATE users SET last_ws = ? WHERE id = ?", (ws, user_id))
            self.log(con, user_id, "workspace_created", name, ws=ws)
            return ws

    def workspace(self, ws):
        with self.db() as con:
            r = con.execute("SELECT * FROM workspaces WHERE id = ?", (ws,)).fetchone()
            return dict(r) if r else None

    def workspace_by_invite(self, code):
        code = (code or "").strip()
        if len(code) < 6:
            return None
        with self.db() as con:
            r = con.execute("SELECT * FROM workspaces WHERE invite_code = ?", (code,)).fetchone()
            return dict(r) if r else None

    def memberships(self, user_id):
        """Workspaces of a person, with role, number of members and open tasks."""
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT w.*, m.role, "
                "(SELECT COUNT(*) FROM memberships x WHERE x.workspace_id = w.id) AS members, "
                "(SELECT COUNT(*) FROM tasks t WHERE t.workspace_id = w.id AND t.status != 'done') AS open_tasks "
                "FROM workspaces w JOIN memberships m ON m.workspace_id = w.id WHERE m.user_id = ? "
                "ORDER BY lower(w.name)", (user_id,))]

    def member_role(self, ws, user_id):
        with self.db() as con:
            r = con.execute("SELECT role FROM memberships WHERE workspace_id = ? AND user_id = ?",
                            (ws, user_id)).fetchone()
            return r[0] if r else None

    def choose_workspace(self, user_id, wanted=None):
        """The workspace to show: the wanted one if the person is a member, else the last used, else any."""
        mine = self.memberships(user_id)
        if not mine:
            u = self.user(user_id)
            return self.create_workspace(user_id, f"{u['name'].split()[0]}'s workspace")
        ids = [w["id"] for w in mine]
        if wanted in ids:
            return wanted
        last = (self.user(user_id) or {}).get("last_ws")
        return last if last in ids else ids[0]

    def remember_workspace(self, user_id, ws):
        with self.db() as con:
            con.execute("UPDATE users SET last_ws = ? WHERE id = ?", (ws, user_id))

    def save_workspace(self, admin_id, ws, name, description, color, default_priority):
        name = " ".join((name or "").split())
        if not 2 <= len(name) <= 40:
            raise TaskError("Workspace name must have 2-40 characters.")
        if default_priority not in PRIORITY_NAMES:
            raise TaskError("Unknown priority.")
        with self.db() as con:
            con.execute("UPDATE workspaces SET name = ?, description = ?, color = ?, default_priority = ? WHERE id = ?",
                        (name, (description or "").strip()[:200], color if color in COLORS else COLORS[0],
                         default_priority, ws))
            self.log(con, admin_id, "settings_updated", name, ws=ws)

    def new_invite_code(self, admin_id, ws):
        code = secrets.token_urlsafe(9)
        with self.db() as con:
            con.execute("UPDATE workspaces SET invite_code = ? WHERE id = ?", (code, ws))
            self.log(con, admin_id, "invite_renewed", ws=ws)
        return code

    def _admin_count(self, ws):
        with self.db() as con:
            return con.execute("SELECT COUNT(*) FROM memberships WHERE workspace_id = ? AND role = 'admin'",
                               (ws,)).fetchone()[0]

    def _member_count(self, ws):
        with self.db() as con:
            return con.execute("SELECT COUNT(*) FROM memberships WHERE workspace_id = ?", (ws,)).fetchone()[0]

    def add_member(self, ws, user_id, role="member", by=None):
        role = role if role in ROLES else "member"
        with self.db() as con:
            if con.execute("SELECT 1 FROM memberships WHERE workspace_id = ? AND user_id = ?",
                           (ws, user_id)).fetchone():
                raise TaskError("This person is already a member of the workspace.")
            con.execute("INSERT INTO memberships (workspace_id, user_id, role, created_at) VALUES (?,?,?,?)",
                        (ws, user_id, role, now()))
            name = con.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()[0]
            wname = con.execute("SELECT name FROM workspaces WHERE id = ?", (ws,)).fetchone()[0]
            self.log(con, by or user_id, "user_added", f"{name} joined as {role}", ws=ws)
            if by and by != user_id:
                self.notify(con, user_id, "added", f"You were added to the workspace “{wname}”.", ws=ws)

    def invite_member(self, admin_id, ws, name, email, password, role="member", job_title=""):
        """Admin adds a person: an existing account joins, or a new account is made with a first password."""
        existing = self.user_by_email(email)
        if existing:
            self.add_member(ws, existing["id"], role, by=admin_id)
            return existing["id"], False
        uid = self.create_user(name, email, password, role="member", job_title=job_title, by=admin_id)
        self.add_member(ws, uid, role, by=admin_id)
        return uid, True

    def set_member_role(self, admin_id, ws, user_id, role):
        if admin_id == user_id:
            raise TaskError("You cannot change your own role.")
        if role not in ROLES:
            raise TaskError("Unknown role.")
        with self.db() as con:
            con.execute("UPDATE memberships SET role = ? WHERE workspace_id = ? AND user_id = ?", (role, ws, user_id))
            name = con.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()["name"]
            self.log(con, admin_id, "role_changed", f"{name} is now {role}", ws=ws)

    def remove_member(self, admin_id, ws, user_id):
        """Take a person out of a workspace (the account stays). Their tasks there become unassigned."""
        role = self.member_role(ws, user_id)
        if role is None:
            raise TaskError("This person is not a member of the workspace.")
        if role == "admin" and self._admin_count(ws) == 1:
            raise TaskError("The last admin cannot leave. Make someone else admin first.")
        with self.db() as con:
            con.execute("DELETE FROM memberships WHERE workspace_id = ? AND user_id = ?", (ws, user_id))
            con.execute("UPDATE tasks SET assignee_id = NULL WHERE workspace_id = ? AND assignee_id = ?", (ws, user_id))
            name = con.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()["name"]
            self.log(con, admin_id, "member_removed" if admin_id != user_id else "member_left", name, ws=ws)

    def delete_workspace(self, admin_id, ws, confirm_name):
        w = self.workspace(ws)
        if not w:
            raise TaskError("Workspace not found.")
        if (confirm_name or "").strip() != w["name"]:
            raise TaskError("Type the name of the workspace to confirm.")
        mine = self.memberships(admin_id)
        if len(mine) <= 1:
            raise TaskError("This is your only workspace. Create another one first.")
        with self.db() as con:
            self._delete_workspace_rows(con, ws)
            self.log(con, admin_id, "workspace_deleted", w["name"])

    def _delete_workspace_rows(self, con, ws):
        con.execute("DELETE FROM tasks WHERE workspace_id IN (?, ?)", (ws, -ws))  # comments, files, time, labels go too
        con.execute("DELETE FROM sprints WHERE workspace_id = ?", (ws,))
        con.execute("DELETE FROM labels WHERE workspace_id = ?", (ws,))
        con.execute("DELETE FROM projects WHERE workspace_id = ?", (ws,))
        con.execute("DELETE FROM events WHERE workspace_id = ?", (ws,))
        con.execute("DELETE FROM notifications WHERE workspace_id = ?", (ws,))
        con.execute("UPDATE activity SET workspace_id = NULL WHERE workspace_id = ?", (ws,))
        con.execute("UPDATE users SET last_ws = NULL WHERE last_ws = ?", (ws,))
        con.execute("DELETE FROM workspaces WHERE id = ?", (ws,))

    # ================================================================ data tools (site administrator)
    TABLES = ["users", "workspaces", "memberships", "projects", "sprints", "labels", "tasks", "task_labels", "task_deps",
              "subtasks", "comments", "attachments", "time_entries", "notifications", "events", "activity", "resumes",
              "outbox", "push_subs", "ui_overrides", "settings"]
    HIDDEN_COLUMNS = {"password_hash", "totp_secret", "data", "photo", "invite_code", "ical_token", "p256dh", "auth",
                      "endpoint", "slack_url"}

    def table_rows(self, table, limit=500):
        if table not in self.TABLES:
            raise TaskError("Unknown table.")
        order = {"settings": "key", "task_labels": "task_id", "task_deps": "task_id",
                 "ui_overrides": "updated_at"}.get(table, "id")
        with self.db() as con:
            cur = con.execute(f"SELECT * FROM {table} ORDER BY {order} DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
            cols_all = [c[0] for c in cur.description]
            cols = [c for c in cols_all if c not in self.HIDDEN_COLUMNS
                    and not (table == "settings" and c == "value" and False)]
            out = []
            for r in rows:
                d = dict(zip(cols_all, r))
                if table == "settings" and d.get("key") in ("secret_key", "vapid", "logo"):
                    d["value"] = "••••••"
                out.append([d[c] for c in cols])
            total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return cols, out, total

    def table_counts(self):
        with self.db() as con:
            return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in self.TABLES}

    def clear_activity(self, admin_id, older_than_days=0):
        cutoff = (datetime.now() - timedelta(days=older_than_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as con:
            n = con.execute("DELETE FROM activity WHERE created_at <= ?", (cutoff,)).rowcount
            con.execute("DELETE FROM events WHERE created_at <= ?", (cutoff,))
            self.log(con, admin_id, "log_cleared", f"{n} entries removed")
            return n

    def delete_completed_tasks(self, admin_id, ws, older_than_days=30):
        cutoff = (datetime.now() - timedelta(days=older_than_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as con:
            ids = [r[0] for r in con.execute("SELECT id FROM tasks WHERE workspace_id = ? AND status = 'done' "
                                             "AND completed_at <= ?", (ws, cutoff))]
            for tid in ids:
                self._to_trash(con, admin_id, ws, tid)
            n = len(ids)
            self.log(con, admin_id, "tasks_cleaned", f"{n} old completed tasks removed", ws=ws)
            return n

    def wipe(self):
        """Empty every table except the login key (used by 'reset demo data')."""
        with self.db() as con:
            for t in ["password_resets", "events", "notifications", "time_entries", "attachments", "subtasks",
                      "comments", "activity", "task_labels", "task_deps", "tasks", "sprints", "labels", "projects",
                      "memberships", "workspaces", "resumes", "outbox", "push_subs", "ui_overrides", "users"]:
                con.execute(f"DELETE FROM {t}")
            con.execute("DELETE FROM settings WHERE key NOT IN ('secret_key', 'vapid')")

    # ================================================================ projects
    def projects(self, ws, include_archived=False):
        sql = (f"SELECT p.*, o.name AS owner_name, "
               f"(SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id) AS total, "
               f"(SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'done') AS done "
               f"FROM projects p LEFT JOIN users o ON o.id = p.created_by WHERE p.workspace_id = ? "
               f"{'' if include_archived else 'AND p.archived = 0'} ORDER BY p.archived, lower(p.name)")
        with self.db() as con:
            out = []
            for r in con.execute(sql, (ws,)):
                d = dict(r)
                d["progress"] = round(d["done"] / d["total"] * 100) if d["total"] else 0
                out.append(d)
            return out

    def project(self, project_id, ws=None):
        with self.db() as con:
            r = con.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not r or (ws is not None and r["workspace_id"] != ws):
            return None
        return dict(r)

    def can_manage_project(self, user, project):
        return user["role"] == "admin" or project.get("created_by") == user["id"]

    def save_project(self, user_id, ws, name, description, color, project_id=None):
        name = " ".join((name or "").split())
        if len(name) < 2:
            raise TaskError("Project name is too short.")
        if len(name) > 60:
            raise TaskError("Project name is too long.")
        color = color if color in COLORS else COLORS[0]
        with self.db() as con:
            if project_id:
                con.execute("UPDATE projects SET name = ?, description = ?, color = ? WHERE id = ? AND workspace_id = ?",
                            (name, (description or "").strip()[:500], color, project_id, ws))
                self.log(con, user_id, "project_updated", name, ws=ws)
                return project_id
            cur = con.execute("INSERT INTO projects (name, description, color, created_by, created_at, workspace_id) "
                              "VALUES (?,?,?,?,?,?)", (name, (description or "").strip()[:500], color, user_id, now(), ws))
            self.log(con, user_id, "project_created", name, ws=ws)
            return cur.lastrowid

    def delete_project(self, user_id, ws, project_id, delete_tasks=False):
        with self.db() as con:
            p = con.execute("SELECT name FROM projects WHERE id = ? AND workspace_id = ?", (project_id, ws)).fetchone()
            if not p:
                raise TaskError("Project not found.")
            if delete_tasks:
                n = con.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,)).rowcount
                detail = f"{p['name']} and {n} tasks"
            else:
                con.execute("UPDATE tasks SET project_id = NULL WHERE project_id = ?", (project_id,))
                detail = f"{p['name']} (tasks kept without project)"
            con.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            self.log(con, user_id, "project_deleted", detail, ws=ws)

    def toggle_archive(self, user_id, ws, project_id):
        with self.db() as con:
            p = con.execute("SELECT name, archived FROM projects WHERE id = ? AND workspace_id = ?",
                            (project_id, ws)).fetchone()
            if not p:
                raise TaskError("Project not found.")
            con.execute("UPDATE projects SET archived = ? WHERE id = ?", (0 if p["archived"] else 1, project_id))
            self.log(con, user_id, "project_restored" if p["archived"] else "project_archived", p["name"], ws=ws)

    # ================================================================ tasks
    TASK_SELECT = """SELECT t.*, u.name AS assignee_name, u.color AS assignee_color,
                            c.name AS creator_name, p.name AS project_name, p.color AS project_color,
                            (SELECT COUNT(*) FROM comments WHERE task_id = t.id) AS comment_count,
                            (SELECT COUNT(*) FROM subtasks WHERE task_id = t.id) AS sub_total,
                            (SELECT COUNT(*) FROM subtasks WHERE task_id = t.id AND done = 1) AS sub_done,
                            (SELECT COUNT(*) FROM attachments WHERE task_id = t.id) AS file_count,
                            (SELECT COALESCE(SUM(minutes), 0) FROM time_entries WHERE task_id = t.id) AS logged_minutes
                     FROM tasks t
                     LEFT JOIN users u ON u.id = t.assignee_id
                     LEFT JOIN users c ON c.id = t.created_by
                     LEFT JOIN projects p ON p.id = t.project_id"""

    def _check_task_fields(self, con, ws, f):
        title = " ".join((f.get("title") or "").split())
        if len(title) < 3:
            raise TaskError("Task title must have at least 3 characters.")
        if len(title) > 120:
            raise TaskError("Task title is too long (max 120 characters).")
        status = f.get("status") or "todo"
        priority = f.get("priority") or "medium"
        if status not in STATUS_NAMES or priority not in PRIORITY_NAMES:
            raise TaskError("Unknown status or priority.")
        assignee = int(f["assignee_id"]) if str(f.get("assignee_id") or "").isdigit() else None
        if assignee and not con.execute(
                "SELECT 1 FROM users u JOIN memberships m ON m.user_id = u.id "
                "WHERE u.id = ? AND u.active = 1 AND m.workspace_id = ?", (assignee, ws)).fetchone():
            raise TaskError("The selected person does not exist.")
        project = int(f["project_id"]) if str(f.get("project_id") or "").isdigit() else None
        if project and not con.execute("SELECT 1 FROM projects WHERE id = ? AND workspace_id = ?",
                                       (project, ws)).fetchone():
            raise TaskError("The selected project does not exist.")
        description = (f.get("description") or "").strip()
        if len(description) > 5000:
            raise TaskError("The description is too long.")
        due = parse_due(f.get("due_date"))
        start = parse_due(f.get("start_date")) if f.get("start_date") else None
        if start and due and start > due:
            raise TaskError("The start date is after the due date.")
        sprint = int(f["sprint_id"]) if str(f.get("sprint_id") or "").isdigit() else None
        if sprint and not con.execute("SELECT 1 FROM sprints WHERE id = ? AND workspace_id = ? AND status != 'closed'",
                                      (sprint, ws)).fetchone():
            raise TaskError("Choose a sprint that is not closed.")
        repeat = f.get("repeat") or ""
        if repeat not in REPEATS:
            raise TaskError("Unknown repeat.")
        if repeat and not due:
            raise TaskError("A repeating task needs a due date.")
        return dict(title=title, description=description, status=status, priority=priority, assignee_id=assignee,
                    project_id=project, due_date=due, estimate_minutes=parse_duration(f.get("estimate")),
                    start_date=start, sprint_id=sprint, repeat=repeat)

    def create_task(self, user_id, ws, fields):
        with self.db() as con:
            f = self._check_task_fields(con, ws, fields)
            if f["due_date"] and f["due_date"] < date.today().isoformat() and not fields.get("_import"):
                raise TaskError("Due date cannot be in the past.")
            ts = now()
            cur = con.execute(
                "INSERT INTO tasks (project_id, title, description, status, priority, assignee_id, created_by, "
                "due_date, created_at, updated_at, completed_at, workspace_id, estimate_minutes, start_date, sprint_id, "
                "repeat) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f["project_id"], f["title"], f["description"], f["status"], f["priority"], f["assignee_id"],
                 user_id, f["due_date"], ts, ts, ts if f["status"] == "done" else None, ws, f["estimate_minutes"],
                 f["start_date"], f["sprint_id"], f["repeat"]))
            tid = cur.lastrowid
            if "labels" in fields:
                self.set_task_labels(con, ws, tid, fields.get("labels"))
            self.log(con, user_id, "task_created", f["title"], tid, ws=ws, title=f["title"])
            if f["assignee_id"] and f["assignee_id"] != user_id:
                self._notify_assigned(con, user_id, f["assignee_id"], tid, f["title"], ws)
            self.notify_mentions(con, user_id, ws, tid, f["title"], f["description"])
            return tid

    def update_task(self, user_id, ws, task_id, fields):
        with self.db() as con:
            old = con.execute("SELECT * FROM tasks WHERE id = ? AND workspace_id = ?", (task_id, ws)).fetchone()
            if not old:
                raise TaskError("Task not found.")
            f = self._check_task_fields(con, ws, fields)
            completed = old["completed_at"]
            if f["status"] == "done" and old["status"] != "done":
                self._check_not_blocked(con, task_id)
                completed = now()
            elif f["status"] != "done":
                completed = None
            con.execute("UPDATE tasks SET project_id=?, title=?, description=?, status=?, priority=?, assignee_id=?, "
                        "due_date=?, updated_at=?, completed_at=?, estimate_minutes=?, start_date=?, sprint_id=?, "
                        "repeat=? WHERE id=?",
                        (f["project_id"], f["title"], f["description"], f["status"], f["priority"],
                         f["assignee_id"], f["due_date"], now(), completed, f["estimate_minutes"], f["start_date"],
                         f["sprint_id"], f["repeat"], task_id))
            if "labels" in fields:
                self.set_task_labels(con, ws, task_id, fields.get("labels"))
            if f["status"] == "done" and old["status"] != "done":
                self._after_done(con, user_id, task_id)
            changes = [k.replace("_id", "").replace("_minutes", "").replace("_", " ") for k in f if f[k] != old[k]]
            self.log(con, user_id, "task_updated", ", ".join(changes) or "no changes", task_id, ws=ws, title=f["title"])
            if f["assignee_id"] and f["assignee_id"] != old["assignee_id"] and f["assignee_id"] != user_id:
                self._notify_assigned(con, user_id, f["assignee_id"], task_id, f["title"], ws)

    def set_status(self, user_id, ws, task_id, status):
        if status not in STATUS_NAMES:
            raise TaskError("Unknown status.")
        with self.db() as con:
            old = con.execute("SELECT status, title FROM tasks WHERE id = ? AND workspace_id = ?",
                              (task_id, ws)).fetchone()
            if not old:
                raise TaskError("Task not found.")
            if old["status"] == status:
                return
            if status == "done":
                self._check_not_blocked(con, task_id)
            con.execute("UPDATE tasks SET status = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                        (status, now(), now() if status == "done" else None, task_id))
            self.log(con, user_id, "task_completed" if status == "done" else "status_changed",
                     f"{STATUS_NAMES[old['status']]} → {STATUS_NAMES[status]}", task_id, ws=ws, title=old["title"])
            if status == "done":
                self._after_done(con, user_id, task_id)

    def delete_task(self, user_id, ws, task_id):
        with self.db() as con:
            t = con.execute("SELECT title FROM tasks WHERE id = ? AND workspace_id = ?", (task_id, ws)).fetchone()
            if not t:
                raise TaskError("Task not found.")
            self._to_trash(con, user_id, ws, task_id)                         # version 3: can be restored
            self.log(con, user_id, "task_deleted", t["title"], ws=ws, title=t["title"])

    def task(self, task_id, ws=None, trashed=False):
        """One task; tasks in the trash are hidden unless trashed=True."""
        with self.db() as con:
            r = con.execute(self.TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
        if not r or (ws is not None and r["workspace_id"] != ws) or (r["deleted_at"] and not trashed):
            return None
        return dict(r)

    def can_edit(self, user, task):
        return user["role"] == "admin" or user["id"] in (task["created_by"], task["assignee_id"])

    def can_delete(self, user, task):
        return user["role"] == "admin" or user["id"] == task["created_by"]

    def tasks(self, ws, q="", status="", priority="", assignee="", project="", due="", sort="due", today=None,
              creator=""):
        today = (today or date.today()).isoformat()
        where, args = ["t.workspace_id = ?", "(p.archived IS NULL OR p.archived = 0)"], [ws]
        if q:
            where.append("(t.title LIKE ? OR t.description LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        if status in STATUS_NAMES:
            where.append("t.status = ?"); args.append(status)
        elif status == "open":
            where.append("t.status != 'done'")
        if priority in PRIORITY_NAMES:
            where.append("t.priority = ?"); args.append(priority)
        if assignee == "none":
            where.append("t.assignee_id IS NULL")
        elif str(assignee).isdigit():
            where.append("t.assignee_id = ?"); args.append(int(assignee))
        if str(project).isdigit():
            where.append("t.project_id = ?"); args.append(int(project))
        if str(creator).isdigit():
            where.append("t.created_by = ?"); args.append(int(creator))
        if due == "overdue":
            where.append("t.status != 'done' AND t.due_date < ?"); args.append(today)
        elif due == "today":
            where.append("t.due_date = ?"); args.append(today)
        elif due == "week":
            week = (date.fromisoformat(today) + timedelta(days=7)).isoformat()
            where.append("t.due_date BETWEEN ? AND ?"); args += [today, week]
        done_last = "CASE WHEN t.status = 'done' THEN 1 ELSE 0 END"
        no_due_last = "CASE WHEN t.due_date IS NULL THEN 1 ELSE 0 END"
        order = {
            "due": f"{done_last}, {no_due_last}, t.due_date",
            "priority": "CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, "
                        f"{no_due_last}, t.due_date",
            "newest": "t.created_at DESC",
            "updated": "t.updated_at DESC",
            "title": "lower(t.title)",
        }.get(sort, f"{done_last}, {no_due_last}, t.due_date")
        sql = f"{self.TASK_SELECT} WHERE {' AND '.join(where)} ORDER BY {order}, t.id"
        with self.db() as con:
            return [dict(r) for r in con.execute(sql, args)]

    def tasks_between(self, ws, start, end):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                self.TASK_SELECT + " WHERE t.workspace_id = ? AND t.due_date BETWEEN ? AND ? "
                "AND (p.archived IS NULL OR p.archived = 0) ORDER BY t.due_date, t.priority",
                (ws, start.isoformat(), end.isoformat()))]

    # ================================================================ comments
    def add_comment(self, user_id, ws, task_id, body):
        body = (body or "").strip()
        if not body:
            raise TaskError("Comment cannot be empty.")
        if len(body) > 2000:
            raise TaskError("Comment is too long.")
        with self.db() as con:
            t = con.execute("SELECT * FROM tasks WHERE id = ? AND workspace_id = ?", (task_id, ws)).fetchone()
            if not t:
                raise TaskError("Task not found.")
            con.execute("INSERT INTO comments (task_id, user_id, body, created_at) VALUES (?,?,?,?)",
                        (task_id, user_id, body, now()))
            self.log(con, user_id, "commented", body[:80], task_id, ws=ws, title=t["title"])
            mentioned = self.notify_mentions(con, user_id, ws, task_id, t["title"], body)
            name = con.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()[0]
            for uid in {t["assignee_id"], t["created_by"]} - {None, user_id} - mentioned:
                self.notify(con, uid, "comment", f"{name} commented on “{t['title']}”.", ws=ws, task_id=task_id)

    def edit_comment(self, user, comment_id, body):
        body = (body or "").strip()
        if not body:
            raise TaskError("Comment cannot be empty.")
        if len(body) > 2000:
            raise TaskError("Comment is too long.")
        with self.db() as con:
            c = con.execute("SELECT c.*, t.workspace_id FROM comments c JOIN tasks t ON t.id = c.task_id "
                            "WHERE c.id = ?", (comment_id,)).fetchone()
            if not c:
                raise TaskError("Comment not found.")
            if user["id"] != c["user_id"]:
                raise PermissionError            # only the author can change the words
            con.execute("UPDATE comments SET body = ?, edited_at = ? WHERE id = ?", (body, now(), comment_id))
            self.log(con, user["id"], "comment_edited", body[:80], c["task_id"], ws=c["workspace_id"])
            return c["task_id"]

    def delete_comment(self, user, comment_id):
        with self.db() as con:
            c = con.execute("SELECT c.*, t.workspace_id FROM comments c JOIN tasks t ON t.id = c.task_id "
                            "WHERE c.id = ?", (comment_id,)).fetchone()
            if not c:
                raise TaskError("Comment not found.")
            if user["role"] != "admin" and user["id"] != c["user_id"]:
                raise PermissionError
            con.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
            self.log(con, user["id"], "comment_deleted", c["body"][:80], c["task_id"], ws=c["workspace_id"])
            return c["task_id"]

    def comment(self, comment_id):
        with self.db() as con:
            r = con.execute("SELECT c.*, t.workspace_id FROM comments c JOIN tasks t ON t.id = c.task_id "
                            "WHERE c.id = ?", (comment_id,)).fetchone()
            return dict(r) if r else None

    def comments(self, task_id):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT c.*, u.name, u.color FROM comments c JOIN users u ON u.id = c.user_id "
                "WHERE c.task_id = ? ORDER BY c.created_at, c.id", (task_id,))]

    # ----- checklist (subtasks)
    def subtasks(self, task_id):
        with self.db() as con:
            return [dict(r) for r in con.execute("SELECT * FROM subtasks WHERE task_id = ? ORDER BY id", (task_id,))]

    def subtask(self, sub_id):
        with self.db() as con:
            r = con.execute("SELECT * FROM subtasks WHERE id = ?", (sub_id,)).fetchone()
            return dict(r) if r else None

    def _task_ws(self, con, task_id):
        r = con.execute("SELECT workspace_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return r[0] if r else None

    def add_subtask(self, user_id, task_id, title):
        title = " ".join((title or "").split())
        if not 2 <= len(title) <= 150:
            raise TaskError("Checklist item must have 2-150 characters.")
        with self.db() as con:
            if con.execute("SELECT COUNT(*) FROM subtasks WHERE task_id = ?", (task_id,)).fetchone()[0] >= 30:
                raise TaskError("A task can have at most 30 checklist items.")
            con.execute("INSERT INTO subtasks (task_id, title, created_by, created_at) VALUES (?,?,?,?)",
                        (task_id, title, user_id, now()))
            con.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now(), task_id))
            self.log(con, user_id, "checklist_added", title, task_id, ws=self._task_ws(con, task_id))

    def update_subtask(self, user_id, sub_id, done=None, title=None):
        with self.db() as con:
            s = con.execute("SELECT * FROM subtasks WHERE id = ?", (sub_id,)).fetchone()
            if done is not None:
                con.execute("UPDATE subtasks SET done = ? WHERE id = ?", (1 if done else 0, sub_id))
                if done:
                    self.log(con, user_id, "checklist_done", s["title"], s["task_id"],
                             ws=self._task_ws(con, s["task_id"]))
            if title is not None:
                title = " ".join(title.split())
                if not 2 <= len(title) <= 150:
                    raise TaskError("Checklist item must have 2-150 characters.")
                con.execute("UPDATE subtasks SET title = ? WHERE id = ?", (title, sub_id))
            con.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now(), s["task_id"]))

    def delete_subtask(self, user_id, sub_id):
        with self.db() as con:
            s = con.execute("SELECT * FROM subtasks WHERE id = ?", (sub_id,)).fetchone()
            con.execute("DELETE FROM subtasks WHERE id = ?", (sub_id,))
            self.log(con, user_id, "checklist_removed", s["title"], s["task_id"], ws=self._task_ws(con, s["task_id"]))

    def bulk_update(self, admin_id, ws, task_ids, action, value=None):
        ids = [int(i) for i in task_ids if str(i).isdigit()]
        if not ids:
            raise TaskError("Select at least one task.")
        marks = ",".join("?" * len(ids))
        scope = f"workspace_id = ? AND id IN ({marks})"
        with self.db() as con:
            if action == "delete":
                found = [r[0] for r in con.execute(f"SELECT id FROM tasks WHERE {scope}", [ws] + ids)]
                for tid in found:
                    self._to_trash(con, admin_id, ws, tid)
                n = len(found)
                self.log(con, admin_id, "bulk_update", f"deleted {n} tasks", ws=ws)
                return n
            if action == "status" and value in STATUS_NAMES:
                becoming_done = [r[0] for r in con.execute(f"SELECT id FROM tasks WHERE {scope} AND status != 'done'",
                                                           [ws] + ids)] if value == "done" else []
                blocked = {r[0] for r in con.execute(
                    "SELECT d.task_id FROM task_deps d JOIN tasks b ON b.id = d.blocked_by "
                    "WHERE b.status != 'done' AND b.deleted_at IS NULL")} if value == "done" else set()
                ids = [i for i in ids if i not in blocked]
                if not ids:
                    raise TaskError("These tasks wait for other tasks. Finish those first.")
                marks = ",".join("?" * len(ids))
                scope = f"workspace_id = ? AND id IN ({marks})"
                n = con.execute(f"UPDATE tasks SET status = ?, updated_at = ?, completed_at = "
                                f"CASE WHEN ? = 'done' THEN COALESCE(completed_at, ?) ELSE NULL END "
                                f"WHERE {scope}", [value, now(), value, now(), ws] + ids).rowcount
                for tid in becoming_done:
                    if tid not in blocked:
                        self._after_done(con, admin_id, tid)
                detail = f"{n} tasks → {STATUS_NAMES[value]}"
            elif action == "priority" and value in PRIORITY_NAMES:
                n = con.execute(f"UPDATE tasks SET priority = ?, updated_at = ? WHERE {scope}",
                                [value, now(), ws] + ids).rowcount
                detail = f"{n} tasks → priority {PRIORITY_NAMES[value]}"
            elif action == "assignee":
                uid = int(value) if str(value or "").isdigit() else None
                if uid and not con.execute("SELECT 1 FROM users u JOIN memberships m ON m.user_id = u.id "
                                           "WHERE u.id = ? AND u.active = 1 AND m.workspace_id = ?",
                                           (uid, ws)).fetchone():
                    raise TaskError("The selected person does not exist.")
                n = con.execute(f"UPDATE tasks SET assignee_id = ?, updated_at = ? WHERE {scope}",
                                [uid, now(), ws] + ids).rowcount
                detail = f"{n} tasks reassigned"
            else:
                raise TaskError("Choose what to change.")
            self.log(con, admin_id, "bulk_update", detail, ws=ws)
            return n

    # ================================================================ activity & stats
    def activity(self, ws, limit=20, task_id=None):
        sql = ("SELECT a.*, u.name, u.color, t.title AS task_title FROM activity a "
               "LEFT JOIN users u ON u.id = a.user_id LEFT JOIN tasks t ON t.id = a.task_id WHERE a.workspace_id = ? ")
        args = [ws]
        if task_id:
            sql += "AND a.task_id = ? "
            args.append(task_id)
        sql += "ORDER BY a.created_at DESC, a.id DESC LIMIT ?"
        args.append(limit)
        with self.db() as con:
            return [dict(r) for r in con.execute(sql, args)]

    def stats(self, ws, user_id=None, today=None):
        today = (today or date.today()).isoformat()
        where = ["t.workspace_id = ?", "(p.archived IS NULL OR p.archived = 0)"]
        args = [ws]
        if user_id:
            where.append("t.assignee_id = ?")
            args.append(user_id)
        cond = " AND ".join(where)
        join = " LEFT JOIN projects p ON p.id = t.project_id "
        with self.db() as con:
            r = con.execute(f"""SELECT COUNT(*) AS total,
                    {count_case("t.status = 'done'")} AS done,
                    {count_case("t.status != 'done' AND t.due_date < ?")} AS overdue,
                    {count_case("t.status != 'done' AND t.due_date = ?")} AS due_today,
                    {count_case("t.status = 'in_progress'")} AS in_progress
                FROM tasks t {join} WHERE {cond}""", [today, today] + args).fetchone()
            s = {k: (r[k] or 0) for k in r.keys()}
            s["open"] = s["total"] - s["done"]
            s["completion"] = round(s["done"] / s["total"] * 100) if s["total"] else 0
            by_status = dict.fromkeys(STATUS_NAMES, 0)
            for row in con.execute(f"SELECT t.status, COUNT(*) FROM tasks t {join} WHERE {cond} GROUP BY t.status", args):
                by_status[row[0]] = row[1]
            s["by_status"] = by_status
            by_priority = dict.fromkeys(PRIORITY_NAMES, 0)
            for row in con.execute(f"SELECT t.priority, COUNT(*) FROM tasks t {join} WHERE {cond} "
                                   "AND t.status != 'done' GROUP BY t.priority", args):
                by_priority[row[0]] = row[1]
            s["by_priority"] = by_priority
            return s

    def completed_per_day(self, ws, days=14, today=None):
        today = today or date.today()
        start = today - timedelta(days=days - 1)
        with self.db() as con:
            counts = {r[0]: r[1] for r in con.execute(
                "SELECT substr(completed_at, 1, 10) AS d, COUNT(*) FROM tasks "
                "WHERE workspace_id = ? AND completed_at >= ? GROUP BY substr(completed_at, 1, 10)",
                (ws, start.isoformat())).fetchall()}
        return [(start + timedelta(days=i), counts.get((start + timedelta(days=i)).isoformat(), 0))
                for i in range(days)]

    def user_report(self, ws, today=None):
        """The same numbers as the original console version's user_overview.txt, per member of the workspace."""
        today = (today or date.today()).isoformat()
        with self.db() as con:
            total = con.execute("SELECT COUNT(*) FROM tasks WHERE workspace_id = ?", (ws,)).fetchone()[0]
            rows = con.execute(f"""SELECT u.id, u.name, u.email, u.color, u.job_title, u.active, u.role AS site_role,
                    m.role,
                    COUNT(t.id) AS assigned,
                    {count_case("t.status = 'done'")} AS done,
                    {count_case("t.status != 'done' AND t.due_date < ?")} AS overdue
                FROM memberships m JOIN users u ON u.id = m.user_id
                LEFT JOIN tasks t ON t.assignee_id = u.id AND t.workspace_id = m.workspace_id
                WHERE m.workspace_id = ?
                GROUP BY u.id, u.name, u.email, u.color, u.job_title, u.active, u.role, m.role
                ORDER BY COUNT(t.id) DESC, lower(u.name)""", (today, ws)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            a = d["assigned"]
            d["share"] = round(a / total * 100, 1) if total else 0
            d["done_pct"] = round(d["done"] / a * 100, 1) if a else 0
            d["open_pct"] = round((a - d["done"]) / a * 100, 1) if a else 0
            d["overdue_pct"] = round(d["overdue"] / a * 100, 1) if a else 0
            out.append(d)
        return out
