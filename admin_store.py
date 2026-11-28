"""
admin_store.py - what the site administrator manages in TaskFlow 3, and the profile of every person.

  * 20 site settings (name, logo, colour, announcement, languages, sign-up, limits, maintenance, trash, email ...)
    checked by their type and written in the audit log
  * every account: add, change everything (name, email, job, phone, bio, photo, colour, language, notifications,
    site administrator or not, active), set a password, remove two-factor login, sign out everywhere, delete
  * every workspace: change it, add / remove members and roles, a new invite link, delete
  * every resume: visibility, delete
  * the words of the interface in every language (changes of the staff are used before the built-in catalog)
  * restore of a backup file
  * own profile: photo, phone, bio, sign out on every device, download all my data
"""

import base64
import io
import json
import re
import secrets
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from core import TaskError, is_example_email, now

SITE_SETTINGS = {
    # key: (group, label, type, default, help)
    "site_name": ("Brand", "Website name", "text:2:40", "TaskFlow", "Shown in the title of every page and in emails."),
    "logo": ("Brand", "Logo", "image", "", "Square picture; it is made 128 x 128. Empty = the TaskFlow logo."),
    "accent": ("Brand", "Main colour", "color", "#4f46e5", "Buttons and links."),
    "announcement": ("Brand", "Announcement at the top of every page", "text:0:200", "", "Empty = no announcement."),
    "footer_text": ("Brand", "Line at the bottom of every page", "text:0:120", "", "Empty = the usual line."),
    "default_lang": ("Languages", "Language for new visitors", "lang", "en",
                     "Used when the browser does not ask for a language we have."),
    "langs_enabled": ("Languages", "Languages people can choose", "langs", "en,ru,fr,es,ar,zh,rw", "English is always on."),
    "allow_registration": ("Accounts", "New people can sign up", "bool", "1", "Invite links always work."),
    "max_workspaces": ("Accounts", "Most workspaces one person can create", "int:1:100", "10", ""),
    "max_resumes": ("Accounts", "Most resumes per person", "int:1:50", "10", ""),
    "max_file_mb": ("Files", "Largest file (MB)", "int:1:20", "5", "Files are kept in the database, or in S3 storage when S3_BUCKET is set."),
    "max_files": ("Files", "Most files per task", "int:1:50", "10", ""),
    "trash_days": ("Data", "Days deleted tasks stay in the trash", "int:1:365", "30", ""),
    "maintenance": ("Maintenance", "Maintenance mode", "bool", "",
                    "Only site administrators can use the site; everybody else sees the message."),
    "maintenance_text": ("Maintenance", "Message during maintenance", "text:0:200",
                         "TaskFlow is being updated. Please come back in a few minutes.", ""),
    "mail_from_name": ("Email", "Sender name of emails", "text:2:60", "TaskFlow",
                       "The mail service itself is set with BREVO_* or SMTP_* secrets."),
    "mail_signature": ("Email", "Signature under every email", "text:0:120", "- TaskFlow", ""),
}

USER_FIELDS_MAX = {"name": 80, "job_title": 80, "phone": 25, "bio": 500}


def clean_phone(text):
    text = " ".join((text or "").split())
    if text and not re.fullmatch(r"\+?[0-9 ()\-]{6,25}", text):
        raise TaskError("Please type a phone number with digits, e.g. +7 900 000 00 00.")
    return text


def picture_to_b64(data, size=256, square=True):
    """Any picture -> JPEG/PNG base64, cut to a square of `size` pixels. Refuses files that are not pictures."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not data:
        raise TaskError("Please choose a picture.")
    if len(data) > 4 * 1024 * 1024:
        raise TaskError("The picture is too big (4 MB at most).")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise TaskError("This file is not a picture (use JPG, PNG or WEBP).")
    img = ImageOps.exif_transpose(img)
    if square:
        img = ImageOps.fit(img.convert("RGB"), (size, size), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=86)
    else:
        img = img.convert("RGBA")
        img.thumbnail((size, size), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode()


class AdminMixin:

    # ================================================================ site settings
    def site(self, key=None):
        s = self.settings()
        values = {k: s.get(k, row[3]) for k, row in SITE_SETTINGS.items()}
        return values[key] if key else values

    def site_int(self, key):
        try:
            return int(self.site(key))
        except (TypeError, ValueError):
            return int(SITE_SETTINGS[key][3])

    def save_site(self, admin_id, values):
        """Check and save the settings that changed. Returns how many changed."""
        from world import LANGUAGES
        old = self.site()
        clean = {}
        for key, raw in values.items():
            if key not in SITE_SETTINGS:
                continue
            group, label, typ, default, _ = SITE_SETTINGS[key]
            v = "" if raw is None else str(raw).strip()
            if typ == "bool":
                v = "1" if v in ("1", "on", "true", "yes") else ""
            elif typ.startswith("int:"):
                lo, hi = (int(x) for x in typ.split(":")[1:])
                if not re.fullmatch(r"\d{1,6}", v) or not lo <= int(v) <= hi:
                    raise TaskError(f"{label}: a number from {lo} to {hi}.")
            elif typ.startswith("text:"):
                lo, hi = (int(x) for x in typ.split(":")[1:])
                v = " ".join(v.split())
                if not lo <= len(v) <= hi:
                    raise TaskError(f"{label}: {lo}-{hi} characters.")
            elif typ == "color":
                if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
                    raise TaskError(f"{label}: a colour like #4f46e5.")
                v = v.lower()
            elif typ == "lang":
                if v not in LANGUAGES:
                    raise TaskError(f"{label}: choose a language from the list.")
            elif typ == "langs":
                chosen = [x for x in re.split(r"[,\s]+", v) if x]
                if any(x not in LANGUAGES for x in chosen):
                    raise TaskError(f"{label}: choose languages from the list.")
                v = ",".join(code for code in LANGUAGES if code in chosen or code == "en")
            elif typ == "image":
                pass                                   # already checked by picture_to_b64
            if v != old.get(key):
                clean[key] = v
        langs = clean.get("langs_enabled", old["langs_enabled"]).split(",")
        if clean.get("default_lang", old["default_lang"]) not in langs:
            raise TaskError("The language for new visitors must be one of the languages people can choose.")
        with self.db() as con:
            for key, v in clean.items():
                self._put_setting(con, key, v)
                shown = "(picture)" if key == "logo" and v else v
                self.log(con, admin_id, "setting_changed", f"{key} = {shown}"[:200])
        return len(clean)

    # ================================================================ accounts (site administrator)
    def users_admin(self, q=""):
        like = f"%{(q or '').strip().lower()}%"
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT u.id, u.name, u.email, u.role, u.job_title, u.color, u.active, u.created_at, u.lang, "
                "u.totp_enabled, (u.photo != '') AS has_photo, "
                "(SELECT COUNT(*) FROM memberships m WHERE m.user_id = u.id) AS workspaces, "
                "(SELECT COUNT(*) FROM tasks t WHERE t.assignee_id = u.id AND t.status != 'done' AND t.workspace_id > 0) "
                "AS open_tasks, (SELECT COUNT(*) FROM resumes r WHERE r.user_id = u.id) AS resumes, "
                "(SELECT MAX(a.created_at) FROM activity a WHERE a.user_id = u.id AND a.action = 'logged_in') AS last_login "
                "FROM users u WHERE lower(u.name) LIKE ? OR lower(u.email) LIKE ? OR lower(u.job_title) LIKE ? "
                "ORDER BY lower(u.name)", (like, like, like))]

    def site_admin_count(self, con):
        return con.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1").fetchone()[0]

    def save_person(self, by_id, user_id, f, own=False):
        """Change the details of an account. `own` = the person changes their own profile
        (then a new email needs the present password). The site administrator can change anybody."""
        from store import COLORS
        from world import LANGUAGES
        name = " ".join((f.get("name") or "").split())
        email = (f.get("email") or "").strip().lower()
        if len(name) < 2 or len(name) > USER_FIELDS_MAX["name"]:
            raise TaskError("Please enter a full name (2-80 characters).")
        if "@" not in email or "." not in email.split("@")[-1] or len(email) > 120:
            raise TaskError("Please enter a valid email address.")
        job = " ".join((f.get("job_title") or "").split())[:80]
        phone = clean_phone(f.get("phone"))
        bio = (f.get("bio") or "").strip()
        if len(bio) > USER_FIELDS_MAX["bio"]:
            raise TaskError("The text about you is too long (500 characters at most).")
        with self.db() as con:
            u = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not u:
                raise TaskError("User not found.")
            if con.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, user_id)).fetchone():
                raise TaskError("This email is used by another account.")
            if own and email != u["email"] and not check_password_hash(u["password_hash"], f.get("password") or ""):
                raise TaskError("Type your present password to change your email.")
            color = f.get("color") if f.get("color") in COLORS else u["color"]
            lang = f.get("lang") if f.get("lang") in LANGUAGES else u["lang"]
            con.execute("UPDATE users SET name = ?, email = ?, job_title = ?, phone = ?, bio = ?, color = ?, lang = ? "
                        "WHERE id = ?", (name, email, job, phone, bio, color, lang, user_id))
            if not own and "remind_days" in f:
                rd = int(f["remind_days"]) if str(f.get("remind_days")).isdigit() else u["remind_days"]
                con.execute("UPDATE users SET notify_email = ?, remind_days = ? WHERE id = ?",
                            (1 if f.get("notify_email") else 0, min(rd, 3), user_id))
            self.log(con, by_id, "profile_updated" if own else "user_updated", name)

    def set_photo(self, by_id, user_id, data):
        b64 = picture_to_b64(data) if data else ""
        with self.db() as con:
            con.execute("UPDATE users SET photo = ? WHERE id = ?", (b64, user_id))
            self.log(con, by_id, "photo_changed" if b64 else "photo_removed")

    def set_site_admin(self, admin_id, user_id, on):
        with self.db() as con:
            u = con.execute("SELECT name, role, active FROM users WHERE id = ?", (user_id,)).fetchone()
            if not u:
                raise TaskError("User not found.")
            if not on and u["role"] == "admin" and self.site_admin_count(con) <= 1:
                raise TaskError("The last site administrator cannot lose this right.")
            if not on and admin_id == user_id:
                raise TaskError("You cannot remove your own administrator right.")
            con.execute("UPDATE users SET role = ? WHERE id = ?", ("admin" if on else "member", user_id))
            self.log(con, admin_id, "site_admin_on" if on else "site_admin_off", u["name"])

    def admin_create_user(self, admin_id, f):
        uid = self.create_user(f.get("name"), f.get("email"), f.get("password"), job_title=f.get("job_title", ""),
                               by=admin_id, lang=f.get("lang") or "en")
        if f.get("site_admin"):
            self.set_site_admin(admin_id, uid, True)
        ws = int(f["workspace"]) if str(f.get("workspace") or "").isdigit() else None
        if ws and self.workspace(ws):
            self.add_member(ws, uid, f.get("ws_role") or "member", by=admin_id)
        return uid

    def bump_session(self, user_id):
        """Signs a person out on every device: sessions keep the number they got at sign-in."""
        with self.db() as con:
            con.execute("UPDATE users SET session_ver = session_ver + 1 WHERE id = ?", (user_id,))

    def admin_delete_person(self, admin_id, user_id):
        with self.db() as con:
            u = con.execute("SELECT role, active FROM users WHERE id = ?", (user_id,)).fetchone()
            if u and u["role"] == "admin" and self.site_admin_count(con) <= 1:
                raise TaskError("The last site administrator cannot be deleted.")
        self.delete_user(admin_id, user_id)

    def admin_toggle(self, admin_id, user_id):
        with self.db() as con:
            u = con.execute("SELECT role, active FROM users WHERE id = ?", (user_id,)).fetchone()
            if u and u["role"] == "admin" and u["active"] and self.site_admin_count(con) <= 1:
                raise TaskError("The last site administrator cannot be switched off.")
        self.admin_toggle_active(admin_id, user_id)

    def my_data(self, user_id):
        """Everything TaskFlow keeps about one person, as JSON (the right to see your own data)."""
        hidden = {"password_hash", "totp_secret", "photo", "ical_token", "session_ver", "totp_last_step"}
        with self.db() as con:
            def rows(sql, *a):
                return [dict(r) for r in con.execute(sql, a)]
            u = dict(con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
            data = {
                "account": {k: v for k, v in u.items() if k not in hidden},
                "workspaces": rows("SELECT w.name, m.role, m.created_at AS joined FROM memberships m JOIN workspaces w "
                                   "ON w.id = m.workspace_id WHERE m.user_id = ?", user_id),
                "tasks_assigned": rows("SELECT id, title, status, priority, due_date FROM tasks WHERE assignee_id = ? "
                                       "AND workspace_id > 0", user_id),
                "tasks_created": rows("SELECT id, title, status FROM tasks WHERE created_by = ? AND workspace_id > 0",
                                      user_id),
                "comments": rows("SELECT task_id, body, created_at FROM comments WHERE user_id = ?", user_id),
                "time": rows("SELECT task_id, started_at, minutes, note FROM time_entries WHERE user_id = ?", user_id),
                "notifications": rows("SELECT kind, text, created_at FROM notifications WHERE user_id = ?", user_id),
                "resumes": [dict(r, data=json.loads(r["data"])) for r in rows(
                    "SELECT slug, title, theme, visibility, data, created_at FROM resumes WHERE user_id = ?", user_id)],
                "activity": rows("SELECT action, details, created_at FROM activity WHERE user_id = ? ORDER BY id DESC "
                                 "LIMIT 500", user_id),
            }
        return json.dumps({"app": "TaskFlow", "exported_at": now(), **data}, ensure_ascii=False, indent=1, default=str)

    # ================================================================ workspaces (site administrator)
    def workspaces_admin(self):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT w.*, (SELECT COUNT(*) FROM memberships m WHERE m.workspace_id = w.id) AS members, "
                "(SELECT COUNT(*) FROM projects p WHERE p.workspace_id = w.id) AS projects, "
                "(SELECT COUNT(*) FROM tasks t WHERE t.workspace_id = w.id) AS tasks, "
                "(SELECT COUNT(*) FROM tasks t WHERE t.workspace_id = -w.id) AS trashed, "
                "(SELECT COALESCE(SUM(size), 0) FROM attachments a WHERE a.workspace_id = w.id) AS file_bytes, "
                "u.name AS owner_name FROM workspaces w LEFT JOIN users u ON u.id = w.created_by ORDER BY lower(w.name)")]

    def admin_delete_workspace(self, admin_id, ws, confirm_name):
        w = self.workspace(ws)
        if not w:
            raise TaskError("Workspace not found.")
        if (confirm_name or "").strip() != w["name"]:
            raise TaskError("Type the name of the workspace to confirm.")
        with self.db() as con:
            self._delete_workspace_rows(con, ws)
            self.log(con, admin_id, "workspace_deleted", w["name"])

    def site_add_member(self, admin_id, ws, email, role):
        u = self.user_by_email(email)
        if not u:
            raise TaskError("No account uses this email.")
        self.add_member(ws, u["id"], role, by=admin_id)

    def site_set_role(self, admin_id, ws, user_id, role):
        if role not in ("admin", "member"):
            raise TaskError("Unknown role.")
        if role == "member" and self.member_role(ws, user_id) == "admin" and self._admin_count(ws) == 1:
            raise TaskError("A workspace needs at least one admin.")
        with self.db() as con:
            con.execute("UPDATE memberships SET role = ? WHERE workspace_id = ? AND user_id = ?", (role, ws, user_id))
            self.log(con, admin_id, "role_changed", f"user {user_id} is now {role}", ws=ws)

    # ================================================================ resumes (site administrator)
    def resumes_admin(self):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT r.id, r.slug, r.title, r.theme, r.visibility, r.views, r.updated_at, u.name AS owner, "
                "u.id AS owner_id FROM resumes r JOIN users u ON u.id = r.user_id ORDER BY r.updated_at DESC")]

    def admin_set_resume_visibility(self, admin_id, resume_id, visibility):
        from resumes import VISIBILITY
        if visibility not in VISIBILITY:
            raise TaskError("Unknown visibility.")
        with self.db() as con:
            r = con.execute("SELECT title FROM resumes WHERE id = ?", (resume_id,)).fetchone()
            if not r:
                raise TaskError("Resume not found.")
            con.execute("UPDATE resumes SET visibility = ? WHERE id = ?", (visibility, resume_id))
            self.log(con, admin_id, "resume_visibility", f"{r['title']} → {visibility}")

    def admin_delete_resume(self, admin_id, resume_id):
        with self.db() as con:
            r = con.execute("SELECT title FROM resumes WHERE id = ?", (resume_id,)).fetchone()
            if not r:
                raise TaskError("Resume not found.")
            con.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
            self.log(con, admin_id, "resume_deleted", r["title"])

    # ================================================================ words of the interface
    def ui_overrides(self, lang):
        with self.db() as con:
            return {r[0]: r[1] for r in con.execute("SELECT english, text FROM ui_overrides WHERE lang = ?", (lang,))}

    def save_ui_override(self, admin_id, lang, english, text):
        import i18n
        from world import LANGUAGES
        if lang not in LANGUAGES or lang == "en":
            raise TaskError("Choose a language other than English.")
        english = " ".join((english or "").split())
        text = " ".join((text or "").split())
        if not english:
            raise TaskError("The English text is missing.")
        if text and sorted(re.findall(r"\{\d\}", english)) != sorted(re.findall(r"\{\d\}", text)):
            raise TaskError("Keep the same gaps {0}, {1} … as the English text.")
        with self.db() as con:
            con.execute("DELETE FROM ui_overrides WHERE lang = ? AND english = ?", (lang, english))
            if text:
                con.execute("INSERT INTO ui_overrides (lang, english, text, updated_by, updated_at) VALUES (?,?,?,?,?)",
                            (lang, english, text[:1000], admin_id, now()))
            self.log(con, admin_id, "translation_changed", f"[{lang}] {english[:80]}")
        i18n.clear_overrides()

    # ================================================================ audit log
    def audit(self, q="", limit=300):
        like = f"%{(q or '').lower()}%"
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT a.*, u.name AS user_name, w.name AS ws_name FROM activity a LEFT JOIN users u ON u.id = a.user_id "
                "LEFT JOIN workspaces w ON w.id = a.workspace_id WHERE lower(a.action) LIKE ? OR lower(a.details) LIKE ? "
                "OR lower(COALESCE(u.name, '')) LIKE ? ORDER BY a.id DESC LIMIT ?", (like, like, like, limit))]

    # ================================================================ backup restore
    RESTORE_ORDER = ["users", "workspaces", "memberships", "projects", "sprints", "labels", "tasks", "task_labels",
                     "task_deps", "subtasks", "comments", "attachments", "time_entries", "notifications", "activity",
                     "resumes", "ui_overrides", "settings"]

    def restore_backup(self, admin_id, blob):
        """Replace all data with a backup made by TaskFlow (version 2 or 3). The login key is kept."""
        import gzip
        try:
            raw = gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else blob
            data = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            raise TaskError("This is not a TaskFlow backup file.")
        if not isinstance(data, dict) or data.get("app") != "TaskFlow" or not isinstance(data.get("tables"), dict):
            raise TaskError("This is not a TaskFlow backup file.")
        tables = data["tables"]
        if not tables.get("users"):
            raise TaskError("The backup has no accounts.")
        if not any(u.get("role") == "admin" for u in tables["users"]):
            raise TaskError("The backup has no site administrator.")
        n = 0
        with self.db() as con:
            for t in reversed(self.RESTORE_ORDER + ["events", "password_resets", "outbox", "push_subs"]):
                if t == "settings":
                    con.execute("DELETE FROM settings WHERE key NOT IN ('secret_key', 'vapid')")
                else:
                    con.execute(f"DELETE FROM {t}")                       # nosec B608 - fixed names
            for t in self.RESTORE_ORDER:
                have = set(con.columns(t))
                for row in tables.get(t) or []:
                    if t == "settings" and row.get("key") in ("secret_key", "vapid"):
                        continue
                    row = {k: v for k, v in row.items() if k in have}
                    if not row:
                        continue
                    cols = list(row)
                    con.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # nosec B608
                                [row[c] for c in cols])
                    n += 1
            if self.is_postgres:                                          # the next new id must follow the restored ones
                for t in self.RESTORE_ORDER:
                    if "id" in con.columns(t):
                        con.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "       # nosec B608
                                    f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {t}), 1))")
            self.log(con, admin_id if con.execute("SELECT 1 FROM users WHERE id = ?", (admin_id,)).fetchone() else None,
                     "backup_restored", f"{n} rows")
        import i18n
        i18n.clear_overrides()
        return n

    # ================================================================ email to anybody (test, reset by admin)
    def admin_send_reset(self, admin_id, user_id, base_url):
        u = self.user(user_id)
        if not u:
            raise TaskError("User not found.")
        if is_example_email(u["email"]):
            raise TaskError("Demo addresses are never emailed.")
        if not self.start_password_reset(u["email"], base_url):
            raise TaskError("No email service is set up (BREVO_* or SMTP_* secrets), so the link was not sent.")
        with self.db() as con:
            self.log(con, admin_id, "reset_link_sent", u["name"])

    def ical_token(self, user_id, renew=False):
        with self.db() as con:
            r = con.execute("SELECT ical_token FROM users WHERE id = ?", (user_id,)).fetchone()
            if r and r[0] and not renew:
                return r[0]
            token = secrets.token_urlsafe(24)
            con.execute("UPDATE users SET ical_token = ? WHERE id = ?", (token, user_id))
            return token

    def user_by_ical(self, token):
        if not token or len(token) < 20:
            return None
        with self.db() as con:
            r = con.execute("SELECT * FROM users WHERE ical_token = ? AND active = 1", (token,)).fetchone()
            return dict(r) if r else None

    def set_password_hash_now(self, user_id, password):
        """Used by tests and the demo only."""
        with self.db() as con:
            con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), user_id))

    @staticmethod
    def stamp():
        return datetime.now().strftime("%Y-%m-%d")
