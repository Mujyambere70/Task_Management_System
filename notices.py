"""
notices.py - messages to people and live updates:

  * notifications on the site (the bell): a task was assigned to you, a comment, an @mention, a reminder
  * the same by email when the person allows it and an email service is set up (Brevo or SMTP)
  * email reminders before a deadline (default: one day before; each person can choose 1, 2 or 3 days or off)
  * live events for open boards (WebSocket, or polling as a fallback)
  * password reset by email and two-factor login with an authenticator app

Demo people (emails ending with .demo, example.com ...) are never emailed.
"""

import hashlib
import re
import secrets
import time
from datetime import date, datetime, timedelta

import i18n
import otp
from core import TaskError, is_example_email, now

RESET_MINUTES = 30


class NoticesMixin:

    # ================================================================ notifications
    def notify(self, con, user_id, kind, text, ws=None, task_id=None, email=False, subject=None):
        """Save a message for a person (translated into their language) and email it after the commit if wanted."""
        u = con.execute("SELECT id, name, email, lang, notify_email, active FROM users WHERE id = ?",
                        (user_id,)).fetchone()
        if not u or not u["active"]:
            return
        lang = u["lang"] or "en"
        text_l = i18n.translate_text(text, lang)
        nid = con.execute("INSERT INTO notifications (user_id, workspace_id, task_id, kind, text, created_at) "
                          "VALUES (?,?,?,?,?,?)", (user_id, ws, task_id, kind, text_l, now())).lastrowid
        con.execute("INSERT INTO events (workspace_id, to_user, kind, task_id, text, created_at) VALUES (?,?,?,?,?,?)",
                    (ws, user_id, "notification", task_id, text_l, now()))
        from store import _wake_listeners
        con.after_commit.append(_wake_listeners)
        if hasattr(self, "queue_push"):                   # version 3: also to the phones of the person
            self.queue_push(con, user_id, self.site("site_name"), text_l, f"/tasks/{task_id}" if task_id else "/")
        if not (email and u["notify_email"] and self.notifier and self.notifier.email_provider()):
            return
        if is_example_email(u["email"]):                  # demo people are never contacted
            con.execute("UPDATE notifications SET emailed = 'skipped: demo address' WHERE id = ?", (nid,))
            return
        base = self.env.get("APP_URL", "").rstrip("/")
        link = f"\n\n{i18n.translate_text('Open the task:', lang)} {base}/tasks/{task_id}" if task_id and base else ""
        hello = i18n.translate_text("Hello {0},", lang).replace("{0}", u["name"].split()[0])
        sign = self.site("mail_signature") if hasattr(self, "site") else "- TaskFlow"
        body = f"{hello}\n\n{text_l}{link}\n\n{sign}"
        subj = i18n.translate_text(subject or "TaskFlow notification", lang)
        oid = self.outbox_add(con, "email", u["email"], subj, body) if hasattr(self, "outbox_add") else None
        jobs = [((nid, oid), "email", u["email"], subj, body)]
        con.after_commit.append(lambda: self.notifier.deliver(jobs, self._email_result))

    def _email_result(self, ids, status):
        nid, oid = ids if isinstance(ids, tuple) else (ids, None)
        with self.db() as con:
            con.execute("UPDATE notifications SET emailed = ? WHERE id = ?", (status[:120], nid))
            if oid:
                con.execute("UPDATE outbox SET status = ? WHERE id = ?", (status[:200], oid))

    def _notify_assigned(self, con, by_id, user_id, task_id, title, ws):
        name = con.execute("SELECT name FROM users WHERE id = ?", (by_id,)).fetchone()[0]
        self.notify(con, user_id, "assigned", f"{name} assigned “{title}” to you.", ws=ws, task_id=task_id,
                    email=True, subject="A task was assigned to you")

    def notify_mentions(self, con, by_id, ws, task_id, title, text):
        """@Anna or @anna.petrova in a comment or description notifies that member. Returns the ids notified."""
        words = {w.lower().rstrip(".,:;!?") for w in re.findall(r"@([\w.\-]{2,40})", text or "", re.UNICODE)}
        if not words:
            return set()
        members = con.execute("SELECT u.id, u.name FROM users u JOIN memberships m ON m.user_id = u.id "
                              "WHERE m.workspace_id = ?", (ws,)).fetchall()
        by = con.execute("SELECT name FROM users WHERE id = ?", (by_id,)).fetchone()[0]
        done = set()
        for uid, name in members:
            keys = {name.split()[0].lower(), ".".join(name.lower().split()), "".join(name.lower().split())}
            if uid != by_id and keys & words:
                self.notify(con, uid, "mention", f"{by} mentioned you in “{title}”.", ws=ws, task_id=task_id,
                            email=True, subject="You were mentioned")
                done.add(uid)
        return done

    def notifications(self, user_id, limit=60):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT n.*, w.name AS workspace_name FROM notifications n LEFT JOIN workspaces w ON w.id = n.workspace_id "
                "WHERE n.user_id = ? ORDER BY n.id DESC LIMIT ?", (user_id, limit))]

    def unseen_count(self, user_id):
        with self.db() as con:
            return con.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND seen = 0",
                               (user_id,)).fetchone()[0]

    def mark_seen(self, user_id):
        with self.db() as con:
            con.execute("UPDATE notifications SET seen = 1 WHERE user_id = ? AND seen = 0", (user_id,))

    def save_notify_settings(self, user_id, email_on, remind_days):
        try:
            remind_days = int(remind_days)
        except (TypeError, ValueError):
            remind_days = 1
        if remind_days not in (0, 1, 2, 3):
            raise TaskError("Choose 1, 2 or 3 days, or no reminders.")
        with self.db() as con:
            con.execute("UPDATE users SET notify_email = ?, remind_days = ? WHERE id = ?",
                        (1 if email_on else 0, remind_days, user_id))

    # ================================================================ deadline reminders
    def send_reminders(self, today=None):
        """Remind assignees of open tasks whose deadline is near (each task once per due date). Returns how many."""
        today = today or date.today()
        horizon = (today + timedelta(days=3)).isoformat()
        with self.db() as con:
            rows = [dict(r) for r in con.execute(
                "SELECT t.id, t.title, t.due_date, t.workspace_id, t.assignee_id, u.remind_days "
                "FROM tasks t JOIN users u ON u.id = t.assignee_id "
                "WHERE t.status != 'done' AND t.due_date IS NOT NULL AND t.due_date BETWEEN ? AND ? AND t.workspace_id > 0 "
                "AND u.active = 1 AND u.remind_days > 0 AND t.reminded_for != t.due_date",
                (today.isoformat(), horizon))]
        sent = 0
        for t in rows:
            left = (date.fromisoformat(t["due_date"]) - today).days
            if left > t["remind_days"]:
                continue
            with self.db() as con:
                # claim first: if two servers run the job at the same time, only one sends
                if not con.execute("UPDATE tasks SET reminded_for = due_date WHERE id = ? AND reminded_for != due_date",
                                   (t["id"],)).rowcount:
                    continue
                text = (f"“{t['title']}” is due today." if left == 0 else
                        f"“{t['title']}” is due tomorrow." if left == 1 else
                        f"“{t['title']}” is due in {left} days.")
                self.notify(con, t["assignee_id"], "reminder", text, ws=t["workspace_id"], task_id=t["id"],
                            email=True, subject="Deadline reminder")
                if hasattr(self, "chat_post"):
                    self.chat_post(con, t["workspace_id"], "reminder", text)
                sent += 1
        return sent

    _last_jobs = 0.0

    def housekeeping(self, force=False):
        """Run at most every 10 minutes: reminders, and old live events are removed."""
        if not force and time.time() - NoticesMixin._last_jobs < 600:
            return None
        NoticesMixin._last_jobs = time.time()
        sent = self.send_reminders()
        cutoff = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db() as con:
            con.execute("DELETE FROM events WHERE created_at < ?", (cutoff,))
        purged = self.empty_old_trash() if hasattr(self, "empty_old_trash") else 0
        return {"reminders": sent, "trash_emptied": purged}

    # ================================================================ live events
    def latest_event_id(self):
        with self.db() as con:
            return con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]

    def events_after(self, ws, user_id, after_id, limit=50):
        with self.db() as con:
            return [dict(r) for r in con.execute(
                "SELECT e.*, u.name AS user_name FROM events e LEFT JOIN users u ON u.id = e.user_id "
                "WHERE e.id > ? AND ((e.workspace_id = ? AND e.to_user IS NULL) OR e.to_user = ?) "
                "ORDER BY e.id LIMIT ?", (after_id, ws, user_id, limit))]

    # ================================================================ password reset by email
    def start_password_reset(self, email, base_url):
        """Email a one-time link (valid 30 minutes). The page always says the same, so nobody learns which
        emails have accounts. Returns True if an email was sent."""
        email = (email or "").strip().lower()
        with self.db() as con:
            u = con.execute("SELECT id, name, email, lang, active FROM users WHERE email = ?", (email,)).fetchone()
        if u is None or not u["active"] or is_example_email(email) or not (self.notifier and self.notifier.email_provider()):
            return False
        token = secrets.token_urlsafe(32)
        with self.db() as con:
            con.execute("INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?,?,?)",
                        (hashlib.sha256(token.encode()).hexdigest(), u["id"],
                         (datetime.now() + timedelta(minutes=RESET_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")))
        lang = u["lang"] or "en"
        t = lambda s: i18n.translate_text(s, lang)  # noqa: E731
        text = (t("Hello {0},").replace("{0}", u["name"].split()[0]) + "\n\n" +
                t("Someone asked to reset the password of your TaskFlow account. Open this link within 30 minutes "
                  "to choose a new password:") + f"\n{base_url}reset/{token}\n\n" +
                t("If it was not you, you can ignore this email: your password stays the same."))
        subject = t("Reset your TaskFlow password")
        oid = None
        if hasattr(self, "outbox_add"):
            with self.db() as con:                        # the outbox shows that a link was sent, never the link
                oid = self.outbox_add(con, "email", u["email"], subject, "(password reset link)")
        try:
            self.notifier.send_email(u["email"], subject, text)
        except Exception as e:
            if oid:
                self.outbox_result(oid, f"failed: {str(e)[:150]}")
            raise
        if oid:
            self.outbox_result(oid, "sent")
        return True

    def reset_token_ok(self, token):
        h = hashlib.sha256(str(token).encode()).hexdigest()
        with self.db() as con:
            r = con.execute("SELECT used, expires_at FROM password_resets WHERE token_hash = ?", (h,)).fetchone()
        return bool(r and not r["used"] and r["expires_at"] >= now())

    def reset_password(self, token, new, confirm):
        from werkzeug.security import generate_password_hash
        from store import MIN_PASSWORD
        if new != confirm:
            raise TaskError("New passwords do not match.")
        if len(new or "") < MIN_PASSWORD:
            raise TaskError(f"Password must have at least {MIN_PASSWORD} characters.")
        h = hashlib.sha256(str(token).encode()).hexdigest()
        with self.db() as con:
            # the link works once: only the request that marks it used may change the password
            if not con.execute("UPDATE password_resets SET used = 1 WHERE token_hash = ? AND used = 0 AND expires_at >= ?",
                               (h, now())).rowcount:
                raise TaskError("This link has expired or was already used. Please ask for a new one.")
            uid = con.execute("SELECT user_id FROM password_resets WHERE token_hash = ?", (h,)).fetchone()[0]
            con.execute("UPDATE users SET password_hash = ?, session_ver = session_ver + 1 WHERE id = ?", (generate_password_hash(new), uid))
            self.log(con, uid, "password_reset_by_email")
            return uid

    # ================================================================ two-factor login (TOTP)
    def enable_2fa(self, user_id, secret, code):
        step = otp.verify(secret, code)
        if step is None:
            raise TaskError("The code is not correct. Check the time on your phone and type the newest code.")
        with self.db() as con:
            con.execute("UPDATE users SET totp_secret = ?, totp_enabled = 1, totp_last_step = ? WHERE id = ?",
                        (secret, step, user_id))
            self.log(con, user_id, "2fa_enabled")

    def verify_2fa(self, user_id, code):
        with self.db() as con:
            r = con.execute("SELECT totp_secret, totp_last_step FROM users WHERE id = ?", (user_id,)).fetchone()
            step = otp.verify(r["totp_secret"], code, r["totp_last_step"]) if r and r["totp_secret"] else None
            if step is None:
                raise TaskError("Wrong or expired code. Type the 6-digit code shown in your authenticator app.")
            # a code can be used only once
            if not con.execute("UPDATE users SET totp_last_step = ? WHERE id = ? AND totp_last_step < ?",
                               (step, user_id, step)).rowcount:
                raise TaskError("Wrong or expired code. Type the 6-digit code shown in your authenticator app.")

    def disable_2fa(self, user_id, password, code):
        from werkzeug.security import check_password_hash
        u = self.user(user_id)
        if not check_password_hash(u["password_hash"], password or ""):
            raise TaskError("Your current password is wrong.")
        self.verify_2fa(user_id, code)
        with self.db() as con:
            con.execute("UPDATE users SET totp_secret = '', totp_enabled = 0, totp_last_step = 0 WHERE id = ?",
                        (user_id,))
            self.log(con, user_id, "2fa_disabled")

    def reset_2fa(self, admin_id, user_id):
        with self.db() as con:
            con.execute("UPDATE users SET totp_secret = '', totp_enabled = 0, totp_last_step = 0 WHERE id = ?",
                        (user_id,))
            self.log(con, admin_id, "2fa_reset")
