"""
store.py - database and business rules for TaskFlow (SQLite).

Tables: users, projects, tasks, comments, activity.
Every function that changes data also writes a line to the activity log.
"""

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

STATUSES = [("todo", "To Do"), ("in_progress", "In Progress"), ("review", "In Review"), ("done", "Done")]
PRIORITIES = [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("urgent", "Urgent")]
STATUS_NAMES = dict(STATUSES)
PRIORITY_NAMES = dict(PRIORITIES)
PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
COLORS = ["#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#14b8a6"]
MIN_PASSWORD = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    job_title TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
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
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    task_id INTEGER,
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_activity_time ON activity(created_at);
CREATE TABLE IF NOT EXISTS subtasks (
    id INTEGER PRIMARY KEY,
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
"""


class TaskError(Exception):
    """A problem the user can fix - shown as a message on the page."""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_due(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise TaskError("Due date must be a valid date.")


class Store:
    def __init__(self, path):
        self.path = path
        with self.db() as con:
            con.executescript(SCHEMA)
            # small migration for databases made by older versions
            cols = [r[1] for r in con.execute("PRAGMA table_info(comments)")]
            if "edited_at" not in cols:
                con.execute("ALTER TABLE comments ADD COLUMN edited_at TEXT")

    @contextmanager
    def db(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def log(self, con, user_id, action, details="", task_id=None):
        con.execute("INSERT INTO activity (user_id, task_id, action, details, created_at) VALUES (?,?,?,?,?)",
                    (user_id, task_id, action, details, now()))

    # ================================================================ users
    def count_users(self):
        with self.db() as con:
            return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, name, email, password, role=None, job_title="", by=None):
        name, email = (name or "").strip(), (email or "").strip().lower()
        if len(name) < 2:
            raise TaskError("Please enter your full name.")
        if "@" not in email or "." not in email.split("@")[-1]:
            raise TaskError("Please enter a valid email address.")
        if len(password or "") < MIN_PASSWORD:
            raise TaskError(f"Password must have at least {MIN_PASSWORD} characters.")
        with self.db() as con:
            if con.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise TaskError("An account with this email already exists.")
            first = con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            role = "admin" if first else (role if role in ("admin", "member") else "member")
            color = COLORS[con.execute("SELECT COUNT(*) FROM users").fetchone()[0] % len(COLORS)]
            cur = con.execute(
                "INSERT INTO users (name, email, password_hash, role, job_title, color, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (name, email, generate_password_hash(password), role, job_title.strip(), color, now()))
            self.log(con, by or cur.lastrowid, "user_added", f"{name} joined as {role}")
            return cur.lastrowid

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

    def users(self, active_only=False):
        sql = "SELECT * FROM users" + (" WHERE active = 1" if active_only else "") + " ORDER BY name"
        with self.db() as con:
            return [dict(r) for r in con.execute(sql)]

    def update_profile(self, user_id, name, email, job_title, color=None):
        name, email = (name or "").strip(), (email or "").strip().lower()
        if len(name) < 2:
            raise TaskError("Please enter your full name.")
        if "@" not in email:
            raise TaskError("Please enter a valid email address.")
        with self.db() as con:
            if con.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, user_id)).fetchone():
                raise TaskError("This email is used by another account.")
            con.execute("UPDATE users SET name = ?, email = ?, job_title = ? WHERE id = ?",
                        (name, email, (job_title or "").strip(), user_id))
            if color in COLORS:
                con.execute("UPDATE users SET color = ? WHERE id = ?", (color, user_id))
            self.log(con, user_id, "profile_updated")

    def change_password(self, user_id, old, new, confirm):
        u = self.user(user_id)
        if not check_password_hash(u["password_hash"], old or ""):
            raise TaskError("Your current password is wrong.")
        if new != confirm:
            raise TaskError("New passwords do not match.")
        if len(new or "") < MIN_PASSWORD:
            raise TaskError(f"Password must have at least {MIN_PASSWORD} characters.")
        with self.db() as con:
            con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new), user_id))
            self.log(con, user_id, "password_changed")

    def admin_set_role(self, admin_id, user_id, role):
        if admin_id == user_id:
            raise TaskError("You cannot change your own role.")
        if role not in ("admin", "member"):
            raise TaskError("Unknown role.")
        with self.db() as con:
            con.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
            name = con.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()["name"]
            self.log(con, admin_id, "role_changed", f"{name} is now {role}")

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
            con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new), user_id))
            name = con.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()["name"]
            self.log(con, admin_id, "password_reset", name)

    def admin_update_user(self, admin_id, user_id, name, email, job_title, color=None):
        name, email = (name or "").strip(), (email or "").strip().lower()
        if len(name) < 2:
            raise TaskError("Please enter a full name.")
        if "@" not in email or "." not in email.split("@")[-1]:
            raise TaskError("Please enter a valid email address.")
        with self.db() as con:
            if con.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, user_id)).fetchone():
                raise TaskError("This email is used by another account.")
            old = con.execute("SELECT color FROM users WHERE id = ?", (user_id,)).fetchone()
            con.execute("UPDATE users SET name = ?, email = ?, job_title = ?, color = ? WHERE id = ?",
                        (name, email, (job_title or "").strip(), color if color in COLORS else old["color"], user_id))
            self.log(con, admin_id, "user_updated", name)

    def delete_own_account(self, user_id, password):
        u = self.user(user_id)
        if not check_password_hash(u["password_hash"], password or ""):
            raise TaskError("Password is wrong - your account was not deleted.")
        if u["role"] == "admin":
            raise TaskError("Admins cannot delete their own account. Ask another admin or change your role first.")
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
            con.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self.log(con, None if action == "account_closed" else admin_id, action, u["name"])

    # ================================================================ settings
    DEFAULT_SETTINGS = {"workspace_name": "TaskFlow", "allow_registration": "1", "default_priority": "medium"}

    def settings(self):
        with self.db() as con:
            saved = dict(con.execute("SELECT key, value FROM settings").fetchall())
        return {**self.DEFAULT_SETTINGS, **saved}

    def save_settings(self, admin_id, workspace_name, allow_registration, default_priority):
        name = (workspace_name or "").strip()
        if not 2 <= len(name) <= 40:
            raise TaskError("Workspace name must have 2-40 characters.")
        if default_priority not in PRIORITY_NAMES:
            raise TaskError("Unknown priority.")
        values = {"workspace_name": name, "allow_registration": "1" if allow_registration else "0",
                  "default_priority": default_priority}
        with self.db() as con:
            for k, v in values.items():
                con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, v))
            self.log(con, admin_id, "settings_updated")

    # ================================================================ data tools (admin)
    TABLES = ["users", "projects", "tasks", "subtasks", "comments", "activity", "settings"]
    HIDDEN_COLUMNS = {"password_hash"}

    def table_rows(self, table, limit=500):
        if table not in self.TABLES:
            raise TaskError("Unknown table.")
        with self.db() as con:
            cur = con.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT ?", (limit,))
            cols = [c[0] for c in cur.description if c[0] not in self.HIDDEN_COLUMNS]
            rows = [[r[c] for c in cols] for r in cur.fetchall()]
            total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return cols, rows, total

    def table_counts(self):
        with self.db() as con:
            return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in self.TABLES}

    def clear_activity(self, admin_id, older_than_days=0):
        cutoff = (datetime.now() - timedelta(days=older_than_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as con:
            n = con.execute("DELETE FROM activity WHERE created_at <= ?", (cutoff,)).rowcount
            self.log(con, admin_id, "log_cleared", f"{n} entries removed")
            return n

    def delete_completed_tasks(self, admin_id, older_than_days=30):
        cutoff = (datetime.now() - timedelta(days=older_than_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as con:
            n = con.execute("DELETE FROM tasks WHERE status = 'done' AND completed_at <= ?", (cutoff,)).rowcount
            self.log(con, admin_id, "tasks_cleaned", f"{n} old completed tasks removed")
            return n

    def wipe(self):
        """Empty every table (used by 'reset demo data')."""
        with self.db() as con:
            for t in ["subtasks", "comments", "activity", "tasks", "projects", "users", "settings"]:
                con.execute(f"DELETE FROM {t}")

    # ================================================================ projects
    def projects(self, include_archived=False):
        sql = """SELECT p.*, o.name AS owner_name,
                   COUNT(t.id) AS total,
                   SUM(t.status = 'done') AS done
                 FROM projects p LEFT JOIN tasks t ON t.project_id = p.id
                 LEFT JOIN users o ON o.id = p.created_by
                 {where} GROUP BY p.id ORDER BY p.archived, p.name"""
        with self.db() as con:
            rows = con.execute(sql.format(where="" if include_archived else "WHERE p.archived = 0"))
            out = []
            for r in rows:
                d = dict(r)
                d["done"] = d["done"] or 0
                d["progress"] = round(d["done"] / d["total"] * 100) if d["total"] else 0
                out.append(d)
            return out

    def project(self, project_id):
        with self.db() as con:
            r = con.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            return dict(r) if r else None

    def can_manage_project(self, user, project):
        return user["role"] == "admin" or project.get("created_by") == user["id"]

    def save_project(self, user_id, name, description, color, project_id=None):
        name = (name or "").strip()
        if len(name) < 2:
            raise TaskError("Project name is too short.")
        color = color if color in COLORS else COLORS[0]
        with self.db() as con:
            if project_id:
                con.execute("UPDATE projects SET name = ?, description = ?, color = ? WHERE id = ?",
                            (name, (description or "").strip(), color, project_id))
                self.log(con, user_id, "project_updated", name)
                return project_id
            cur = con.execute("INSERT INTO projects (name, description, color, created_by, created_at) "
                              "VALUES (?,?,?,?,?)", (name, (description or "").strip(), color, user_id, now()))
            self.log(con, user_id, "project_created", name)
            return cur.lastrowid

    def delete_project(self, user_id, project_id, delete_tasks=False):
        with self.db() as con:
            p = con.execute("SELECT name FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not p:
                raise TaskError("Project not found.")
            if delete_tasks:
                n = con.execute("DELETE FROM tasks WHERE project_id = ?", (project_id,)).rowcount
                detail = f"{p['name']} and {n} tasks"
            else:
                con.execute("UPDATE tasks SET project_id = NULL WHERE project_id = ?", (project_id,))
                detail = f"{p['name']} (tasks kept without project)"
            con.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            self.log(con, user_id, "project_deleted", detail)

    def toggle_archive(self, user_id, project_id):
        with self.db() as con:
            p = con.execute("SELECT name, archived FROM projects WHERE id = ?", (project_id,)).fetchone()
            con.execute("UPDATE projects SET archived = ? WHERE id = ?", (0 if p["archived"] else 1, project_id))
            self.log(con, user_id, "project_restored" if p["archived"] else "project_archived", p["name"])

    # ================================================================ tasks
    TASK_SELECT = """SELECT t.*, u.name AS assignee_name, u.color AS assignee_color,
                            c.name AS creator_name, p.name AS project_name, p.color AS project_color,
                            (SELECT COUNT(*) FROM comments WHERE task_id = t.id) AS comment_count,
                            (SELECT COUNT(*) FROM subtasks WHERE task_id = t.id) AS sub_total,
                            (SELECT COUNT(*) FROM subtasks WHERE task_id = t.id AND done = 1) AS sub_done
                     FROM tasks t
                     LEFT JOIN users u ON u.id = t.assignee_id
                     LEFT JOIN users c ON c.id = t.created_by
                     LEFT JOIN projects p ON p.id = t.project_id"""

    def _check_task_fields(self, con, f):
        title = (f.get("title") or "").strip()
        if len(title) < 3:
            raise TaskError("Task title must have at least 3 characters.")
        if len(title) > 120:
            raise TaskError("Task title is too long (max 120 characters).")
        status = f.get("status") or "todo"
        priority = f.get("priority") or "medium"
        if status not in STATUS_NAMES or priority not in PRIORITY_NAMES:
            raise TaskError("Unknown status or priority.")
        assignee = int(f["assignee_id"]) if str(f.get("assignee_id") or "").isdigit() else None
        if assignee and not con.execute("SELECT 1 FROM users WHERE id = ? AND active = 1", (assignee,)).fetchone():
            raise TaskError("The selected person does not exist.")
        project = int(f["project_id"]) if str(f.get("project_id") or "").isdigit() else None
        if project and not con.execute("SELECT 1 FROM projects WHERE id = ?", (project,)).fetchone():
            raise TaskError("The selected project does not exist.")
        return dict(title=title, description=(f.get("description") or "").strip(), status=status,
                    priority=priority, assignee_id=assignee, project_id=project, due_date=parse_due(f.get("due_date")))

    def create_task(self, user_id, fields):
        with self.db() as con:
            f = self._check_task_fields(con, fields)
            if f["due_date"] and f["due_date"] < date.today().isoformat():
                raise TaskError("Due date cannot be in the past.")
            ts = now()
            cur = con.execute(
                "INSERT INTO tasks (project_id, title, description, status, priority, assignee_id, created_by, "
                "due_date, created_at, updated_at, completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f["project_id"], f["title"], f["description"], f["status"], f["priority"], f["assignee_id"],
                 user_id, f["due_date"], ts, ts, ts if f["status"] == "done" else None))
            self.log(con, user_id, "task_created", f["title"], cur.lastrowid)
            return cur.lastrowid

    def update_task(self, user_id, task_id, fields):
        with self.db() as con:
            old = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not old:
                raise TaskError("Task not found.")
            f = self._check_task_fields(con, fields)
            completed = old["completed_at"]
            if f["status"] == "done" and old["status"] != "done":
                completed = now()
            elif f["status"] != "done":
                completed = None
            con.execute("UPDATE tasks SET project_id=?, title=?, description=?, status=?, priority=?, assignee_id=?, "
                        "due_date=?, updated_at=?, completed_at=? WHERE id=?",
                        (f["project_id"], f["title"], f["description"], f["status"], f["priority"],
                         f["assignee_id"], f["due_date"], now(), completed, task_id))
            changes = [k.replace("_id", "").replace("_", " ") for k in f if f[k] != old[k]]
            self.log(con, user_id, "task_updated", ", ".join(changes) or "no changes", task_id)

    def set_status(self, user_id, task_id, status):
        if status not in STATUS_NAMES:
            raise TaskError("Unknown status.")
        with self.db() as con:
            old = con.execute("SELECT status, title FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not old:
                raise TaskError("Task not found.")
            if old["status"] == status:
                return
            con.execute("UPDATE tasks SET status = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                        (status, now(), now() if status == "done" else None, task_id))
            self.log(con, user_id, "task_completed" if status == "done" else "status_changed",
                     f"{STATUS_NAMES[old['status']]} → {STATUS_NAMES[status]}", task_id)

    def delete_task(self, user_id, task_id):
        with self.db() as con:
            t = con.execute("SELECT title FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not t:
                raise TaskError("Task not found.")
            con.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self.log(con, user_id, "task_deleted", t["title"])

    def task(self, task_id):
        with self.db() as con:
            r = con.execute(self.TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
            return dict(r) if r else None

    def can_edit(self, user, task):
        return user["role"] == "admin" or user["id"] in (task["created_by"], task["assignee_id"])

    def can_delete(self, user, task):
        return user["role"] == "admin" or user["id"] == task["created_by"]

    def tasks(self, q="", status="", priority="", assignee="", project="", due="", sort="due", today=None, creator=""):
        today = (today or date.today()).isoformat()
        where, args = ["(p.archived IS NULL OR p.archived = 0)"], []
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
        order = {
            "due": "t.status = 'done', t.due_date IS NULL, t.due_date",
            "priority": "CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, t.due_date",
            "newest": "t.created_at DESC",
            "updated": "t.updated_at DESC",
            "title": "t.title COLLATE NOCASE",
        }.get(sort, "t.status = 'done', t.due_date IS NULL, t.due_date")
        sql = f"{self.TASK_SELECT} WHERE {' AND '.join(where)} ORDER BY {order}, t.id"
        with self.db() as con:
            return [dict(r) for r in con.execute(sql, args)]

    def tasks_between(self, start, end):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                self.TASK_SELECT + " WHERE t.due_date BETWEEN ? AND ? AND (p.archived IS NULL OR p.archived = 0) "
                "ORDER BY t.due_date, t.priority", (start.isoformat(), end.isoformat()))]

    # ================================================================ comments
    def add_comment(self, user_id, task_id, body):
        body = (body or "").strip()
        if not body:
            raise TaskError("Comment cannot be empty.")
        if len(body) > 2000:
            raise TaskError("Comment is too long.")
        with self.db() as con:
            if not con.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
                raise TaskError("Task not found.")
            con.execute("INSERT INTO comments (task_id, user_id, body, created_at) VALUES (?,?,?,?)",
                        (task_id, user_id, body, now()))
            self.log(con, user_id, "commented", body[:80], task_id)

    def edit_comment(self, user, comment_id, body):
        body = (body or "").strip()
        if not body:
            raise TaskError("Comment cannot be empty.")
        if len(body) > 2000:
            raise TaskError("Comment is too long.")
        with self.db() as con:
            c = con.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
            if not c:
                raise TaskError("Comment not found.")
            if user["id"] != c["user_id"]:
                raise PermissionError            # only the author can change the words
            con.execute("UPDATE comments SET body = ?, edited_at = ? WHERE id = ?", (body, now(), comment_id))
            self.log(con, user["id"], "comment_edited", body[:80], c["task_id"])
            return c["task_id"]

    # ----- checklist (subtasks)
    def subtasks(self, task_id):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT * FROM subtasks WHERE task_id = ? ORDER BY id", (task_id,))]

    def subtask(self, sub_id):
        with self.db() as con:
            r = con.execute("SELECT * FROM subtasks WHERE id = ?", (sub_id,)).fetchone()
            return dict(r) if r else None

    def add_subtask(self, user_id, task_id, title):
        title = (title or "").strip()
        if not 2 <= len(title) <= 150:
            raise TaskError("Checklist item must have 2-150 characters.")
        with self.db() as con:
            if con.execute("SELECT COUNT(*) FROM subtasks WHERE task_id = ?", (task_id,)).fetchone()[0] >= 30:
                raise TaskError("A task can have at most 30 checklist items.")
            con.execute("INSERT INTO subtasks (task_id, title, created_by, created_at) VALUES (?,?,?,?)",
                        (task_id, title, user_id, now()))
            con.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now(), task_id))
            self.log(con, user_id, "checklist_added", title, task_id)

    def update_subtask(self, user_id, sub_id, done=None, title=None):
        with self.db() as con:
            s = con.execute("SELECT * FROM subtasks WHERE id = ?", (sub_id,)).fetchone()
            if done is not None:
                con.execute("UPDATE subtasks SET done = ? WHERE id = ?", (1 if done else 0, sub_id))
                if done:
                    self.log(con, user_id, "checklist_done", s["title"], s["task_id"])
            if title is not None:
                title = title.strip()
                if not 2 <= len(title) <= 150:
                    raise TaskError("Checklist item must have 2-150 characters.")
                con.execute("UPDATE subtasks SET title = ? WHERE id = ?", (title, sub_id))
            con.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now(), s["task_id"]))

    def delete_subtask(self, user_id, sub_id):
        with self.db() as con:
            s = con.execute("SELECT * FROM subtasks WHERE id = ?", (sub_id,)).fetchone()
            con.execute("DELETE FROM subtasks WHERE id = ?", (sub_id,))
            self.log(con, user_id, "checklist_removed", s["title"], s["task_id"])

    def delete_comment(self, user, comment_id):
        with self.db() as con:
            c = con.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
            if not c:
                raise TaskError("Comment not found.")
            if user["role"] != "admin" and user["id"] != c["user_id"]:
                raise PermissionError
            con.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
            self.log(con, user["id"], "comment_deleted", c["body"][:80], c["task_id"])
            return c["task_id"]

    def bulk_update(self, admin_id, task_ids, action, value=None):
        ids = [int(i) for i in task_ids if str(i).isdigit()]
        if not ids:
            raise TaskError("Select at least one task.")
        marks = ",".join("?" * len(ids))
        with self.db() as con:
            if action == "delete":
                n = con.execute(f"DELETE FROM tasks WHERE id IN ({marks})", ids).rowcount
                self.log(con, admin_id, "bulk_update", f"deleted {n} tasks")
                return n
            if action == "status" and value in STATUS_NAMES:
                n = con.execute(f"UPDATE tasks SET status = ?, updated_at = ?, completed_at = "
                                f"CASE WHEN ? = 'done' THEN COALESCE(completed_at, ?) ELSE NULL END "
                                f"WHERE id IN ({marks})", [value, now(), value, now()] + ids).rowcount
                detail = f"{n} tasks → {STATUS_NAMES[value]}"
            elif action == "priority" and value in PRIORITY_NAMES:
                n = con.execute(f"UPDATE tasks SET priority = ?, updated_at = ? WHERE id IN ({marks})",
                                [value, now()] + ids).rowcount
                detail = f"{n} tasks → priority {PRIORITY_NAMES[value]}"
            elif action == "assignee":
                uid = int(value) if str(value or "").isdigit() else None
                if uid and not con.execute("SELECT 1 FROM users WHERE id = ? AND active = 1", (uid,)).fetchone():
                    raise TaskError("The selected person does not exist.")
                n = con.execute(f"UPDATE tasks SET assignee_id = ?, updated_at = ? WHERE id IN ({marks})",
                                [uid, now()] + ids).rowcount
                detail = f"{n} tasks reassigned"
            else:
                raise TaskError("Choose what to change.")
            self.log(con, admin_id, "bulk_update", detail)
            return n

    def comments(self, task_id):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT c.*, u.name, u.color FROM comments c JOIN users u ON u.id = c.user_id "
                "WHERE c.task_id = ? ORDER BY c.created_at", (task_id,))]

    # ================================================================ activity & stats
    def activity(self, limit=20, task_id=None):
        sql = ("SELECT a.*, u.name, u.color, t.title AS task_title FROM activity a "
               "LEFT JOIN users u ON u.id = a.user_id LEFT JOIN tasks t ON t.id = a.task_id ")
        args = []
        if task_id:
            sql += "WHERE a.task_id = ? "
            args.append(task_id)
        sql += "ORDER BY a.created_at DESC, a.id DESC LIMIT ?"
        args.append(limit)
        with self.db() as con:
            return [dict(r) for r in con.execute(sql, args)]

    def stats(self, user_id=None, today=None):
        today = (today or date.today()).isoformat()
        cond, args = ("WHERE t.assignee_id = ?", [user_id]) if user_id else ("", [])
        join = " LEFT JOIN projects p ON p.id = t.project_id "
        arch = ("AND" if cond else "WHERE") + " (p.archived IS NULL OR p.archived = 0)"
        with self.db() as con:
            r = con.execute(f"""SELECT COUNT(*) AS total,
                    SUM(t.status = 'done') AS done,
                    SUM(t.status != 'done' AND t.due_date < ?) AS overdue,
                    SUM(t.status != 'done' AND t.due_date = ?) AS due_today,
                    SUM(t.status = 'in_progress') AS in_progress
                FROM tasks t {join} {cond} {arch}""", [today, today] + args).fetchone()
            s = {k: (r[k] or 0) for k in r.keys()}
            s["open"] = s["total"] - s["done"]
            s["completion"] = round(s["done"] / s["total"] * 100) if s["total"] else 0
            by_status = dict.fromkeys(STATUS_NAMES, 0)
            for row in con.execute(f"SELECT t.status, COUNT(*) FROM tasks t {join} {cond} {arch} GROUP BY t.status", args):
                by_status[row[0]] = row[1]
            s["by_status"] = by_status
            by_priority = dict.fromkeys(PRIORITY_NAMES, 0)
            for row in con.execute(f"SELECT t.priority, COUNT(*) FROM tasks t {join} {cond} {arch} "
                                   "AND t.status != 'done' GROUP BY t.priority", args):
                by_priority[row[0]] = row[1]
            s["by_priority"] = by_priority
            return s

    def completed_per_day(self, days=14, today=None):
        today = today or date.today()
        start = today - timedelta(days=days - 1)
        with self.db() as con:
            counts = dict(con.execute("SELECT substr(completed_at, 1, 10) AS d, COUNT(*) FROM tasks "
                                      "WHERE completed_at >= ? GROUP BY d", (start.isoformat(),)).fetchall())
        return [(start + timedelta(days=i), counts.get((start + timedelta(days=i)).isoformat(), 0))
                for i in range(days)]

    def user_report(self, today=None):
        """The same numbers as the original console version's user_overview.txt, per person."""
        today = (today or date.today()).isoformat()
        with self.db() as con:
            total = con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            rows = con.execute("""SELECT u.id, u.name, u.email, u.color, u.job_title, u.active, u.role,
                    COUNT(t.id) AS assigned,
                    COALESCE(SUM(t.status = 'done'), 0) AS done,
                    COALESCE(SUM(t.status != 'done' AND t.due_date < ?), 0) AS overdue
                FROM users u LEFT JOIN tasks t ON t.assignee_id = u.id
                GROUP BY u.id ORDER BY assigned DESC, u.name""", (today,)).fetchall()
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
