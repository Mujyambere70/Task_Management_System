"""
v4_store.py - the data rules added in TaskFlow version 4.

  board columns     a workspace can have its own board columns ("Backlog", "Testing" ...); every column belongs to one
                    of the four base states, so reports, Gantt and sprints keep working; a column can have a WIP limit
  custom fields     extra fields on tasks (text, number, date, choice, yes/no), set by the workspace admin
  automations       "when ... then ..." rules: when a task is created / moved to a state / commented, then assign it,
                    set the priority, add a label, move it, set a due date or tell somebody
  saved views       a filtered task list or board saved under a name (in the sidebar)
  task templates    a ready title, description, priority, labels and checklist for repeated work
  API tokens        personal keys for the REST API /api/v1 (kept only as a SHA-256 hash)
  webhooks          TaskFlow calls your https address on task changes, signed with HMAC-SHA256
  outbox retries    a failed email or webhook is tried again later (1, 5, 30, 120 minutes), up to 5 times
  weekly digest     an optional Monday email with your open tasks of the week
  calendar import   tasks from an .ics file or a calendar address (Google, Outlook ...); moved feed events update
                    the due dates of their tasks
  better estimate   suggested time from the title, project, assignee, labels and priority, with its measured accuracy
  file storage      task files in S3-compatible storage when S3_BUCKET is set (s3.py)
  translations      people suggest better words for their language; the site administrator approves them
  sign-in limits    too many wrong passwords from one address or for one account -> wait 15 minutes
  single sign-on    accounts linked to Google / Microsoft sign-in (oidc.py)
"""

import hashlib
import hmac
import ipaddress
import json
import math
import re
import secrets
import socket
import threading
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta

from core import TaskError, is_example_email, now

V4_SCHEMA = """
CREATE TABLE IF NOT EXISTS board_columns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    base_status TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    wip_limit INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS custom_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    options TEXT NOT NULL DEFAULT '',
    required INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS field_values (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    field_id INTEGER NOT NULL REFERENCES custom_fields(id) ON DELETE CASCADE,
    value TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (task_id, field_id)
);
CREATE TABLE IF NOT EXISTS automations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    trigger_kind TEXT NOT NULL,
    trigger_value TEXT NOT NULL DEFAULT '',
    project_id INTEGER,
    action_kind TEXT NOT NULL,
    action_value TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    runs INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS saved_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    page TEXT NOT NULL DEFAULT 'tasks',
    query TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'medium',
    project_id INTEGER,
    estimate_minutes INTEGER,
    checklist TEXT NOT NULL DEFAULT '',
    label_ids TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'read',
    last_used TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS webhooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    secret TEXT NOT NULL,
    events TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    last_status TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS translation_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lang TEXT NOT NULL,
    english TEXT NOT NULL,
    text TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_by INTEGER,
    decided_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    who TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calendar_subs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL,
    project_id INTEGER,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    last_sync TEXT,
    last_status TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_columns_ws ON board_columns(workspace_id);
CREATE INDEX IF NOT EXISTS idx_fields_ws ON custom_fields(workspace_id);
CREATE INDEX IF NOT EXISTS idx_auto_ws ON automations(workspace_id);
CREATE INDEX IF NOT EXISTS idx_views_user ON saved_views(user_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_login_who ON login_attempts(who, created_at);
CREATE INDEX IF NOT EXISTS idx_hooks_ws ON webhooks(workspace_id);
"""

V4_MIGRATIONS = {
    "tasks": [("column_id", "INTEGER"), ("ext_uid", "TEXT NOT NULL DEFAULT ''")],
    "outbox": [("attempts", "INTEGER NOT NULL DEFAULT 0"), ("next_try", "TEXT")],
    "attachments": [("storage", "TEXT NOT NULL DEFAULT 'db'"), ("storage_key", "TEXT NOT NULL DEFAULT ''")],
    "users": [("theme", "TEXT NOT NULL DEFAULT 'auto'"), ("digest", "INTEGER NOT NULL DEFAULT 0"),
              ("digest_at", "TEXT NOT NULL DEFAULT ''"), ("sso_provider", "TEXT NOT NULL DEFAULT ''"),
              ("sso_subject", "TEXT NOT NULL DEFAULT ''")],
}

# indexes for the pages that are opened most (after the new columns exist)
V4_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tasks_ws_status ON tasks(workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_ws_due ON tasks(workspace_id, due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_ext ON tasks(workspace_id, ext_uid);
CREATE INDEX IF NOT EXISTS idx_notifications_seen ON notifications(user_id, seen);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, next_try);
CREATE INDEX IF NOT EXISTS idx_activity_ws ON activity(workspace_id, id);
"""

BASE_STATES = ("todo", "in_progress", "review", "done")
FIELD_KINDS = {"text": "Text", "number": "Number", "date": "Date", "select": "Choice", "checkbox": "Yes / no"}
TRIGGERS = {"task_created": "A task is created", "moved_to": "A task is moved to", "commented": "Somebody comments"}
ACTIONS = {"assign": "Assign it to", "priority": "Set the priority to", "label": "Add the label",
           "status": "Move it to", "due_in": "Set the due date (days from today)", "notify": "Tell"}
WEBHOOK_EVENTS = {"task_created": "task.created", "task_updated": "task.updated", "status_changed": "task.moved",
                  "task_completed": "task.completed", "task_deleted": "task.deleted", "commented": "comment.created"}
VIEW_KEYS = {"q", "status", "priority", "assignee", "project", "due", "sort", "label", "sprint", "creator"}
BACKOFF_MINUTES = (1, 5, 30, 120)
MAX_TRIES = 5
LOGIN_WINDOW_MIN, LOGIN_MAX_ACCOUNT, LOGIN_MAX_ADDRESS = 15, 5, 20
ICS_MAX_BYTES, ICS_MAX_ITEMS = 2 * 1024 * 1024, 500
SYNC_HOURS = 6
WORD = re.compile(r"[^\W\d_]{3,}", re.U)
STOP = {"the", "and", "for", "with", "from", "into", "new", "add", "make"}

_auto_guard = threading.local()


def words(text):
    return [w for w in WORD.findall((text or "").lower()) if w not in STOP]


def ago(minutes=0, hours=0, days=0):
    return (datetime.now() - timedelta(minutes=minutes, hours=hours, days=days)).strftime("%Y-%m-%d %H:%M:%S")


def later(minutes):
    return (datetime.now() + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------- calendar files (.ics)
def ics_unescape(text):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), text)


def parse_ics(text):
    """Events and to-dos of an iCalendar file: [{uid, title, description, due, done}] (RFC 5545, the parts we need)."""
    lines = re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n")).split("\n")       # unfold long lines
    items, cur = [], None
    for line in lines:
        if line.upper() in ("BEGIN:VEVENT", "BEGIN:VTODO"):
            cur = {"kind": line[6:].upper(), "uid": "", "title": "", "description": "", "due": None, "done": False}
            continue
        if line.upper() in ("END:VEVENT", "END:VTODO"):
            if cur and cur["title"]:
                items.append(cur)
            cur = None
            if len(items) >= ICS_MAX_ITEMS:
                break
            continue
        if cur is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name = head.split(";", 1)[0].upper()
        if name == "UID":
            cur["uid"] = value.strip()[:200]
        elif name == "SUMMARY":
            cur["title"] = " ".join(ics_unescape(value).split())[:120]
        elif name == "DESCRIPTION":
            cur["description"] = ics_unescape(value).strip()[:5000]
        elif name in ("DUE", "DTSTART") and (name == "DUE" or cur["kind"] == "VEVENT" or not cur["due"]):
            m = re.match(r"(\d{4})(\d{2})(\d{2})", value.strip())
            if m:
                try:
                    cur["due"] = date(int(m[1]), int(m[2]), int(m[3])).isoformat()
                except ValueError:
                    pass
        elif name == "STATUS" and value.strip().upper() in ("COMPLETED", "CANCELLED"):
            cur["done"] = value.strip().upper() == "COMPLETED"
    return items


def public_https_url(url, resolver=None):
    """Only https addresses of public internet hosts (no localhost / private network): protects the server (SSRF)."""
    url = (url or "").strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password or len(url) > 1000:
        raise TaskError("Use an https:// address.")
    try:
        infos = (resolver or socket.getaddrinfo)(u.hostname, u.port or 443)
    except OSError:
        raise TaskError("This address cannot be found.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise TaskError("Addresses inside a private network are not allowed.")
    return url


class V4Mixin:
    resolver = None                           # tests put a fake DNS here
    objects = None                            # s3.ObjectStore when S3_BUCKET is set

    def _v4_init(self):
        with self.db() as con:
            for table, cols in V4_MIGRATIONS.items():
                have = set(con.columns(table))
                for col, sql in cols:
                    if col not in have:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sql}")
            con.executescript(V4_INDEXES)
        import s3
        self.objects = s3.ObjectStore(self.env)

    # ================================================================ called by every log line
    def _v4_after_log(self, con, user_id, action, details, task_id, ws):
        if not ws or ws < 0:
            return
        if self.is_postgres:
            import livebus
            livebus.publish(con, ws)
        if task_id:
            moved = action in ("status_changed", "task_completed") or (
                action == "task_updated" and "status" in [x.strip() for x in (details or "").split(",")])
            if action == "task_created":
                self.run_automations(con, ws, "task_created", task_id, user_id)
            if moved:
                self.run_automations(con, ws, "moved_to", task_id, user_id)
            if action == "commented":
                self.run_automations(con, ws, "commented", task_id, user_id)
        if action in WEBHOOK_EVENTS:
            self.queue_webhooks(con, ws, WEBHOOK_EVENTS[action], task_id, user_id, details)

    # ================================================================ board columns
    def columns(self, ws):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT * FROM board_columns WHERE workspace_id = ? ORDER BY position, id", (ws,))]

    def start_custom_columns(self, admin_id, ws):
        """Make the four usual columns as custom ones, so they can be renamed and more can be added."""
        from store import STATUS_NAMES
        with self.db() as con:
            if con.execute("SELECT COUNT(*) FROM board_columns WHERE workspace_id = ?", (ws,)).fetchone()[0]:
                return
            for i, st in enumerate(BASE_STATES):
                con.execute("INSERT INTO board_columns (workspace_id, name, base_status, position, wip_limit, created_at) "
                            "VALUES (?,?,?,?,?,?)", (ws, STATUS_NAMES[st], st, i, 0, now()))
            self.log(con, admin_id, "settings_updated", "board columns", ws=ws)

    def save_column(self, admin_id, ws, name, base_status, wip_limit=0, column_id=None):
        name = " ".join((name or "").split())
        if not 1 <= len(name) <= 30:
            raise TaskError("A column name has 1 to 30 characters.")
        if base_status not in BASE_STATES:
            raise TaskError("Choose the state the column belongs to.")
        try:
            wip = int(wip_limit or 0)
        except (TypeError, ValueError):
            raise TaskError("The WIP limit is a whole number (0 = no limit).")
        if not 0 <= wip <= 99:
            raise TaskError("The WIP limit is a whole number (0 = no limit).")
        with self.db() as con:
            cols = [dict(r) for r in con.execute("SELECT * FROM board_columns WHERE workspace_id = ?", (ws,))]
            if any(c["name"].lower() == name.lower() and c["id"] != column_id for c in cols):
                raise TaskError("There is already a column with this name.")
            if column_id:
                old = next((c for c in cols if c["id"] == column_id), None)
                if not old:
                    raise TaskError("Column not found.")
                if old["base_status"] != base_status and not any(
                        c["base_status"] == old["base_status"] and c["id"] != column_id for c in cols):
                    raise TaskError("Every state needs at least one column.")
                con.execute("UPDATE board_columns SET name = ?, base_status = ?, wip_limit = ? WHERE id = ?",
                            (name, base_status, wip, column_id))
                con.execute("UPDATE tasks SET column_id = NULL WHERE column_id = ? AND status != ?", (column_id, base_status))
            else:
                if len(cols) >= 12:
                    raise TaskError("A board can have at most 12 columns.")
                pos = max([c["position"] for c in cols] + [0]) + 1
                column_id = con.execute("INSERT INTO board_columns (workspace_id, name, base_status, position, wip_limit, "
                                        "created_at) VALUES (?,?,?,?,?,?)", (ws, name, base_status, pos, wip, now())).lastrowid
            self.log(con, admin_id, "settings_updated", f"board column {name}", ws=ws)
            return column_id

    def delete_column(self, admin_id, ws, column_id):
        with self.db() as con:
            cols = [dict(r) for r in con.execute("SELECT * FROM board_columns WHERE workspace_id = ?", (ws,))]
            col = next((c for c in cols if c["id"] == column_id), None)
            if not col:
                raise TaskError("Column not found.")
            if not any(c["base_status"] == col["base_status"] and c["id"] != column_id for c in cols):
                raise TaskError("Every state needs at least one column.")
            con.execute("UPDATE tasks SET column_id = NULL WHERE column_id = ?", (column_id,))
            con.execute("DELETE FROM board_columns WHERE id = ?", (column_id,))
            self.log(con, admin_id, "settings_updated", f"board column {col['name']} deleted", ws=ws)

    def move_column(self, admin_id, ws, column_id, step):
        with self.db() as con:
            cols = [dict(r) for r in con.execute(
                "SELECT id FROM board_columns WHERE workspace_id = ? ORDER BY position, id", (ws,))]
            ids = [c["id"] for c in cols]
            if column_id not in ids:
                raise TaskError("Column not found.")
            i = ids.index(column_id)
            j = max(0, min(len(ids) - 1, i + (1 if step > 0 else -1)))
            ids[i], ids[j] = ids[j], ids[i]
            for pos, cid in enumerate(ids):
                con.execute("UPDATE board_columns SET position = ? WHERE id = ?", (pos, cid))

    def stop_custom_columns(self, admin_id, ws):
        with self.db() as con:
            con.execute("UPDATE tasks SET column_id = NULL WHERE workspace_id = ?", (ws,))
            con.execute("DELETE FROM board_columns WHERE workspace_id = ?", (ws,))
            self.log(con, admin_id, "settings_updated", "board columns reset", ws=ws)

    @staticmethod
    def place_in_columns(cols, tasks):
        """{column id: [tasks]}. A task stays in its column while its state matches; otherwise it goes to the first
        column of its state."""
        by_id = {c["id"]: c for c in cols}
        first = {}
        for c in cols:
            first.setdefault(c["base_status"], c["id"])
        out = {c["id"]: [] for c in cols}
        for t in tasks:
            cid = t.get("column_id")
            if not (cid in by_id and by_id[cid]["base_status"] == t["status"]):
                cid = first.get(t["status"])
            if cid in out:
                out[cid].append(t)
        return out

    def move_to_column(self, user_id, ws, task_id, column_id):
        cols = self.columns(ws)
        col = next((c for c in cols if c["id"] == column_id), None)
        if not col:
            raise TaskError("Column not found.")
        with self.db() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT id, status, column_id FROM tasks WHERE workspace_id = ? AND deleted_at IS NULL", (ws,))]
        if not any(r["id"] == task_id for r in rows):
            raise TaskError("Task not found.")
        inside = [t for t in self.place_in_columns(cols, rows)[column_id] if t["id"] != task_id]
        if col["wip_limit"] and len(inside) >= col["wip_limit"]:
            raise TaskError(f"The column “{col['name']}” is full (WIP limit {col['wip_limit']}). Finish a task first.")
        self.set_status(user_id, ws, task_id, col["base_status"])
        with self.db() as con:
            con.execute("UPDATE tasks SET column_id = ? WHERE id = ?", (column_id, task_id))
            t = con.execute("SELECT title FROM tasks WHERE id = ?", (task_id,)).fetchone()
            self.log(con, user_id, "task_updated", f"column {col['name']}", task_id, ws=ws, title=t["title"])
        return col

    # ================================================================ custom fields
    def fields(self, ws):
        with self.db() as con:
            out = [dict(r) for r in con.execute(
                "SELECT * FROM custom_fields WHERE workspace_id = ? ORDER BY position, id", (ws,))]
        for f in out:
            f["choices"] = [x for x in (f["options"] or "").split("\n") if x]
        return out

    def save_field(self, admin_id, ws, name, kind, options="", required=False, field_id=None):
        name = " ".join((name or "").split())
        if not 2 <= len(name) <= 40:
            raise TaskError("A field name has 2 to 40 characters.")
        if kind not in FIELD_KINDS:
            raise TaskError("Unknown field type.")
        choices = []
        for x in re.split(r"[\n,]", options or ""):
            x = " ".join(x.split())[:40]
            if x and x not in choices:
                choices.append(x)
        if kind == "select" and not 2 <= len(choices) <= 30:
            raise TaskError("A choice field needs 2 to 30 choices (one per line or separated by commas).")
        with self.db() as con:
            have = [dict(r) for r in con.execute("SELECT id, name FROM custom_fields WHERE workspace_id = ?", (ws,))]
            if any(h["name"].lower() == name.lower() and h["id"] != field_id for h in have):
                raise TaskError("There is already a field with this name.")
            if field_id:
                if not any(h["id"] == field_id for h in have):
                    raise TaskError("Field not found.")
                con.execute("UPDATE custom_fields SET name = ?, options = ?, required = ? WHERE id = ?",
                            (name, "\n".join(choices) if kind == "select" else "", 1 if required else 0, field_id))
            else:
                if len(have) >= 20:
                    raise TaskError("A workspace can have at most 20 custom fields.")
                field_id = con.execute(
                    "INSERT INTO custom_fields (workspace_id, name, kind, options, required, position, created_at) "
                    "VALUES (?,?,?,?,?,?,?)", (ws, name, kind, "\n".join(choices) if kind == "select" else "",
                                               1 if required else 0, len(have), now())).lastrowid
            self.log(con, admin_id, "settings_updated", f"custom field {name}", ws=ws)
            return field_id

    def delete_field(self, admin_id, ws, field_id):
        with self.db() as con:
            f = con.execute("SELECT name FROM custom_fields WHERE id = ? AND workspace_id = ?", (field_id, ws)).fetchone()
            if not f:
                raise TaskError("Field not found.")
            con.execute("DELETE FROM field_values WHERE field_id = ?", (field_id,))
            con.execute("DELETE FROM custom_fields WHERE id = ?", (field_id,))
            self.log(con, admin_id, "settings_updated", f"custom field {f['name']} deleted", ws=ws)

    def clean_field_values(self, ws, form):
        """Check the cf_<id> values of a form. Returns {field id: text}; raises TaskError for a wrong value."""
        out = {}
        for f in self.fields(ws):
            raw = form.get(f"cf_{f['id']}", "")
            v = " ".join(str(raw).split()) if f["kind"] != "text" else str(raw).strip()
            if f["kind"] == "checkbox":
                v = "1" if raw in ("1", "on", "yes", "true", True) else ""
            elif f["kind"] == "number" and v:
                try:
                    num = float(v.replace(",", "."))
                except ValueError:
                    raise TaskError(f"“{f['name']}” must be a number.")
                if not math.isfinite(num) or abs(num) > 1e12:
                    raise TaskError(f"“{f['name']}” must be a number.")
                v = ("%d" % num) if num == int(num) else ("%g" % num)
            elif f["kind"] == "date" and v:
                try:
                    v = date.fromisoformat(v).isoformat()
                except ValueError:
                    raise TaskError(f"“{f['name']}” must be a date.")
            elif f["kind"] == "select" and v and v not in f["choices"]:
                raise TaskError(f"Choose one of the values of “{f['name']}”.")
            elif f["kind"] == "text":
                v = v[:500]
            if f["required"] and not v and f["kind"] != "checkbox":
                raise TaskError(f"“{f['name']}” is required.")
            out[f["id"]] = v
        return out

    def set_field_values(self, ws, task_id, values):
        with self.db() as con:
            if not con.execute("SELECT 1 FROM tasks WHERE id = ? AND workspace_id = ?", (task_id, ws)).fetchone():
                raise TaskError("Task not found.")
            for fid, v in values.items():
                con.execute("DELETE FROM field_values WHERE task_id = ? AND field_id = ?", (task_id, fid))
                if v:
                    con.execute("INSERT INTO field_values (task_id, field_id, value) VALUES (?,?,?)", (task_id, fid, v))

    def field_values(self, task_ids):
        ids = [int(i) for i in task_ids]
        out = {i: {} for i in ids}
        if not ids:
            return out
        with self.db() as con:
            for r in con.execute(f"SELECT task_id, field_id, value FROM field_values WHERE task_id IN "
                                 f"({','.join('?' * len(ids))})", ids):
                out[r[0]][r[1]] = r[2]
        return out

    # ================================================================ automations
    def automations(self, ws):
        with self.db() as con:
            return [dict(r) for r in con.execute("SELECT * FROM automations WHERE workspace_id = ? ORDER BY id", (ws,))]

    def save_automation(self, admin_id, ws, f):
        from store import PRIORITY_NAMES, STATUS_NAMES
        name = " ".join((f.get("name") or "").split())[:60]
        trig, tval = f.get("trigger_kind", ""), (f.get("trigger_value") or "").strip()
        act, aval = f.get("action_kind", ""), (f.get("action_value") or "").strip()
        if trig not in TRIGGERS or act not in ACTIONS:
            raise TaskError("Choose when the rule runs and what it does.")
        if trig == "moved_to" and tval not in STATUS_NAMES:
            raise TaskError("Choose the state.")
        if trig != "moved_to":
            tval = ""
        project = int(f["project_id"]) if str(f.get("project_id") or "").isdigit() else None
        with self.db() as con:
            if project and not con.execute("SELECT 1 FROM projects WHERE id = ? AND workspace_id = ?",
                                           (project, ws)).fetchone():
                raise TaskError("The selected project does not exist.")
            if act in ("assign", "notify"):
                if not (aval.isdigit() and con.execute("SELECT 1 FROM memberships WHERE workspace_id = ? AND user_id = ?",
                                                       (ws, int(aval))).fetchone()):
                    raise TaskError("Choose a member of the workspace.")
            elif act == "priority" and aval not in PRIORITY_NAMES:
                raise TaskError("Choose a priority.")
            elif act == "status" and aval not in STATUS_NAMES:
                raise TaskError("Choose the state.")
            elif act == "label" and not (aval.isdigit() and con.execute(
                    "SELECT 1 FROM labels WHERE id = ? AND workspace_id = ?", (int(aval), ws)).fetchone()):
                raise TaskError("Choose a label.")
            elif act == "due_in" and not (aval.isdigit() and 0 <= int(aval) <= 365):
                raise TaskError("The number of days is 0 to 365.")
            if trig == "moved_to" and act == "status":
                raise TaskError("A rule cannot move a task when it is moved (it would never stop).")
            if con.execute("SELECT COUNT(*) FROM automations WHERE workspace_id = ?", (ws,)).fetchone()[0] >= 30:
                raise TaskError("A workspace can have at most 30 rules.")
            rid = con.execute("INSERT INTO automations (workspace_id, name, trigger_kind, trigger_value, project_id, "
                              "action_kind, action_value, active, runs, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                              (ws, name or f"{TRIGGERS[trig]} → {ACTIONS[act]}", trig, tval, project, act, aval, 1, 0,
                               admin_id, now())).lastrowid
            self.log(con, admin_id, "settings_updated", f"automation {name}", ws=ws)
            return rid

    def toggle_automation(self, admin_id, ws, rid):
        with self.db() as con:
            r = con.execute("SELECT active FROM automations WHERE id = ? AND workspace_id = ?", (rid, ws)).fetchone()
            if not r:
                raise TaskError("Rule not found.")
            con.execute("UPDATE automations SET active = ? WHERE id = ?", (0 if r[0] else 1, rid))

    def delete_automation(self, admin_id, ws, rid):
        with self.db() as con:
            con.execute("DELETE FROM automations WHERE id = ? AND workspace_id = ?", (rid, ws))

    def run_automations(self, con, ws, trigger, task_id, user_id):
        """Run the active rules of the workspace for one task. Changes made by a rule do not start other rules
        (no endless loops)."""
        if getattr(_auto_guard, "on", False):
            return 0
        from store import PRIORITY_NAMES, STATUS_NAMES
        t = con.execute("SELECT id, title, status, project_id, assignee_id FROM tasks WHERE id = ? AND workspace_id = ?",
                        (task_id, ws)).fetchone()
        if not t:
            return 0
        rules = con.execute("SELECT * FROM automations WHERE workspace_id = ? AND active = 1 AND trigger_kind = ? "
                            "ORDER BY id", (ws, trigger)).fetchall()
        done = 0
        _auto_guard.on = True
        try:
            for r in rules:
                if r["project_id"] and r["project_id"] != t["project_id"]:
                    continue
                if trigger == "moved_to" and r["trigger_value"] != t["status"]:
                    continue
                act, val = r["action_kind"], r["action_value"]
                note = f"rule “{r['name']}”"
                if act == "assign" and val.isdigit() and t["assignee_id"] != int(val):
                    con.execute("UPDATE tasks SET assignee_id = ?, updated_at = ? WHERE id = ?", (int(val), now(), task_id))
                    if int(val) != user_id:
                        self._notify_assigned(con, user_id, int(val), task_id, t["title"], ws)
                elif act == "priority" and val in PRIORITY_NAMES:
                    con.execute("UPDATE tasks SET priority = ?, updated_at = ? WHERE id = ?", (val, now(), task_id))
                elif act == "label" and val.isdigit():
                    if not con.execute("SELECT 1 FROM task_labels WHERE task_id = ? AND label_id = ?",
                                       (task_id, int(val))).fetchone():
                        con.execute("INSERT INTO task_labels (task_id, label_id) VALUES (?, ?)", (task_id, int(val)))
                elif act == "status" and val in STATUS_NAMES and t["status"] != val:
                    con.execute("UPDATE tasks SET status = ?, updated_at = ?, completed_at = ?, column_id = NULL "
                                "WHERE id = ?", (val, now(), now() if val == "done" else None, task_id))
                elif act == "due_in" and val.isdigit():
                    due = (date.today() + timedelta(days=int(val))).isoformat()
                    con.execute("UPDATE tasks SET due_date = ?, reminded_for = '', updated_at = ? WHERE id = ?",
                                (due, now(), task_id))
                elif act == "notify" and val.isdigit():
                    self.notify(con, int(val), "automation", f"{t['title']}: {r['name']}", ws=ws, task_id=task_id)
                else:
                    continue
                con.execute("UPDATE automations SET runs = runs + 1 WHERE id = ?", (r["id"],))
                self.log(con, user_id, "task_updated", note, task_id, ws=ws, title=t["title"])
                done += 1
        finally:
            _auto_guard.on = False
        return done

    # ================================================================ saved views
    def saved_views(self, user_id, ws):
        with self.db() as con:
            return [dict(r) for r in con.execute("SELECT * FROM saved_views WHERE user_id = ? AND workspace_id = ? "
                                                 "ORDER BY lower(name)", (user_id, ws))]

    def save_view(self, user_id, ws, name, page, query):
        name = " ".join((name or "").split())
        if not 2 <= len(name) <= 40:
            raise TaskError("Give the view a name of 2 to 40 characters.")
        page = page if page in ("tasks", "board") else "tasks"
        pairs = [(k, v[:80]) for k, v in urllib.parse.parse_qsl(query or "") if (k in VIEW_KEYS or k.startswith("cf_")) and v]
        with self.db() as con:
            if con.execute("SELECT COUNT(*) FROM saved_views WHERE user_id = ? AND workspace_id = ?",
                           (user_id, ws)).fetchone()[0] >= 20:
                raise TaskError("You can save at most 20 views. Delete one first.")
            return con.execute("INSERT INTO saved_views (user_id, workspace_id, name, page, query, created_at) "
                               "VALUES (?,?,?,?,?,?)", (user_id, ws, name, page, urllib.parse.urlencode(pairs), now())).lastrowid

    def delete_view(self, user_id, view_id):
        with self.db() as con:
            con.execute("DELETE FROM saved_views WHERE id = ? AND user_id = ?", (view_id, user_id))

    # ================================================================ task templates
    def templates(self, ws):
        with self.db() as con:
            out = [dict(r) for r in con.execute("SELECT * FROM task_templates WHERE workspace_id = ? ORDER BY lower(name)",
                                                (ws,))]
        for t in out:
            t["steps"] = [x for x in t["checklist"].split("\n") if x]
            t["labels"] = [int(x) for x in t["label_ids"].split(",") if x.isdigit()]
        return out

    def template(self, ws, tid):
        return next((t for t in self.templates(ws) if t["id"] == tid), None)

    def save_template(self, user_id, ws, f, template_id=None):
        from core import parse_duration
        from store import PRIORITY_NAMES
        name = " ".join((f.get("name") or "").split())
        title = " ".join((f.get("title") or "").split())
        if not 2 <= len(name) <= 60:
            raise TaskError("Give the template a name of 2 to 60 characters.")
        if not 3 <= len(title) <= 120:
            raise TaskError("Task title must have at least 3 characters.")
        priority = f.get("priority") or "medium"
        if priority not in PRIORITY_NAMES:
            raise TaskError("Unknown status or priority.")
        steps = [" ".join(x.split())[:200] for x in (f.get("checklist") or "").split("\n") if x.strip()][:30]
        labels = f.get("labels") or []
        project = int(f["project_id"]) if str(f.get("project_id") or "").isdigit() else None
        with self.db() as con:
            if project and not con.execute("SELECT 1 FROM projects WHERE id = ? AND workspace_id = ?",
                                           (project, ws)).fetchone():
                raise TaskError("The selected project does not exist.")
            ok = {r[0] for r in con.execute("SELECT id FROM labels WHERE workspace_id = ?", (ws,))}
            label_ids = ",".join(str(i) for i in sorted({int(x) for x in labels if str(x).isdigit()} & ok))
            values = (name, title, (f.get("description") or "").strip()[:5000], priority, project,
                      parse_duration(f.get("estimate")), "\n".join(steps), label_ids)
            if template_id:
                con.execute("UPDATE task_templates SET name=?, title=?, description=?, priority=?, project_id=?, "
                            "estimate_minutes=?, checklist=?, label_ids=? WHERE id = ? AND workspace_id = ?",
                            values + (template_id, ws))
            else:
                if con.execute("SELECT COUNT(*) FROM task_templates WHERE workspace_id = ?", (ws,)).fetchone()[0] >= 50:
                    raise TaskError("A workspace can have at most 50 templates.")
                template_id = con.execute(
                    "INSERT INTO task_templates (name, title, description, priority, project_id, estimate_minutes, "
                    "checklist, label_ids, workspace_id, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    values + (ws, user_id, now())).lastrowid
            self.log(con, user_id, "settings_updated", f"template {name}", ws=ws)
            return template_id

    def delete_template(self, user_id, ws, template_id):
        with self.db() as con:
            con.execute("DELETE FROM task_templates WHERE id = ? AND workspace_id = ?", (template_id, ws))

    def task_from_template(self, user_id, ws, template_id, extra=None):
        t = self.template(ws, template_id)
        if not t:
            raise TaskError("Template not found.")
        from core import fmt_minutes
        fields = {"title": t["title"], "description": t["description"], "priority": t["priority"],
                  "project_id": t["project_id"] or "", "status": "todo", "labels": t["labels"],
                  "estimate": fmt_minutes(t["estimate_minutes"]) if t["estimate_minutes"] else ""}
        fields.update(extra or {})
        tid = self.create_task(user_id, ws, fields)
        for step in t["steps"]:
            self.add_subtask(user_id, tid, step)
        return tid

    # ================================================================ API tokens
    @staticmethod
    def _token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def api_tokens(self, user_id):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT a.id, a.name, a.prefix, a.scope, a.last_used, a.created_at, a.workspace_id, w.name AS ws_name "
                "FROM api_tokens a LEFT JOIN workspaces w ON w.id = a.workspace_id "
                "WHERE a.user_id = ? AND a.revoked_at IS NULL ORDER BY a.id DESC", (user_id,))]

    def create_api_token(self, user_id, ws, name, scope="read"):
        name = " ".join((name or "").split())
        if not 2 <= len(name) <= 40:
            raise TaskError("Give the key a name of 2 to 40 characters.")
        if scope not in ("read", "write"):
            raise TaskError("Choose read or read and write.")
        token = "tf_" + secrets.token_urlsafe(30)
        with self.db() as con:
            if con.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id = ? AND revoked_at IS NULL",
                           (user_id,)).fetchone()[0] >= 10:
                raise TaskError("You can have at most 10 keys. Delete one first.")
            con.execute("INSERT INTO api_tokens (user_id, workspace_id, name, token_hash, prefix, scope, created_at) "
                        "VALUES (?,?,?,?,?,?,?)", (user_id, ws, name, self._token_hash(token), token[:10], scope, now()))
            self.log(con, user_id, "api_key_created", name, ws=ws)
        return token

    def revoke_api_token(self, user_id, token_id):
        with self.db() as con:
            con.execute("UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND user_id = ?", (now(), token_id, user_id))

    def api_auth(self, token):
        """(user, workspace id, scope) for a valid key, else None."""
        if not token or not token.startswith("tf_") or len(token) > 100:
            return None
        with self.db() as con:
            r = con.execute("SELECT * FROM api_tokens WHERE token_hash = ? AND revoked_at IS NULL",
                            (self._token_hash(token),)).fetchone()
            if not r:
                return None
            u = con.execute("SELECT * FROM users WHERE id = ? AND active = 1", (r["user_id"],)).fetchone()
            m = con.execute("SELECT role FROM memberships WHERE workspace_id = ? AND user_id = ?",
                            (r["workspace_id"], r["user_id"])).fetchone()
            if not u or not m:
                return None
            if not r["last_used"] or r["last_used"] < ago(minutes=1):
                con.execute("UPDATE api_tokens SET last_used = ? WHERE id = ?", (now(), r["id"]))
        return dict(u, role=m["role"], site_admin=False), r["workspace_id"], r["scope"]

    # ================================================================ webhooks
    def webhooks(self, ws):
        with self.db() as con:
            return [dict(r) for r in con.execute("SELECT * FROM webhooks WHERE workspace_id = ? ORDER BY id", (ws,))]

    def save_webhook(self, admin_id, ws, url, events):
        url = public_https_url(url, self.resolver)
        events = [e for e in events if e in WEBHOOK_EVENTS.values()]
        if not events:
            raise TaskError("Choose at least one event.")
        secret = "whsec_" + secrets.token_urlsafe(24)
        with self.db() as con:
            if con.execute("SELECT COUNT(*) FROM webhooks WHERE workspace_id = ?", (ws,)).fetchone()[0] >= 5:
                raise TaskError("A workspace can have at most 5 webhooks.")
            hid = con.execute("INSERT INTO webhooks (workspace_id, url, secret, events, active, created_by, created_at) "
                              "VALUES (?,?,?,?,?,?,?)", (ws, url, secret, ",".join(events), 1, admin_id, now())).lastrowid
            self.log(con, admin_id, "settings_updated", "webhook " + urllib.parse.urlsplit(url).hostname, ws=ws)
        return hid, secret

    def delete_webhook(self, admin_id, ws, hook_id):
        with self.db() as con:
            con.execute("DELETE FROM webhooks WHERE id = ? AND workspace_id = ?", (hook_id, ws))

    @staticmethod
    def sign_webhook(secret, body):
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def queue_webhooks(self, con, ws, event, task_id, user_id, details=""):
        hooks = con.execute("SELECT id, events FROM webhooks WHERE workspace_id = ? AND active = 1", (ws,)).fetchall()
        hooks = [h for h in hooks if event in h["events"].split(",")]
        if not hooks:
            return
        task = None
        if task_id:
            t = con.execute("SELECT id, title, status, priority, assignee_id, project_id, due_date, updated_at FROM tasks "
                            "WHERE id = ?", (task_id,)).fetchone()
            task = dict(t) if t else {"id": task_id}
        body = json.dumps({"event": event, "workspace": ws, "task": task, "by": user_id, "details": details,
                           "at": now()}, ensure_ascii=False, default=str)
        ids = [self.outbox_add(con, "webhook", f"webhook:{h['id']}", event, body) for h in hooks]
        con.after_commit.append(lambda: self._send_outbox_later(ids))

    def _send_outbox_later(self, ids):
        if self.http is not None:                       # tests: at once
            for i in ids:
                self.send_outbox_item(i)
        else:
            threading.Thread(target=lambda: [self.send_outbox_item(i) for i in ids], daemon=True).start()

    # ================================================================ outbox: sending and retries
    def send_outbox_item(self, outbox_id):
        with self.db() as con:
            row = con.execute("SELECT * FROM outbox WHERE id = ?", (outbox_id,)).fetchone()
            row = dict(row) if row else None
        if not row:
            return None
        try:
            if row["channel"] == "webhook":
                hid = int(row["to_addr"].split(":", 1)[1])
                with self.db() as con:
                    h = con.execute("SELECT url, secret FROM webhooks WHERE id = ?", (hid,)).fetchone()
                if not h:
                    raise RuntimeError("the webhook was deleted")
                public_https_url(h["url"], self.resolver)                       # checked again at every send
                body = row["body"].encode()
                req = urllib.request.Request(h["url"], data=body, method="POST", headers={
                    "Content-Type": "application/json", "User-Agent": "TaskFlow-Webhook/4",
                    "X-TaskFlow-Event": row["subject"], "X-TaskFlow-Delivery": str(row["id"]),
                    "X-TaskFlow-Signature": self.sign_webhook(h["secret"], body)})
                with (self.http or urllib.request.urlopen)(req, timeout=10) as resp:
                    if resp.status >= 300:
                        raise RuntimeError(f"answered {resp.status}")
            elif row["channel"] == "email":
                if is_example_email(row["to_addr"]):
                    status = "not sent: demo or test address"
                    with self.db() as con:
                        con.execute("UPDATE outbox SET status = ? WHERE id = ?", (status, outbox_id))
                    return status
                if not (self.notifier and self.notifier.email_provider()):
                    raise RuntimeError("no email service")
                self.notifier.send_email(row["to_addr"], row["subject"], row["body"])
            else:
                return row["status"]
            status, nxt = "sent", None
        except Exception as e:
            tries = (row["attempts"] or 0) + 1
            if tries >= MAX_TRIES:
                status, nxt = f"failed: {str(e)[:120]} (gave up after {tries} tries)", None
            else:
                status, nxt = f"failed: {str(e)[:120]} (try {tries} of {MAX_TRIES})", later(BACKOFF_MINUTES[tries - 1])
        with self.db() as con:
            con.execute("UPDATE outbox SET status = ?, attempts = attempts + 1, next_try = ? WHERE id = ?",
                        (status[:200], nxt, outbox_id))
            if row["channel"] == "webhook":
                con.execute("UPDATE webhooks SET last_status = ? WHERE id = ?",
                            (f"{now()} {status}"[:200], int(row["to_addr"].split(":", 1)[1])))
        return status

    def retry_outbox(self, limit=25):
        """Try again the emails and webhooks that failed (or were never sent, e.g. the server restarted)."""
        with self.db() as con:
            ids = [r[0] for r in con.execute(
                "SELECT id FROM outbox WHERE channel IN ('email', 'webhook') AND attempts < ? AND "
                "((lower(status) LIKE 'failed%' AND status NOT LIKE '%gave up%' AND (next_try IS NULL OR next_try <= ?)) "
                "OR (status = 'waiting' AND created_at < ?)) ORDER BY id LIMIT ?",
                (MAX_TRIES, now(), ago(minutes=5), limit))]
        return [self.send_outbox_item(i) for i in ids]

    def retry_outbox_now(self, admin_id, outbox_id):
        with self.db() as con:
            con.execute("UPDATE outbox SET attempts = 0, next_try = NULL WHERE id = ?", (outbox_id,))
            self.log(con, admin_id, "outbox_retry", str(outbox_id))
        return self.send_outbox_item(outbox_id)

    # ================================================================ weekly digest
    def set_digest(self, user_id, on):
        with self.db() as con:
            con.execute("UPDATE users SET digest = ? WHERE id = ?", (1 if on else 0, user_id))

    def digest_text(self, user_id, today=None):
        import i18n
        today = today or date.today()
        week = (today + timedelta(days=7)).isoformat()
        with self.db() as con:
            u = con.execute("SELECT name, lang FROM users WHERE id = ?", (user_id,)).fetchone()
            rows = [dict(r) for r in con.execute(
                "SELECT t.id, t.title, t.due_date, w.name AS ws FROM tasks t JOIN workspaces w ON w.id = t.workspace_id "
                "JOIN memberships m ON m.workspace_id = t.workspace_id AND m.user_id = t.assignee_id "
                "WHERE t.assignee_id = ? AND t.status != 'done' AND t.deleted_at IS NULL AND t.due_date IS NOT NULL "
                "AND t.due_date <= ? ORDER BY t.due_date, t.id LIMIT 40", (user_id, week))]
        if not u or not rows:
            return None
        lang = u["lang"] or "en"
        tr = lambda s: i18n.translate_text(s, lang)                       # noqa: E731
        late = [r for r in rows if r["due_date"] < today.isoformat()]
        soon = [r for r in rows if r["due_date"] >= today.isoformat()]
        base = self.env.get("APP_URL", "").rstrip("/")
        lines = [tr("Hello {0},").replace("{0}", u["name"].split()[0]), "", tr("Your tasks for this week:"), ""]
        for head, part in ((tr("Overdue"), late), (tr("Due in the next 7 days"), soon)):
            if part:
                lines.append(f"{head} ({len(part)})")
                lines += [f"  - {r['due_date']}  {r['title']}  [{r['ws']}]" + (f"  {base}/tasks/{r['id']}" if base else "")
                          for r in part]
                lines.append("")
        lines.append(self.site("mail_signature"))
        return tr("Your TaskFlow week"), "\n".join(lines)

    def send_digests(self, today=None, force=False):
        """Monday (or the first visit after it): one email per person who switched the digest on."""
        today = today or date.today()
        if not force and today.weekday() != 0:
            return 0
        if not (self.notifier and self.notifier.email_provider()):
            return 0
        with self.db() as con:
            people = [dict(r) for r in con.execute(
                "SELECT id, email FROM users WHERE digest = 1 AND active = 1 AND digest_at < ?", (today.isoformat(),))]
        sent = 0
        for p in people:
            with self.db() as con:
                con.execute("UPDATE users SET digest_at = ? WHERE id = ?", (today.isoformat(), p["id"]))
            if is_example_email(p["email"]):
                continue
            msg = self.digest_text(p["id"], today)
            if not msg:
                continue
            with self.db() as con:
                oid = self.outbox_add(con, "email", p["email"], msg[0], msg[1])
            self.send_outbox_item(oid)
            sent += 1
        return sent

    # ================================================================ calendar import and subscriptions
    def import_ics(self, user_id, ws, text, project_id=None):
        """Create tasks from the events / to-dos of a calendar; update the ones that came from TaskFlow or from an
        earlier import (same UID). Returns {created, updated, skipped}."""
        items = parse_ics(text or "")
        if not items:
            raise TaskError("No events or to-dos were found in this calendar.")
        project = int(project_id) if str(project_id or "").isdigit() else None
        out = {"created": 0, "updated": 0, "skipped": 0}
        for it in items:
            m = re.match(r"task-(\d+)@", it["uid"])
            with self.db() as con:
                if m:
                    row = con.execute("SELECT id, due_date, status FROM tasks WHERE id = ? AND workspace_id = ? AND "
                                      "deleted_at IS NULL", (int(m.group(1)), ws)).fetchone()
                else:
                    row = con.execute("SELECT id, due_date, status FROM tasks WHERE workspace_id = ? AND ext_uid = ? AND "
                                      "ext_uid != '' AND deleted_at IS NULL", (ws, it["uid"])).fetchone() if it["uid"] else None
                if row:
                    changed = []
                    if it["due"] and it["due"] != row["due_date"]:
                        con.execute("UPDATE tasks SET due_date = ?, reminded_for = '', updated_at = ? WHERE id = ?",
                                    (it["due"], now(), row["id"]))
                        changed.append("due date")
                    if changed:
                        self.log(con, user_id, "task_updated", ", ".join(changed) + " (calendar)", row["id"], ws=ws)
                        out["updated"] += 1
                    else:
                        out["skipped"] += 1
                    continue
            title = it["title"] if len(it["title"]) >= 3 else (it["title"] + " …")[:120]
            try:
                tid = self.create_task(user_id, ws, {"title": title, "description": it["description"],
                                                     "due_date": it["due"] or "", "project_id": project or "",
                                                     "status": "done" if it["done"] else "todo", "_import": True})
            except TaskError:
                out["skipped"] += 1
                continue
            if it["uid"]:
                with self.db() as con:
                    con.execute("UPDATE tasks SET ext_uid = ? WHERE id = ?", (it["uid"], tid))
            out["created"] += 1
        return out

    def calendar_subs(self, user_id, ws):
        with self.db() as con:
            return [dict(r) for r in con.execute("SELECT * FROM calendar_subs WHERE user_id = ? AND workspace_id = ? "
                                                 "ORDER BY id", (user_id, ws))]

    def add_calendar_sub(self, user_id, ws, name, url, project_id=None):
        url = public_https_url(url, self.resolver)
        name = " ".join((name or "").split())[:60] or urllib.parse.urlsplit(url).hostname
        project = int(project_id) if str(project_id or "").isdigit() else None
        with self.db() as con:
            if con.execute("SELECT COUNT(*) FROM calendar_subs WHERE user_id = ?", (user_id,)).fetchone()[0] >= 5:
                raise TaskError("You can follow at most 5 calendars.")
            sid = con.execute("INSERT INTO calendar_subs (user_id, workspace_id, project_id, name, url, created_at) "
                              "VALUES (?,?,?,?,?,?)", (user_id, ws, project, name, url, now())).lastrowid
        self.sync_calendar_sub(sid)
        return sid

    def delete_calendar_sub(self, user_id, sid):
        with self.db() as con:
            con.execute("DELETE FROM calendar_subs WHERE id = ? AND user_id = ?", (sid, user_id))

    def sync_calendar_sub(self, sid):
        with self.db() as con:
            s = con.execute("SELECT * FROM calendar_subs WHERE id = ?", (sid,)).fetchone()
        if not s:
            return None
        try:
            url = public_https_url(s["url"], self.resolver)
            req = urllib.request.Request(url, headers={"Accept": "text/calendar", "User-Agent": "TaskFlow-Calendar/4"})
            with (self.http or urllib.request.urlopen)(req, timeout=15) as resp:
                data = resp.read(ICS_MAX_BYTES + 1)
            if len(data) > ICS_MAX_BYTES:
                raise TaskError("The calendar is bigger than 2 MB.")
            r = self.import_ics(s["user_id"], s["workspace_id"], data.decode("utf-8", "replace"), s["project_id"])
            status = f"{r['created']} new, {r['updated']} updated"
        except Exception as e:
            status = f"failed: {str(e)[:120]}"
        with self.db() as con:
            con.execute("UPDATE calendar_subs SET last_sync = ?, last_status = ? WHERE id = ?", (now(), status, sid))
        return status

    def sync_due_calendars(self):
        with self.db() as con:
            ids = [r[0] for r in con.execute("SELECT id FROM calendar_subs WHERE last_sync IS NULL OR last_sync < ? "
                                             "ORDER BY id LIMIT 10", (ago(hours=SYNC_HOURS),))]
        return [self.sync_calendar_sub(i) for i in ids]

    # ================================================================ suggested estimate (version 4)
    def _estimate_rows(self, ws):
        with self.db() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT t.id, t.title, t.project_id, t.assignee_id, t.priority, t.estimate_minutes, "
                "(SELECT COALESCE(SUM(minutes), 0) FROM time_entries e WHERE e.task_id = t.id) AS logged "
                "FROM tasks t WHERE t.workspace_id = ? AND t.status = 'done' AND t.deleted_at IS NULL", (ws,))]
        labels = self.task_labels([r["id"] for r in rows])
        for r in rows:
            r["minutes"] = r["logged"] or r["estimate_minutes"]
            r["words"] = Counter(words(r["title"]))
            r["labels"] = {lb["id"] for lb in labels.get(r["id"], [])}
        return [r for r in rows if r["minutes"]]

    @staticmethod
    def _knn(rows, q, exclude=None, k=5, mode="v4"):
        mine = Counter(words(q.get("title")))
        if not mine:
            return None
        na = math.sqrt(sum(v * v for v in mine.values()))
        scored = []
        for r in rows:
            if r["id"] == exclude or not r["words"]:
                continue
            dot = sum(mine[w] * r["words"][w] for w in mine)
            cos = dot / (na * math.sqrt(sum(v * v for v in r["words"].values()))) if dot else 0.0
            if mode == "v3":
                if not cos:
                    continue
                sim = cos + (0.1 if q.get("project_id") and r["project_id"] == q.get("project_id") else 0)
                if sim < 0.2:
                    continue
            else:
                qlabels = set(q.get("labels") or ())
                jac = len(qlabels & r["labels"]) / len(qlabels | r["labels"]) if qlabels and r["labels"] else 0.0
                if not cos and not jac:
                    continue
                sim = (0.6 * cos + 0.15 * (q.get("project_id") is not None and r["project_id"] == q.get("project_id"))
                       + 0.15 * (q.get("assignee_id") is not None and r["assignee_id"] == q.get("assignee_id"))
                       + 0.1 * jac + 0.05 * (q.get("priority") is not None and r["priority"] == q.get("priority")))
                if sim < 0.25:
                    continue
            scored.append((sim, r["minutes"], r))
        scored.sort(key=lambda x: (-x[0], x[2]["id"]))
        best = scored[:k]
        if not best:
            return None
        weight = sum(s for s, _, _ in best)
        minutes = sum(s * m for s, m, _ in best) / weight
        return {"minutes": max(5, int(round(minutes / 5.0)) * 5), "count": len(best),
                "examples": [{"id": r["id"], "title": r["title"], "minutes": m, "similarity": round(min(s, 1), 2)}
                             for s, m, r in best]}

    def suggest_estimate(self, ws, title, project_id=None, exclude=None, k=5, assignee_id=None, labels=None,
                         priority=None):
        """Nearest finished tasks, now compared on title words (60 %), project (15 %), assignee (15 %), labels (10 %)
        and priority (+5 %). The answer is the similarity-weighted mean of their real logged time."""
        q = {"title": title, "project_id": project_id, "assignee_id": assignee_id,
             "labels": [int(x) for x in labels or [] if str(x).isdigit()], "priority": priority}
        return self._knn(self._estimate_rows(ws), q, exclude, k, "v4")

    def estimate_accuracy(self, ws):
        """Leave-one-out test on the finished tasks with logged time: how far the suggestion of version 3 (title only)
        and of version 4 is from the real time. Lower error is better."""
        rows = self._estimate_rows(ws)
        real = [r for r in rows if r["logged"]]
        res = {"v3": [], "v4": []}
        for r in real:
            q = {"title": r["title"], "project_id": r["project_id"], "assignee_id": r["assignee_id"],
                 "labels": list(r["labels"]), "priority": r["priority"]}
            for mode in ("v3", "v4"):
                s = self._knn(rows, q, r["id"], 5, mode)
                if s:
                    res[mode].append(abs(s["minutes"] - r["logged"]) / r["logged"])
        out = {"tasks": len(real)}
        for mode, errs in res.items():
            out[mode] = {"answered": len(errs), "error": round(100 * sum(errs) / len(errs)) if errs else None,
                         "close": round(100 * sum(1 for e in errs if e <= 0.25) / len(errs)) if errs else None}
        return out

    # ================================================================ files in object storage
    def add_attachment(self, user_id, task_id, filename, data):
        aid = super().add_attachment(user_id, task_id, filename, data)
        if self.objects is not None and self.objects.enabled:
            with self.db() as con:
                a = con.execute("SELECT workspace_id, name, mime FROM attachments WHERE id = ?", (aid,)).fetchone()
            key = f"ws{a['workspace_id']}/task{task_id}/{aid}-{secrets.token_hex(4)}-{a['name']}"
            try:
                self.objects.put(key, data, a["mime"])
                with self.db() as con:
                    con.execute("UPDATE attachments SET data = '', storage = 's3', storage_key = ? WHERE id = ?",
                                (key, aid))
            except Exception:                         # storage down: the file stays in the database
                pass
        return aid

    def attachment(self, attachment_id, with_data=False):
        d = super().attachment(attachment_id, with_data)
        if d and with_data and d.get("storage") == "s3":
            d["bytes"] = self.objects.get(d["storage_key"])
        return d

    def delete_attachment(self, user_id, attachment_id):
        with self.db() as con:
            r = con.execute("SELECT storage, storage_key FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        task_id = super().delete_attachment(user_id, attachment_id)
        if r and r["storage"] == "s3" and self.objects is not None and self.objects.enabled:
            try:
                self.objects.delete(r["storage_key"])
            except Exception:
                pass
        return task_id

    def storage_summary(self):
        with self.db() as con:
            rows = {r[0]: (r[1], r[2]) for r in con.execute(
                "SELECT storage, COUNT(*), COALESCE(SUM(size), 0) FROM attachments GROUP BY storage")}
        return {"db": rows.get("db", (0, 0)), "s3": rows.get("s3", (0, 0)),
                "s3_on": bool(self.objects is not None and self.objects.enabled)}

    # ================================================================ translation suggestions
    def suggest_translation(self, user_id, lang, english, text, note=""):
        from world import LANGUAGES
        english, text = " ".join((english or "").split()), " ".join((text or "").split())
        if lang not in LANGUAGES or lang == "en":
            raise TaskError("Choose a language other than English.")
        if not english or not text or len(english) > 500 or len(text) > 1000:
            raise TaskError("Write the English text and your better words.")
        if sorted(re.findall(r"\{\d\}", english)) != sorted(re.findall(r"\{\d\}", text)):
            raise TaskError("Keep the same gaps {0}, {1} … as the English text.")
        with self.db() as con:
            if con.execute("SELECT COUNT(*) FROM translation_suggestions WHERE user_id = ? AND created_at > ?",
                           (user_id, ago(days=1))).fetchone()[0] >= 30:
                raise TaskError("Thank you! You sent many suggestions today - please wait until tomorrow.")
            return con.execute("INSERT INTO translation_suggestions (lang, english, text, note, user_id, status, created_at) "
                               "VALUES (?,?,?,?,?,?,?)", (lang, english, text, (note or "")[:300], user_id, "pending",
                                                          now())).lastrowid

    def translation_suggestions(self, status="pending"):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT s.*, u.name AS user_name FROM translation_suggestions s LEFT JOIN users u ON u.id = s.user_id "
                "WHERE s.status = ? ORDER BY s.id DESC LIMIT 300", (status,))]

    def decide_suggestion(self, admin_id, sid, approve, text=None):
        with self.db() as con:
            s = con.execute("SELECT * FROM translation_suggestions WHERE id = ? AND status = 'pending'", (sid,)).fetchone()
            s = dict(s) if s else None
        if not s:
            raise TaskError("This suggestion was already handled.")
        if approve:
            self.save_ui_override(admin_id, s["lang"], s["english"], text or s["text"])
        with self.db() as con:
            con.execute("UPDATE translation_suggestions SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                        ("approved" if approve else "rejected", admin_id, now(), sid))

    # ================================================================ sign-in limits
    def login_wait(self, email, address):
        """Minutes to wait before the next try, 0 = go ahead."""
        email = (email or "").strip().lower()[:200]
        since = ago(minutes=LOGIN_WINDOW_MIN)
        with self.db() as con:
            acc = con.execute("SELECT COUNT(*), MIN(created_at) FROM login_attempts WHERE who = ? AND created_at > ?",
                              ("e:" + email, since)).fetchone()
            addr = con.execute("SELECT COUNT(*), MIN(created_at) FROM login_attempts WHERE who = ? AND created_at > ?",
                               ("a:" + (address or ""), since)).fetchone()
        for (count, first), most in ((acc, LOGIN_MAX_ACCOUNT), (addr, LOGIN_MAX_ADDRESS)):
            if count >= most:
                free = datetime.strptime(first, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=LOGIN_WINDOW_MIN)
                return max(1, math.ceil((free - datetime.now()).total_seconds() / 60))
        return 0

    def login_failed(self, email, address):
        with self.db() as con:
            for who in ("e:" + (email or "").strip().lower()[:200], "a:" + (address or "")):
                con.execute("INSERT INTO login_attempts (who, created_at) VALUES (?, ?)", (who, now()))

    def login_succeeded(self, email):
        with self.db() as con:
            con.execute("DELETE FROM login_attempts WHERE who = ?", ("e:" + (email or "").strip().lower()[:200],))

    # ================================================================ single sign-on
    def sso_user(self, provider, subject, email, name):
        """The account for a Google / Microsoft sign-in: linked before, or the account with the same (verified)
        email; a new account when sign-up is open. Returns the user or raises TaskError."""
        email = email.strip().lower()
        with self.db() as con:
            u = con.execute("SELECT * FROM users WHERE sso_provider = ? AND sso_subject = ?", (provider, subject)).fetchone()
            if not u:
                u = con.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
                if u:
                    con.execute("UPDATE users SET sso_provider = ?, sso_subject = ? WHERE id = ?", (provider, subject, u["id"]))
                    self.log(con, u["id"], "sso_linked", provider)
        if u:
            if not u["active"]:
                raise TaskError("This account is switched off. Please contact the administrator.")
            return self.user(u["id"])
        if self.site("allow_registration") != "1":
            raise TaskError("New accounts are closed. Ask an administrator for an invitation.")
        uid, _ws = self.register(name[:80] if len(name) >= 2 else email.split("@")[0], email,
                                 "Sso!" + secrets.token_urlsafe(18))
        with self.db() as con:
            con.execute("UPDATE users SET sso_provider = ?, sso_subject = ? WHERE id = ?", (provider, subject, uid))
            self.log(con, uid, "sso_linked", provider)
        return self.user(uid)

    # ================================================================ small things
    def set_theme(self, user_id, theme):
        if theme not in ("auto", "light", "dark"):
            raise TaskError("Unknown theme.")
        with self.db() as con:
            con.execute("UPDATE users SET theme = ? WHERE id = ?", (theme, user_id))

    def health(self):
        with self.db() as con:
            con.execute("SELECT 1").fetchone()
            waiting = con.execute("SELECT COUNT(*) FROM outbox WHERE lower(status) LIKE 'failed%' AND attempts < ?",
                                  (MAX_TRIES,)).fetchone()[0]
        return {"database": "ok", "outbox_retrying": waiting}

    def claim_jobs(self, minutes=9):
        """Only one worker / server runs the background jobs in a 10-minute window (an atomic UPDATE in the database)."""
        with self.db() as con:
            if not con.execute("SELECT 1 FROM settings WHERE key = 'jobs_claim'").fetchone():
                con.execute("INSERT INTO settings (key, value) VALUES ('jobs_claim', ?)", ("2000-01-01 00:00:00",))
        with self.db() as con:
            return con.execute("UPDATE settings SET value = ? WHERE key = 'jobs_claim' AND value < ?",
                               (now(), ago(minutes=minutes))).rowcount == 1

    def v4_housekeeping(self):
        out = {"outbox_retried": len(self.retry_outbox()), "digests": self.send_digests(),
               "calendars": len(self.sync_due_calendars())}
        with self.db() as con:
            con.execute("DELETE FROM login_attempts WHERE created_at < ?", (ago(days=1),))
        return out
