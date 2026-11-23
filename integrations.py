"""
integrations.py - TaskFlow talking to other programs (version 3).

  * outbox: every email, Slack and Telegram message the site writes is kept with its status
  * Slack (incoming webhook) and Telegram (bot) messages of a workspace: new or reassigned task,
    finished task, deadline reminder
  * Web Push: the site can be installed on a phone (PWA) and sends notifications even when it is closed
  * iCal feed: a private link that Google Calendar, Outlook or Apple Calendar can subscribe to

Nothing is sent to demo or test addresses, and nothing leaves the server when no key is set.
"""

import json
import re
import threading
import urllib.request
from datetime import date, datetime, timedelta, timezone

from core import TaskError, is_example_email, now

SLACK_RX = re.compile(r"^https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{20,200}$")
TELEGRAM_CHAT_RX = re.compile(r"^-?\d{5,20}$|^@[A-Za-z][A-Za-z0-9_]{4,31}$")
CHAT_EVENTS = {"task_created", "task_completed", "reminder"}


def ics_escape(text):
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def ics_fold(line):
    """Lines of an iCalendar file are at most 75 bytes; longer ones continue on the next line after a space."""
    out, raw = [], line.encode("utf-8")
    while len(raw) > 75:
        cut = 75 if not out else 74
        while (raw[cut] & 0xC0) == 0x80:                        # never cut inside a UTF-8 letter
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n ".join(out)


class IntegrationsMixin:
    http = None                       # tests put a fake here; None = urllib

    # ================================================================ outbox
    def outbox_add(self, con, channel, to, subject, body, status="waiting"):
        return con.execute("INSERT INTO outbox (channel, to_addr, subject, body, status, created_at) VALUES (?,?,?,?,?,?)",
                           (channel, str(to)[:200], (subject or "")[:200], (body or "")[:4000], status, now())).lastrowid

    def outbox_result(self, outbox_id, status):
        with self.db() as con:
            con.execute("UPDATE outbox SET status = ? WHERE id = ?", (status[:200], outbox_id))

    def outbox(self, limit=200, channel=""):
        with self.db() as con:
            if channel:
                return [dict(r) for r in con.execute("SELECT * FROM outbox WHERE channel = ? ORDER BY id DESC LIMIT ?",
                                                     (channel, limit))]
            return [dict(r) for r in con.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))]

    def send_test_email(self, admin_id, to):
        to = (to or "").strip().lower()
        if "@" not in to:
            raise TaskError("Please enter a valid email address.")
        with self.db() as con:
            if is_example_email(to):
                self.outbox_add(con, "email", to, "Test", "", "not sent: demo or test address")
                return "not sent: demo or test address"
            if not (self.notifier and self.notifier.email_provider()):
                self.outbox_add(con, "email", to, "Test", "", "not sent: no email service")
                return "not sent: no email service"
            oid = self.outbox_add(con, "email", to, "TaskFlow test", "This is a test email from TaskFlow.")
            self.log(con, admin_id, "test_email", to)
        try:
            self.notifier.send_email(to, "TaskFlow test", "This is a test email from TaskFlow.")
            status = "sent"
        except Exception as e:                                       # keep the reason, never crash
            status = f"failed: {str(e)[:120]}"
        self.outbox_result(oid, status)
        return status

    # ================================================================ Slack and Telegram of a workspace
    def save_chat_settings(self, admin_id, ws, slack_url, telegram_chat):
        slack_url, telegram_chat = (slack_url or "").strip(), (telegram_chat or "").strip()
        if slack_url and not SLACK_RX.match(slack_url):
            raise TaskError("A Slack link starts with https://hooks.slack.com/services/")
        if telegram_chat and not TELEGRAM_CHAT_RX.match(telegram_chat):
            raise TaskError("A Telegram chat is a number (like -1001234567890) or a channel name (like @myteam).")
        with self.db() as con:
            con.execute("UPDATE workspaces SET slack_url = ?, telegram_chat = ? WHERE id = ?",
                        (slack_url, telegram_chat, ws))
            self.log(con, admin_id, "settings_updated", "chat messages", ws=ws)

    def chat_post(self, con, ws, kind, text):
        """Queue a message to the Slack / Telegram of the workspace; sent after the commit, in the background."""
        if kind not in CHAT_EVENTS or not ws or ws < 0:
            return
        w = con.execute("SELECT name, slack_url, telegram_chat FROM workspaces WHERE id = ?", (ws,)).fetchone()
        if not w:
            return
        text = f"[{w['name']}] {text}"
        jobs = []
        if w["slack_url"]:
            jobs.append((self.outbox_add(con, "slack", "Slack webhook", kind, text), "slack", w["slack_url"]))
        if w["telegram_chat"]:
            token = self.env.get("TELEGRAM_BOT_TOKEN", "")
            if token:
                jobs.append((self.outbox_add(con, "telegram", w["telegram_chat"], kind, text), "telegram",
                             w["telegram_chat"]))
            else:
                self.outbox_add(con, "telegram", w["telegram_chat"], kind, text, "not sent: TELEGRAM_BOT_TOKEN is not set")
        if jobs:
            con.after_commit.append(lambda: self._send_chat(jobs, text))

    def _send_chat(self, jobs, text):
        def work():
            for oid, channel, target in jobs:
                try:
                    if channel == "slack":
                        body, url = json.dumps({"text": text}).encode(), target
                    else:
                        url = f"https://api.telegram.org/bot{self.env['TELEGRAM_BOT_TOKEN']}/sendMessage"
                        body = json.dumps({"chat_id": target, "text": text, "disable_web_page_preview": True}).encode()
                    req = urllib.request.Request(url, data=body, method="POST",
                                                 headers={"Content-Type": "application/json"})
                    with (self.http or urllib.request.urlopen)(req, timeout=10) as resp:
                        if resp.status >= 300:
                            raise RuntimeError(f"answered {resp.status}")
                    self.outbox_result(oid, "sent")
                except Exception as e:
                    self.outbox_result(oid, f"failed: {str(e)[:150]}")
        if self.http is not None:                      # tests: at once
            work()
        else:
            threading.Thread(target=work, daemon=True).start()

    # ================================================================ Web Push
    def vapid(self):
        """The server's push keys, made once and kept in the settings (never shown)."""
        import push
        with self.db() as con:
            r = con.execute("SELECT value FROM settings WHERE key = 'vapid'").fetchone()
            if r:
                return json.loads(r[0])
            keys = push.new_vapid_keys()
            con.execute("INSERT INTO settings (key, value) VALUES ('vapid', ?) ON CONFLICT (key) DO NOTHING",
                        (json.dumps(keys),))
            return json.loads(con.execute("SELECT value FROM settings WHERE key = 'vapid'").fetchone()[0])

    def save_push_sub(self, user_id, sub):
        try:
            endpoint = str(sub["endpoint"])
            p256dh, auth = str(sub["keys"]["p256dh"]), str(sub["keys"]["auth"])
        except (KeyError, TypeError):
            raise TaskError("The browser sent a wrong subscription.")
        if not endpoint.startswith("https://") or len(endpoint) > 1000 or not p256dh or not auth:
            raise TaskError("The browser sent a wrong subscription.")
        with self.db() as con:
            con.execute("DELETE FROM push_subs WHERE endpoint = ?", (endpoint,))
            con.execute("INSERT INTO push_subs (user_id, endpoint, p256dh, auth, created_at) VALUES (?,?,?,?,?)",
                        (user_id, endpoint, p256dh[:200], auth[:100], now()))

    def delete_push_subs(self, user_id, endpoint=None):
        with self.db() as con:
            if endpoint:
                con.execute("DELETE FROM push_subs WHERE user_id = ? AND endpoint = ?", (user_id, endpoint))
            else:
                con.execute("DELETE FROM push_subs WHERE user_id = ?", (user_id,))

    def push_count(self, user_id):
        with self.db() as con:
            return con.execute("SELECT COUNT(*) FROM push_subs WHERE user_id = ?", (user_id,)).fetchone()[0]

    def queue_push(self, con, user_id, title, body, url="/"):
        subs = [dict(r) for r in con.execute("SELECT * FROM push_subs WHERE user_id = ?", (user_id,))]
        if not subs:
            return
        u = con.execute("SELECT notify_push FROM users WHERE id = ?", (user_id,)).fetchone()
        if not u or not u["notify_push"]:
            return
        row = con.execute("SELECT value FROM settings WHERE key = 'vapid'").fetchone()
        if not row:                                     # keys are made when the first browser subscribes
            return
        keys = json.loads(row[0])
        jobs = []
        for s in subs:
            jobs.append((self.outbox_add(con, "push", f"user {user_id}", title, body),
                         {"endpoint": s["endpoint"], "p256dh": s["p256dh"], "auth": s["auth"], "vapid": keys}))
        con.after_commit.append(lambda: self._send_push(jobs, title, body, url))

    def _send_push(self, jobs, title, body, url):
        import push

        def work():
            for oid, sub in jobs:
                try:
                    push.send(sub, title, body, http=self.http, url=url)
                    self.outbox_result(oid, "sent")
                except Exception as e:
                    if str(e) == "gone":                       # the phone unsubscribed: forget it
                        with self.db() as con:
                            con.execute("DELETE FROM push_subs WHERE endpoint = ?", (sub["endpoint"],))
                    self.outbox_result(oid, f"failed: {str(e)[:150]}")
        if self.http is not None:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()

    # ================================================================ iCal feed
    def ical(self, user, base_url="", today=None):
        """VCALENDAR with one all-day event per open task of the person (every workspace) that has a due date,
        and the sprints of their workspaces."""
        today = today or date.today()
        since = (today - timedelta(days=60)).isoformat()
        with self.db() as con:
            tasks = [dict(r) for r in con.execute(
                "SELECT t.id, t.title, t.description, t.status, t.priority, t.due_date, t.updated_at, w.name AS ws_name, "
                "p.name AS project_name FROM tasks t JOIN workspaces w ON w.id = t.workspace_id "
                "LEFT JOIN projects p ON p.id = t.project_id WHERE t.assignee_id = ? AND t.due_date IS NOT NULL "
                "AND t.due_date >= ? AND t.workspace_id > 0", (user["id"], since))]
            sprints = [dict(r) for r in con.execute(
                "SELECT s.*, w.name AS ws_name FROM sprints s JOIN memberships m ON m.workspace_id = s.workspace_id "
                "JOIN workspaces w ON w.id = s.workspace_id WHERE m.user_id = ? AND s.end_date >= ?", (user["id"], since))]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//TaskFlow//Tasks 3//EN", "CALSCALE:GREGORIAN",
                 "METHOD:PUBLISH", "X-WR-CALNAME:" + ics_escape(f"TaskFlow - {user['name']}"),
                 "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
        for t in tasks:
            d = date.fromisoformat(t["due_date"])
            done = t["status"] == "done"
            desc = f"{t['ws_name']}" + (f" / {t['project_name']}" if t["project_name"] else "") + \
                   f"\nPriority: {t['priority']}" + (f"\n{base_url}tasks/{t['id']}" if base_url else "") + \
                   (f"\n\n{t['description'][:500]}" if t["description"] else "")
            lines += ["BEGIN:VEVENT", f"UID:task-{t['id']}@taskflow", f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
                      "SUMMARY:" + ics_escape(("✓ " if done else "") + t["title"]),
                      "DESCRIPTION:" + ics_escape(desc), "TRANSP:TRANSPARENT",
                      "STATUS:" + ("CANCELLED" if done else "CONFIRMED")]
            if base_url:
                lines.append(f"URL:{base_url}tasks/{t['id']}")
            if not done:
                lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + ics_escape(t["title"]),
                          "TRIGGER:-PT15H", "END:VALARM"]
            lines.append("END:VEVENT")
        for s in sprints:
            a, b = date.fromisoformat(s["start_date"]), date.fromisoformat(s["end_date"])
            lines += ["BEGIN:VEVENT", f"UID:sprint-{s['id']}@taskflow", f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{a:%Y%m%d}", f"DTEND;VALUE=DATE:{b + timedelta(days=1):%Y%m%d}",
                      "SUMMARY:" + ics_escape(f"Sprint: {s['name']} ({s['ws_name']})"),
                      "DESCRIPTION:" + ics_escape(s["goal"] or ""), "TRANSP:TRANSPARENT", "END:VEVENT"]
        lines.append("END:VCALENDAR")
        return "\r\n".join(ics_fold(x) for x in lines) + "\r\n"
