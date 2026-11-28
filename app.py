"""
TaskFlow - team task management web app (Flask).

Run:   pip install -r requirements.txt
       python app.py            -> http://127.0.0.1:5000
Demo:  admin@taskflow.demo / Admin@1234   (more users in README)

Database: SQLite file taskflow.db on your computer, or PostgreSQL when DATABASE_URL is set (online).

Version 3: sprints, Gantt chart, labels, dependencies, recurring tasks, search, trash, iCal feed, Slack / Telegram,
Web Push, suggested estimates, resume PDF; and a back office where the site administrator manages everything
(planning_views.py, admin_views.py).

Version 4: custom board columns with WIP limits, custom fields, automation rules, saved views, task templates,
REST API with personal keys, signed webhooks, retries of failed emails, weekly digest, calendar import and
subscriptions, a better estimate, S3 file storage, Google / Microsoft sign-in, translation suggestions,
sign-in limits, security headers, compressed pages, dark mode, command palette and an offline page
(v4_store.py, v4_views.py, oidc.py, s3.py, livebus.py).
"""

import calendar
import csv
import gzip
import io
import json
import os
import secrets
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

from jinja2 import pass_context
from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, send_file,
                   session, url_for)

import i18n
import otp
import world
from core import TaskError, fmt_minutes, parse_duration
from store import COLORS, PRIORITIES, PRIORITY_NAMES, REPEATS, STATUS_NAMES, STATUSES, LIVE_SIGNAL, Store

BASE = os.path.dirname(os.path.abspath(__file__))
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
       "img-src 'self' data: blob:; connect-src 'self' ws: wss:; frame-ancestors 'self'; base-uri 'self'; "
       "object-src 'none'; form-action 'self'")
GZIP_TYPES = {"text/html", "application/json", "text/css", "application/javascript", "text/javascript", "text/csv",
              "text/calendar", "image/svg+xml"}
DEVELOPER = {"name": "Jean Michel Mujyambere", "github": "https://github.com/Mujyambere70"}
VERSION = "4.0"


def create_app(db_path=None, seed=True, testing=False, notifier=None, driver=None, env=None):
    env = os.environ if env is None else env
    app = Flask(__name__)
    target = db_path or env.get("DATABASE_URL") or os.path.join(BASE, "taskflow.db")
    store = Store(target, driver=driver, notifier=notifier, env=env)
    app.store = store
    app.config.update(
        SECRET_KEY=env.get("SECRET_KEY") or env.get("TASKFLOW_SECRET") or store.secret_key(),
        TESTING=testing,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=env.get("SECURE_COOKIES") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=21 * 1024 * 1024,              # the real file limit is a site setting (1-20 MB)
        JOBS=not testing and env.get("RUN_JOBS", "1") != "0",
        APP_URL=env.get("APP_URL", "").rstrip("/"),
    )
    i18n.overrides_source = store.ui_overrides          # words changed in Admin -> Translations
    i18n.clear_overrides()
    if env.get("DATABASE_URL") and store.is_postgres and app.config["JOBS"] and env.get("LIVE_BUS", "1") != "0":
        import livebus                                   # live board updates across processes (PostgreSQL)
        from store import _wake_listeners
        livebus.Listener(lambda: store.database._driver.connect(livebus.direct_url(store.database.target), autocommit=True),
                         _wake_listeners).start()
    if seed and store.count_users() == 0:
        from demo_data import load_demo
        load_demo(store)

    # ------------------------------------------------------------ helpers
    def login_required(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if not g.user:
                return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
            return view(*a, **kw)
        return wrapper

    def admin_required(view):
        """Admin of the current workspace."""
        @wraps(view)
        @login_required
        def wrapper(*a, **kw):
            if g.user["role"] != "admin":
                abort(403)
            return view(*a, **kw)
        return wrapper

    def site_admin_required(view):
        """The administrator of the whole TaskFlow site (the first account)."""
        @wraps(view)
        @login_required
        def wrapper(*a, **kw):
            if not g.user["site_admin"]:
                abort(403)
            return view(*a, **kw)
        return wrapper

    app.login_required = login_required

    def get_task_or_404(task_id):
        t = store.task(task_id, ws=g.ws)
        if not t:
            abort(404)
        return t

    def safe_next(target):
        return target if target and target.startswith("/") and not target.startswith("//") else url_for("dashboard")

    def base_url():
        return env.get("APP_URL", "").rstrip("/") + "/" if env.get("APP_URL") else request.host_url

    def client_ip():
        """Fly.io puts the visitor's address in Fly-Client-IP; elsewhere the direct address is used."""
        return (request.headers.get("Fly-Client-IP") if env.get("FLY_APP_NAME") else None) or request.remote_addr or ""

    app.client_ip = client_ip
    app.finish_login = lambda u, remember: finish_login(u, remember)

    def start_session(u, remember=False):
        session.clear()
        session["uid"] = u["id"]
        session["csrf"] = secrets.token_hex(16)
        session["lang"] = u.get("lang") or "en"
        session["sv"] = u.get("session_ver") or 1
        session.permanent = bool(remember)

    # ------------------------------------------------------------ every request
    @app.before_request
    def load_user_and_check_csrf():
        g.user, g.ws, g.workspace = None, None, None
        u = store.user(session["uid"]) if "uid" in session else None
        if u and (not u["active"] or session.get("sv", 1) != (u.get("session_ver") or 1)):
            session.clear()                          # switched off, or signed out on every device / new password
            u = None
        g.site = site = store.site()
        enabled = site["langs_enabled"].split(",")
        lang = session.get("lang") or (u["lang"] if u else None)
        if not lang:                                 # the first browser language we have, else the site's choice
            lang = next((x[:2].lower() for x, _ in request.accept_languages if x[:2].lower() in enabled),
                        site["default_lang"])
        if lang not in enabled:
            lang = site["default_lang"]
        world.set_lang(lang)
        g.lang = world.current_lang()
        if u:
            wanted = session.get("ws")
            if u["role"] == "admin" and wanted and not store.member_role(wanted, u["id"]) and store.workspace(wanted):
                ws = wanted                          # the site administrator may open any workspace
            else:
                ws = store.choose_workspace(u["id"], wanted)
            session["ws"] = ws
            g.ws = ws
            g.workspace = store.workspace(ws)
            role = store.member_role(ws, u["id"]) or ("admin" if u["role"] == "admin" else "member")
            g.user = dict(u, site_admin=u["role"] == "admin", role=role, guest_admin=not store.member_role(ws, u["id"]))
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        g.api = request.path.startswith("/api/v1/")          # the REST API signs in with a key, not a cookie
        if request.method == "POST" and request.endpoint not in ("cron_run",) and not g.api:
            token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not token or not secrets.compare_digest(token, session["csrf"]):
                abort(400, "Your session expired. Please reload the page and try again.")
        if site["maintenance"] and not (g.user and g.user["site_admin"]) and request.endpoint not in (
                "static", "login", "login_verify", "logout", "set_language", "manifest", "service_worker", "brand_logo"):
            return render_template("maintenance.html"), 503
        if app.config["JOBS"] and request.endpoint not in ("static", "live_socket", "healthz"):
            try:
                store.housekeeping()
            except Exception:                                  # reminders must never break a page
                pass

    @app.after_request
    def translate_page(resp):
        lang = world.current_lang()
        if lang != "en" and resp.mimetype == "text/html" and not resp.direct_passthrough:
            page = i18n.translate_html(resp.get_data(as_text=True), lang)
            attrs = f'lang="{lang}" dir="rtl"' if lang in world.RTL_LANGUAGES else f'lang="{lang}"'
            resp.set_data(page.replace('<html lang="en">', f"<html {attrs}>", 1))
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if resp.mimetype == "text/html":                  # version 4: what a page may load and who may frame it
            resp.headers.setdefault("Content-Security-Policy", CSP)
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
            if g.get("user"):
                resp.headers.setdefault("Cache-Control", "no-store")     # private pages are not kept by the browser
        if env.get("SECURE_COOKIES") == "1":
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return compress(resp)

    def compress(resp):
        """gzip for pages, JSON, CSS and JavaScript when the browser accepts it (a page is 5-8 times smaller)."""
        if (resp.direct_passthrough or resp.status_code < 200 or resp.status_code >= 300 or "Content-Encoding" in resp.headers
                or "gzip" not in request.headers.get("Accept-Encoding", "") or resp.mimetype not in GZIP_TYPES):
            return resp
        data = resp.get_data()
        if len(data) < 800:
            return resp
        resp.set_data(gzip.compress(data, compresslevel=6))
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Vary"] = "Accept-Encoding"
        return resp

    def tr(text):
        return i18n.translate_text(text)

    @app.context_processor
    def globals_for_templates():
        u = g.get("user")
        return dict(
            user=u, csrf_token=session.get("csrf", ""), STATUSES=STATUSES, PRIORITIES=PRIORITIES,
            STATUS_NAMES=STATUS_NAMES, PRIORITY_NAMES=PRIORITY_NAMES, COLORS=COLORS, today=date.today(),
            now_hour=datetime.now().hour, can_edit=lambda t: u and store.can_edit(u, t),
            can_delete=lambda t: u and store.can_delete(u, t), can_manage=lambda p: u and store.can_manage_project(u, p),
            nav_projects=store.projects(g.ws) if u else [], settings=store.settings(), workspace=g.get("workspace"),
            CFG=g.get("site") or store.site(), VERSION=VERSION,
            LANGS_ON={k: v for k, v in world.LANGUAGES.items() if k in (g.get("site") or store.site())["langs_enabled"].split(",")},
            my_workspaces=store.memberships(u["id"]) if u else [], unseen=store.unseen_count(u["id"]) if u else 0,
            timer=store.running_timer(u["id"]) if u else None, LANGUAGES=world.LANGUAGES, lang=g.get("lang", "en"),
            DEVELOPER=DEVELOPER, fmt_date=world.fmt_date, server_now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    @app.template_filter("initials")
    def initials(name):
        parts = (name or "?").split()
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

    @app.template_filter("due")
    def due_label(value):
        if not value:
            return ""
        d = date.fromisoformat(value)
        diff = (d - date.today()).days
        if diff == 0:
            return tr("Today")
        if diff == 1:
            return tr("Tomorrow")
        if diff == -1:
            return tr("Yesterday")
        return world.fmt_date(d, "d MMM" if d.year == date.today().year else "d MMM y")

    @app.template_filter("due_class")
    def due_class(task):
        if not task.get("due_date") or task.get("status") == "done":
            return ""
        diff = (date.fromisoformat(task["due_date"]) - date.today()).days
        return "overdue" if diff < 0 else "soon" if diff <= 2 else ""

    @app.template_filter("ago")
    def time_ago(ts):
        if not ts:
            return ""
        s = int((datetime.now() - datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")).total_seconds())
        if s < 60:
            return tr("just now")
        if s < 3600:
            return tr("{0} min ago").replace("{0}", str(s // 60))
        if s < 86400:
            return tr("{0} h ago").replace("{0}", str(s // 3600))
        if s < 7 * 86400:
            return tr("{0} d ago").replace("{0}", str(s // 86400))
        return world.fmt_date(ts[:10])

    @app.template_filter("nice_date")
    def nice_date(value):
        return world.fmt_date(value) if value else "—"

    # pass_context: Jinja must not compute these once when the template is compiled ("constant folding"),
    # because the result depends on the language of each visitor
    @pass_context
    def tr_filter(ctx, text):
        return i18n.translate_text(text)

    @pass_context
    def hours_filter(ctx, minutes):
        return fmt_minutes(minutes)

    @pass_context
    def when_filter(ctx, value):
        return world.fmt_datetime(value)

    app.jinja_env.filters["tr"] = tr_filter
    app.jinja_env.filters["hours"] = hours_filter
    app.jinja_env.filters["when"] = when_filter

    # ------------------------------------------------------------ language
    @app.route("/lang/<code>")
    def set_language(code):
        if code in world.LANGUAGES and code in g.site["langs_enabled"].split(","):
            session["lang"] = code
            if g.user:
                store.set_language(g.user["id"], code)
        nxt = request.args.get("next")
        if not nxt and request.referrer:
            ref = urlparse(request.referrer)
            if ref.netloc == request.host:
                nxt = ref.path + ("?" + ref.query if ref.query else "")
        return redirect(safe_next(nxt))

    # ------------------------------------------------------------ auth
    def finish_login(u, remember):
        if u["totp_enabled"]:
            session.clear()
            session["pending_2fa"] = u["id"]
            session["pending_remember"] = bool(remember)
            session["pending_next"] = request.args.get("next", "")
            session["csrf"] = secrets.token_hex(16)
            return redirect(url_for("login_verify"))
        start_session(u, remember)
        flash(tr("Welcome back, {0}!").replace("{0}", u["name"].split()[0]), "success")
        return redirect(safe_next(request.args.get("next")))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = request.form.get("email", "")
            wait = store.login_wait(email, client_ip())
            if wait:                                     # version 4: too many wrong passwords
                flash(tr("Too many wrong tries. Please wait {0} minutes and try again.").replace("{0}", str(wait)), "error")
                return render_template("auth/login.html", email=email), 429
            try:
                u = store.authenticate(email, request.form.get("password"))
            except TaskError as e:
                flash(str(e), "error")
                return render_template("auth/login.html", email=email), 403
            if not u:
                store.login_failed(email, client_ip())
                flash("Wrong email or password.", "error")
                return render_template("auth/login.html", email=email), 401
            store.login_succeeded(email)
            return finish_login(u, request.form.get("remember"))
        return render_template("auth/login.html", email="")

    @app.route("/login/verify", methods=["GET", "POST"])
    def login_verify():
        uid = session.get("pending_2fa")
        if not uid:
            return redirect(url_for("login"))
        if request.method == "POST":
            tries = session.get("pending_tries", 0) + 1
            session["pending_tries"] = tries
            try:
                store.verify_2fa(uid, request.form.get("code", ""))
            except TaskError as e:
                if tries >= 5:
                    session.clear()
                    flash("Too many wrong codes. Please sign in again.", "error")
                    return redirect(url_for("login"))
                flash(str(e), "error")
                return render_template("auth/verify.html"), 401
            remember, nxt = session.get("pending_remember"), session.get("pending_next")
            start_session(store.user(uid), remember)
            return redirect(safe_next(nxt))
        return render_template("auth/verify.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(url_for("dashboard"))
        invite = request.values.get("invite", "")
        joining = store.workspace_by_invite(invite) if invite else None
        if store.site("allow_registration") != "1" and store.count_users() > 0 and not joining:
            flash("New sign-ups are turned off. Please ask your admin to add you.", "error")
            return redirect(url_for("login"))
        if request.method == "POST":
            f = request.form
            try:
                if f.get("password") != f.get("confirm"):
                    raise TaskError("Passwords do not match.")
                uid, ws = store.register(f.get("name"), f.get("email"), f.get("password"), f.get("job_title", ""),
                                         g.lang, invite)
            except TaskError as e:
                flash(str(e), "error")
                return render_template("auth/register.html", form=f, joining=joining, invite=invite), 400
            start_session(store.user(uid))
            session["ws"] = ws
            flash("Your account is ready. Welcome to TaskFlow!", "success")
            return redirect(url_for("dashboard"))
        return render_template("auth/register.html", form={}, joining=joining, invite=invite)

    @app.route("/logout", methods=["POST"])
    def logout():
        lang = session.get("lang")
        session.clear()
        session["lang"] = lang
        flash("You have been signed out.", "success")
        return redirect(url_for("login"))

    @app.route("/forgot", methods=["GET", "POST"])
    def forgot():
        if request.method == "POST":
            sent = store.start_password_reset(request.form.get("email"), base_url())
            if not sent and not store.notifier.email_provider():
                flash("Note: this demo site has no email service switched on yet, so no email can be sent. "
                      "Ask your workspace admin to set a new password for you.", "info")
            else:
                flash("If this email belongs to an account, we sent a link to choose a new password. "
                      "It works for 30 minutes.", "success")
            return redirect(url_for("login"))
        return render_template("auth/forgot.html")

    @app.route("/reset/<token>", methods=["GET", "POST"])
    def reset(token):
        if request.method == "POST":
            try:
                store.reset_password(token, request.form.get("new"), request.form.get("confirm"))
                flash("Your new password is saved. Please sign in.", "success")
                return redirect(url_for("login"))
            except TaskError as e:
                flash(str(e), "error")
        return render_template("auth/reset.html", ok=store.reset_token_ok(token), token=token)

    # ------------------------------------------------------------ workspaces
    @app.route("/workspaces", methods=["GET", "POST"])
    @login_required
    def workspaces():
        if request.method == "POST":
            try:
                made = sum(1 for m in store.memberships(g.user["id"]) if m["created_by"] == g.user["id"])
                if made >= store.site_int("max_workspaces") and not g.user["site_admin"]:
                    raise TaskError("You have made the most workspaces allowed. Ask the site administrator.")
                ws = store.create_workspace(g.user["id"], request.form.get("name"), request.form.get("description"),
                                            request.form.get("color"))
                session["ws"] = ws
                flash("Workspace created. Invite your team from the Team page.", "success")
                return redirect(url_for("dashboard"))
            except TaskError as e:
                flash(str(e), "error")
        return render_template("workspaces.html", mine=store.memberships(g.user["id"]))

    @app.route("/workspaces/<int:ws>/open", methods=["POST"])
    @login_required
    def workspace_switch(ws):
        if not store.member_role(ws, g.user["id"]) and not (g.user["site_admin"] and store.workspace(ws)):
            abort(403)
        session["ws"] = ws
        store.remember_workspace(g.user["id"], ws)
        return redirect(url_for("dashboard"))

    @app.route("/workspace/settings", methods=["GET", "POST"])
    @admin_required
    def workspace_settings():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "invite":
                    store.new_invite_code(g.user["id"], g.ws)
                    flash("A new invite link was made. The old link does not work any more.", "success")
                elif f.get("action") == "delete":
                    store.delete_workspace(g.user["id"], g.ws, f.get("confirm"))
                    session.pop("ws", None)
                    flash("Workspace deleted.", "success")
                    return redirect(url_for("workspaces"))
                elif f.get("action") == "chat":
                    store.save_chat_settings(g.user["id"], g.ws, f.get("slack_url"), f.get("telegram_chat"))
                    flash("Chat messages saved.", "success")
                elif f.get("action") == "clean_tasks":
                    days = int(f["days"]) if f.get("days", "").isdigit() else 30
                    n = store.delete_completed_tasks(g.user["id"], g.ws, days)
                    flash(tr("{0} completed tasks removed.").replace("{0}", str(n)), "success")
                else:
                    store.save_workspace(g.user["id"], g.ws, f.get("name"), f.get("description"), f.get("color"),
                                         f.get("default_priority"))
                    flash("Settings saved.", "success")
            except TaskError as e:
                flash(str(e), "error")
            return redirect(url_for("workspace_settings"))
        invite_link = base_url() + "join/" + g.workspace["invite_code"]
        return render_template("workspace_settings.html", invite_link=invite_link,
                               telegram_ready=bool(env.get("TELEGRAM_BOT_TOKEN")))

    @app.route("/join/<code>", methods=["GET", "POST"])
    def join(code):
        ws = store.workspace_by_invite(code)
        if not ws:
            flash("This invite link is not valid any more. Ask for a new one.", "error")
            return redirect(url_for("login"))
        if not g.user:
            return render_template("auth/join.html", ws=ws, code=code)
        if store.member_role(ws["id"], g.user["id"]):
            session["ws"] = ws["id"]
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            store.add_member(ws["id"], g.user["id"], "member")
            session["ws"] = ws["id"]
            store.remember_workspace(g.user["id"], ws["id"])
            flash(tr("You joined “{0}”.").replace("{0}", ws["name"]), "success")
            return redirect(url_for("dashboard"))
        return render_template("auth/join.html", ws=ws, code=code)

    @app.route("/workspace/leave", methods=["POST"])
    @login_required
    def workspace_leave():
        try:
            if len(store.memberships(g.user["id"])) <= 1:
                raise TaskError("This is your only workspace. Create another one first.")
            store.remove_member(g.user["id"], g.ws, g.user["id"])
            session.pop("ws", None)
            flash("You left the workspace.", "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(url_for("dashboard"))

    # ------------------------------------------------------------ dashboard
    @app.route("/")
    @login_required
    def dashboard():
        uid = g.user["id"]
        mine = store.tasks(g.ws, assignee=uid, status="open")
        return render_template(
            "dashboard.html",
            my=store.stats(g.ws, uid), team=store.stats(g.ws),
            overdue=[t for t in mine if t["due_date"] and t["due_date"] < date.today().isoformat()],
            upcoming=[t for t in mine if not t["due_date"] or t["due_date"] >= date.today().isoformat()][:7],
            activity=store.activity(g.ws, 12), chart=store.completed_per_day(g.ws, 14), projects=store.projects(g.ws))

    # ------------------------------------------------------------ board
    @app.route("/board")
    @login_required
    def board():
        a = request.args
        tasks = store.tasks(g.ws, q=a.get("q", ""), assignee=a.get("assignee", ""), project=a.get("project", ""),
                            priority=a.get("priority", ""), sort="priority")
        tasks = filter_v3(tasks, a)
        columns = {key: [t for t in tasks if t["status"] == key] for key, _ in STATUSES}
        custom = store.columns(g.ws)                                   # version 4: the workspace's own columns
        placed = store.place_in_columns(custom, tasks) if custom else {}
        return render_template("board.html", columns=columns, custom=custom, placed=placed,
                               users=store.users(g.ws, active_only=True),
                               projects=store.projects(g.ws), args=a, labels=store.labels(g.ws),
                               sprints=store.sprints(g.ws), tlabels=store.task_labels([t["id"] for t in tasks]))

    @app.route("/api/tasks/<int:task_id>/status", methods=["POST"])
    @login_required
    def api_status(task_id):
        t = get_task_or_404(task_id)
        if not store.can_edit(g.user, t):
            return jsonify(ok=False, error=tr("You can only move tasks you created or that are assigned to you.")), 403
        data = request.get_json(silent=True) or {}
        try:
            if str(data.get("column") or "").isdigit():                # version 4: custom column (WIP limit)
                col = store.move_to_column(g.user["id"], g.ws, task_id, int(data["column"]))
                return jsonify(ok=True, status=col["base_status"], label=col["name"])
            store.set_status(g.user["id"], g.ws, task_id, data.get("status"))
        except TaskError as e:
            return jsonify(ok=False, error=tr(str(e))), 400
        return jsonify(ok=True, status=data.get("status"), label=tr(STATUS_NAMES[data.get("status")]))

    # ------------------------------------------------------------ live updates (WebSocket + polling fallback)
    @app.route("/api/events")
    @login_required
    def api_events():
        after = request.args.get("after", type=int)
        if after is None:
            return jsonify(last=store.latest_event_id(), events=[])
        events = store.events_after(g.ws, g.user["id"], after)
        return jsonify(last=events[-1]["id"] if events else after,
                       events=[{"id": e["id"], "kind": e["kind"], "task": e["task_id"], "by": e["user_id"],
                                "text": tr(e["text"]), "me": e["user_id"] == g.user["id"]} for e in events],
                       unseen=store.unseen_count(g.user["id"]))

    try:
        from flask_sock import Sock
        sock = Sock(app)

        @sock.route("/ws")
        def live_socket(ws_conn):
            """Sends every change of the workspace to the open page at once. Works across several servers,
            because the changes are read from the events table."""
            if not g.user:
                ws_conn.close()
                return
            last = store.latest_event_id()
            lang, user_id, workspace = g.lang, g.user["id"], g.ws
            ws_conn.send(json.dumps({"hello": True, "last": last}))
            idle = 0
            while ws_conn.connected:
                with LIVE_SIGNAL:
                    LIVE_SIGNAL.wait(timeout=2)
                events = store.events_after(workspace, user_id, last)
                for e in events:
                    last = e["id"]
                    ws_conn.send(json.dumps({"id": e["id"], "kind": e["kind"], "task": e["task_id"], "by": e["user_id"],
                                             "text": i18n.translate_text(e["text"], lang), "me": e["user_id"] == user_id,
                                             "unseen": store.unseen_count(user_id) if e["kind"] == "notification" else None}))
                idle = 0 if events else idle + 1
                if idle >= 12:                         # every ~25 s: keep the connection alive through proxies
                    ws_conn.send('{"ping":1}')
                    idle = 0
        app.config["WEBSOCKETS"] = True
    except ImportError:                                 # flask-sock not installed: pages fall back to polling
        app.config["WEBSOCKETS"] = False

    # ------------------------------------------------------------ task list
    @app.route("/tasks")
    @login_required
    def task_list():
        a = request.args
        tasks = store.tasks(g.ws, q=a.get("q", "").strip(), status=a.get("status", ""), priority=a.get("priority", ""),
                            assignee=a.get("assignee", ""), project=a.get("project", ""), due=a.get("due", ""),
                            sort=a.get("sort", "due"), creator=a.get("creator", ""))
        tasks = filter_v3(tasks, a)
        return render_template("tasks.html", tasks=tasks, users=store.users(g.ws), projects=store.projects(g.ws), args=a,
                               labels=store.labels(g.ws), sprints=store.sprints(g.ws),
                               tlabels=store.task_labels([t["id"] for t in tasks]))

    def filter_v3(tasks, a):
        """Version 3 filters: label and sprint."""
        if a.get("label", "").isdigit():
            keep = store.tasks_with_label(g.ws, int(a["label"]))
            tasks = [t for t in tasks if t["id"] in keep]
        if a.get("sprint") == "none":
            tasks = [t for t in tasks if not t["sprint_id"]]
        elif a.get("sprint", "").isdigit():
            tasks = [t for t in tasks if t["sprint_id"] == int(a["sprint"])]
        return tasks

    @app.route("/my-tasks")
    @login_required
    def my_tasks():
        return redirect(url_for("task_list", assignee=g.user["id"], status="open"))

    def form_from_task(t):
        form = {k: ("" if t[k] is None else str(t[k])) for k in
                ("title", "description", "status", "priority", "assignee_id", "project_id", "due_date", "start_date",
                 "sprint_id", "repeat")}
        form["estimate"] = fmt_minutes(t["estimate_minutes"], "en") if t["estimate_minutes"] else ""
        form["labels"] = [str(lb["id"]) for lb in store.task_labels([t["id"]])[t["id"]]]
        for fid, v in store.field_values([t["id"]])[t["id"]].items():
            form[f"cf_{fid}"] = v
        return form

    def task_form_data():
        """The form as a dict; the labels are a list (several checkboxes with the same name)."""
        data = request.form.to_dict()
        if "labels_sent" in request.form:
            data["labels"] = request.form.getlist("labels")
        return data

    def task_form_page(form, task):
        return render_template("task_form.html", form=form, task=task, users=store.users(g.ws, active_only=True),
                               projects=store.projects(g.ws), labels=store.labels(g.ws), fields=store.fields(g.ws),
                               templates=store.templates(g.ws) if not task else [],
                               sprints=[s for s in store.sprints(g.ws) if s["status"] != "closed"])

    @app.route("/tasks/new", methods=["GET", "POST"])
    @login_required
    def task_new():
        if request.method == "POST":
            data = task_form_data()
            try:
                values = store.clean_field_values(g.ws, request.form)
                tid = store.create_task(g.user["id"], g.ws, data)
                store.set_field_values(g.ws, tid, values)
                flash("Task created.", "success")
                return redirect(url_for("task_detail", task_id=tid))
            except TaskError as e:
                flash(str(e), "error")
                form = data
        else:
            active = next((s for s in store.sprints(g.ws) if s["status"] == "active"), None)
            form = {"status": request.args.get("status", "todo"), "project_id": request.args.get("project", ""),
                    "assignee_id": str(g.user["id"]), "priority": g.workspace["default_priority"],
                    "sprint_id": request.args.get("sprint", str(active["id"]) if active else ""),
                    "labels": request.args.getlist("label")}
            tpl = store.template(g.ws, request.args.get("template", type=int) or 0)
            if tpl:                                       # version 4: start from a task template
                form.update(title=tpl["title"], description=tpl["description"], priority=tpl["priority"],
                            project_id=str(tpl["project_id"] or form["project_id"]), labels=[str(x) for x in tpl["labels"]],
                            estimate=fmt_minutes(tpl["estimate_minutes"], "en") if tpl["estimate_minutes"] else "",
                            template_id=str(tpl["id"]))
        return task_form_page(form, None)

    @app.route("/tasks/<int:task_id>")
    @login_required
    def task_detail(task_id):
        t = store.task(task_id, ws=g.ws)
        if not t:                                   # a link to a task of another of my workspaces: switch to it
            other = store.task(task_id)
            if other and store.member_role(other["workspace_id"], g.user["id"]):
                session["ws"] = other["workspace_id"]
                store.remember_workspace(g.user["id"], other["workspace_id"])
                return redirect(request.full_path if request.query_string else request.path)
            abort(404)
        suggestion = None
        if t["status"] != "done" and not t["estimate_minutes"]:
            suggestion = store.suggest_estimate(g.ws, t["title"], t["project_id"], exclude=task_id,
                                                assignee_id=t["assignee_id"], priority=t["priority"],
                                                labels=[lb["id"] for lb in store.task_labels([task_id])[task_id]])
        return render_template("task_detail.html", fields=store.fields(g.ws), values=store.field_values([task_id])[task_id], t=t, comments=store.comments(task_id),
                               subtasks=store.subtasks(task_id), history=store.activity(g.ws, 30, task_id=task_id),
                               files=store.attachments(task_id), entries=store.time_entries(task_id),
                               members=store.users(g.ws, active_only=True), labels=store.task_labels([task_id])[task_id],
                               blockers=store.blockers(task_id), blocking=store.blocking(task_id),
                               candidates=[x for x in store.tasks(g.ws, sort="title") if x["id"] != task_id],
                               sprint=store.sprint(t["sprint_id"], g.ws) if t["sprint_id"] else None,
                               suggestion=suggestion, REPEATS=REPEATS)

    # ----- checklist inside a task (anyone who can edit the task)
    def editable_task(task_id):
        t = get_task_or_404(task_id)
        if not store.can_edit(g.user, t):
            abort(403)
        return t

    def back_to(task_id, anchor):
        return redirect(url_for("task_detail", task_id=task_id) + "#" + anchor)

    @app.route("/tasks/<int:task_id>/checklist", methods=["POST"])
    @login_required
    def checklist_add(task_id):
        editable_task(task_id)
        try:
            store.add_subtask(g.user["id"], task_id, request.form.get("title"))
        except TaskError as e:
            flash(str(e), "error")
        return back_to(task_id, "checklist")

    @app.route("/checklist/<int:sub_id>/<action>", methods=["POST"])
    @login_required
    def checklist_change(sub_id, action):
        sub = store.subtask(sub_id)
        if not sub:
            abort(404)
        editable_task(sub["task_id"])
        try:
            if action == "toggle":
                store.update_subtask(g.user["id"], sub_id, done=not sub["done"])
            elif action == "edit":
                store.update_subtask(g.user["id"], sub_id, title=request.form.get("title", ""))
            elif action == "delete":
                store.delete_subtask(g.user["id"], sub_id)
            else:
                abort(404)
        except TaskError as e:
            flash(str(e), "error")
        if request.headers.get("X-Requested-With") == "fetch":
            t = store.task(sub["task_id"])
            return jsonify(ok=True, done=t["sub_done"], total=t["sub_total"])
        return back_to(sub["task_id"], "checklist")

    @app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
    @login_required
    def task_edit(task_id):
        t = editable_task(task_id)
        form = form_from_task(t)
        if request.method == "POST":
            data = task_form_data()
            try:
                values = store.clean_field_values(g.ws, request.form)
                store.update_task(g.user["id"], g.ws, task_id, data)
                store.set_field_values(g.ws, task_id, values)
                flash("Task updated.", "success")
                return redirect(url_for("task_detail", task_id=task_id))
            except TaskError as e:
                flash(str(e), "error")
                form = data
        return task_form_page(form, t)

    @app.route("/tasks/<int:task_id>/status", methods=["POST"])
    @login_required
    def task_status(task_id):
        editable_task(task_id)
        try:
            store.set_status(g.user["id"], g.ws, task_id, request.form.get("status"))
            flash(tr("Status changed to {0}.").replace("{0}", tr(STATUS_NAMES[request.form["status"]])), "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(safe_next(request.form.get("next")) if request.form.get("next")
                        else url_for("task_detail", task_id=task_id))

    @app.route("/tasks/<int:task_id>/delete", methods=["POST"])
    @login_required
    def task_delete(task_id):
        t = get_task_or_404(task_id)
        if not store.can_delete(g.user, t):
            abort(403)
        store.delete_task(g.user["id"], g.ws, task_id)
        flash(tr("Task “{0}” was moved to the trash.").replace("{0}", t["title"]), "success")
        return redirect(url_for("task_list"))

    @app.route("/tasks/<int:task_id>/comment", methods=["POST"])
    @login_required
    def task_comment(task_id):
        get_task_or_404(task_id)
        try:
            store.add_comment(g.user["id"], g.ws, task_id, request.form.get("body"))
        except TaskError as e:
            flash(str(e), "error")
        return back_to(task_id, "comments")

    # ----- attachments
    @app.route("/tasks/<int:task_id>/files", methods=["POST"])
    @login_required
    def file_upload(task_id):
        get_task_or_404(task_id)                       # every member of the workspace may add files
        files = [f for f in request.files.getlist("file") if f and f.filename]
        if not files:
            flash("Please choose a file.", "error")
        for f in files[:10]:
            try:
                store.add_attachment(g.user["id"], task_id, f.filename, f.read())
            except TaskError as e:
                flash(f"{f.filename}: {tr(str(e))}", "error")
                break
        else:
            if files:
                flash("File uploaded." if len(files) == 1 else tr("{0} files uploaded.").replace("{0}", str(len(files))),
                      "success")
        return back_to(task_id, "files")

    def member_file(file_id, with_data=False):
        a = store.attachment(file_id, with_data=with_data)
        if not a or not store.member_role(a["workspace_id"], g.user["id"]):
            abort(404)
        return a

    @app.route("/files/<int:file_id>")
    @login_required
    def file_download(file_id):
        a = member_file(file_id, with_data=True)
        inline = a["is_image"] or a["mime"] == "application/pdf"
        resp = send_file(io.BytesIO(a["bytes"]), mimetype=a["mime"], as_attachment=not inline, download_name=a["name"])
        resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
        resp.headers["Cache-Control"] = "private, max-age=3600"
        return resp

    @app.route("/files/<int:file_id>/delete", methods=["POST"])
    @login_required
    def file_delete(file_id):
        a = member_file(file_id)
        t = get_task_or_404(a["task_id"])
        if not (store.can_edit(g.user, t) or a["user_id"] == g.user["id"]):
            abort(403)
        store.delete_attachment(g.user["id"], file_id)
        flash("File removed.", "success")
        return back_to(a["task_id"], "files")

    # ----- time tracking
    @app.route("/tasks/<int:task_id>/timer", methods=["POST"])
    @login_required
    def timer(task_id):
        get_task_or_404(task_id)
        if request.form.get("action") == "stop":
            minutes = store.stop_timer(g.user["id"])
            if minutes:
                flash(tr("Timer stopped: {0} logged.").replace("{0}", fmt_minutes(minutes)), "success")
        else:
            stopped = store.start_timer(g.user["id"], task_id)
            flash("Timer started." if not stopped else
                  tr("Timer started. The previous timer was stopped ({0}).").replace("{0}", fmt_minutes(stopped)),
                  "success")
        return redirect(safe_next(request.form.get("next")) if request.form.get("next") else
                        url_for("task_detail", task_id=task_id) + "#time")

    @app.route("/timer/stop", methods=["POST"])
    @login_required
    def timer_stop():
        minutes = store.stop_timer(g.user["id"])
        if minutes:
            flash(tr("Timer stopped: {0} logged.").replace("{0}", fmt_minutes(minutes)), "success")
        return redirect(safe_next(request.form.get("next")))

    @app.route("/tasks/<int:task_id>/time", methods=["POST"])
    @login_required
    def time_log(task_id):
        get_task_or_404(task_id)
        try:
            minutes = parse_duration(request.form.get("time"))
            if not minutes:
                raise TaskError("Write the time like 1.5, 90m or 1h 30m.")
            store.log_time(g.user["id"], task_id, minutes, request.form.get("day"), request.form.get("note"))
            flash(tr("{0} logged.").replace("{0}", fmt_minutes(minutes)), "success")
        except TaskError as e:
            flash(str(e), "error")
        return back_to(task_id, "time")

    @app.route("/time/<int:entry_id>/delete", methods=["POST"])
    @login_required
    def time_delete(entry_id):
        e = store.time_entry(entry_id)
        if not e or e["workspace_id"] != g.ws:
            abort(404)
        try:
            task_id = store.delete_time_entry(g.user, entry_id)
        except PermissionError:
            abort(403)
        flash("Time entry removed.", "success")
        return back_to(task_id, "time")

    # ------------------------------------------------------------ calendar
    @app.route("/calendar")
    @login_required
    def calendar_view():
        try:
            month = datetime.strptime(request.args.get("m", ""), "%Y-%m").date()
        except ValueError:
            month = date.today().replace(day=1)
        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)
        tasks = store.tasks_between(g.ws, weeks[0][0], weeks[-1][-1])
        by_day = {}
        for t in tasks:
            by_day.setdefault(t["due_date"], []).append(t)
        prev_m = (month - timedelta(days=1)).replace(day=1)
        next_m = (month + timedelta(days=32)).replace(day=1)
        return render_template("calendar.html", weeks=weeks, month=month, by_day=by_day,
                               month_title=world.month_title(month), weekdays=world.weekday_names(),
                               prev_m=prev_m.strftime("%Y-%m"), next_m=next_m.strftime("%Y-%m"))

    # ------------------------------------------------------------ projects
    @app.route("/projects")
    @login_required
    def projects():
        return render_template("projects.html", projects=store.projects(g.ws, include_archived=True))

    def managed_project(project_id):
        p = store.project(project_id, ws=g.ws)
        if not p:
            abort(404)
        if not store.can_manage_project(g.user, p):
            abort(403)
        return p

    @app.route("/projects/save", methods=["POST"])
    @login_required
    def project_save():
        f = request.form
        pid = int(f["id"]) if f.get("id", "").isdigit() else None
        if pid:
            managed_project(pid)                      # only the owner or an admin may edit
        try:
            store.save_project(g.user["id"], g.ws, f.get("name"), f.get("description"), f.get("color"), pid)
            flash("Project saved.", "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(url_for("projects"))

    @app.route("/projects/<int:project_id>/archive", methods=["POST"])
    @login_required
    def project_archive(project_id):
        managed_project(project_id)
        store.toggle_archive(g.user["id"], g.ws, project_id)
        flash("Project updated.", "success")
        return redirect(url_for("projects"))

    @app.route("/projects/<int:project_id>/delete", methods=["POST"])
    @login_required
    def project_delete(project_id):
        managed_project(project_id)
        try:
            store.delete_project(g.user["id"], g.ws, project_id, delete_tasks=request.form.get("mode") == "tasks")
            flash("Project deleted.", "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(url_for("projects"))

    def comment_in_workspace(comment_id):
        c = store.comment(comment_id)
        if not c or c["workspace_id"] != g.ws:
            abort(404)
        return c

    @app.route("/comments/<int:comment_id>/edit", methods=["POST"])
    @login_required
    def comment_edit(comment_id):
        comment_in_workspace(comment_id)
        try:
            task_id = store.edit_comment(g.user, comment_id, request.form.get("body"))
            flash("Comment updated.", "success")
        except PermissionError:
            abort(403)
        except TaskError as e:
            flash(str(e), "error")
            return redirect(request.referrer or url_for("dashboard"))
        return back_to(task_id, "comments")

    @app.route("/comments/<int:comment_id>/delete", methods=["POST"])
    @login_required
    def comment_delete(comment_id):
        comment_in_workspace(comment_id)
        try:
            task_id = store.delete_comment(g.user, comment_id)
        except TaskError:
            abort(404)
        except PermissionError:
            abort(403)
        flash("Comment deleted.", "success")
        return back_to(task_id, "comments")

    @app.route("/tasks/bulk", methods=["POST"])
    @admin_required
    def task_bulk():
        f = request.form
        try:
            n = store.bulk_update(g.user["id"], g.ws, f.getlist("ids"), f.get("action"), f.get("value"))
            flash(tr("{0} tasks updated.").replace("{0}", str(n)), "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(safe_next(f.get("next")))

    # ------------------------------------------------------------ notifications
    @app.route("/notifications", methods=["GET", "POST"])
    @login_required
    def notifications():
        if request.method == "POST":
            store.mark_seen(g.user["id"])
            return redirect(url_for("notifications"))
        rows = store.notifications(g.user["id"])
        store.mark_seen(g.user["id"])
        return render_template("notifications.html", rows=rows)

    @app.route("/cron/run", methods=["GET", "POST"])
    def cron_run():
        """For an outside scheduler (cron-job.org, GitHub Actions): /cron/run?key=CRON_KEY sends due reminders."""
        key = env.get("CRON_KEY", "")
        if not key or not secrets.compare_digest(request.args.get("key", ""), key):
            abort(404)
        return jsonify(ok=True, **(store.housekeeping(force=True) or {}))

    # ------------------------------------------------------------ admin panel
    @app.route("/admin")
    @site_admin_required
    def admin_panel():
        tab = request.args.get("tab", "site")
        if tab not in ("site", "data", "maintenance"):
            tab = "site"
        table = request.args.get("table", "tasks")
        if table not in store.TABLES:
            table = "tasks"
        cols, rows, total = store.table_rows(table, limit=200) if tab == "data" else ([], [], 0)
        services = {"database": store.describe_database(), "email": store.notifier.email_provider(),
                    "websockets": app.config["WEBSOCKETS"], "cron": bool(env.get("CRON_KEY")),
                    "app_url": env.get("APP_URL", "")}
        return render_template("admin.html", counts=store.table_counts(),
                               table=table, cols=cols, rows=rows, total=total, tab=tab, services=services,
                               section="overview")

    @app.route("/admin/settings", methods=["GET", "POST"])
    @site_admin_required
    def admin_settings():
        """Version 2 address: the settings now live on /admin/site."""
        if request.method == "POST":
            store.save_site(g.user["id"], {"allow_registration": request.form.get("allow_registration", "")})
            flash("Settings saved.", "success")
        return redirect(url_for("admin_site"))

    @app.route("/admin/data/<table>.csv")
    @site_admin_required
    def admin_table_csv(table):
        try:
            cols, rows, _ = store.table_rows(table, limit=1_000_000)
        except TaskError:
            abort(404)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        w.writerows(rows)
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=taskflow_{table}_{date.today()}.csv"})

    @app.route("/admin/backup.json.gz")
    @site_admin_required
    def admin_backup():
        data = {"app": "TaskFlow", "version": 3, "created_at": datetime.now().isoformat(timespec="seconds"),
                "tables": {}}
        with store.db() as con:
            for t in store.TABLES:
                cur = con.execute(f"SELECT * FROM {t}")
                keys = [c[0] for c in cur.description]
                data["tables"][t] = [dict(zip(keys, list(r))) for r in cur.fetchall()
                                     if not (t == "settings" and r[0] in ("secret_key", "vapid"))]
        blob = gzip.compress(json.dumps(data, default=str).encode())
        return Response(blob, mimetype="application/gzip", headers={
            "Content-Disposition": f"attachment; filename=taskflow_backup_{date.today()}.json.gz"})

    @app.route("/admin/maintenance", methods=["POST"])
    @site_admin_required
    def admin_maintenance():
        f = request.form
        action = f.get("action")
        days = int(f["days"]) if f.get("days", "").isdigit() else 0
        if action == "clear_activity":
            n = store.clear_activity(g.user["id"], days)
            flash(tr("{0} activity entries removed.").replace("{0}", str(n)), "success")
        elif action == "clean_tasks":
            n = store.delete_completed_tasks(g.user["id"], g.ws, days)
            flash(tr("{0} completed tasks removed.").replace("{0}", str(n)), "success")
        elif action == "send_reminders":
            n = store.send_reminders()
            flash(tr("{0} reminders sent.").replace("{0}", str(n)), "success")
        elif action == "reset_demo":
            if f.get("confirm") != "RESET":
                flash("Type RESET to confirm.", "error")
            else:
                from demo_data import load_demo
                store.wipe()
                load_demo(store)
                session.clear()
                flash("All data was reset to the demo data. Please sign in again.", "success")
                return redirect(url_for("login"))
        else:
            abort(400)
        return redirect(url_for("admin_panel", tab="maintenance"))

    # ------------------------------------------------------------ team (members of the workspace)
    @app.route("/team")
    @login_required
    def team():
        return render_template("team.html", people=store.user_report(g.ws), workload=store.workload(g.ws),
                               invite_link=base_url() + "join/" + g.workspace["invite_code"])

    @app.route("/team/add", methods=["POST"])
    @admin_required
    def team_add():
        f = request.form
        try:
            _, created = store.invite_member(g.user["id"], g.ws, f.get("name"), f.get("email"), f.get("password"),
                                             role=f.get("role"), job_title=f.get("job_title", ""))
            flash(tr("{0} was added to the team.").replace("{0}", f.get("name") or f.get("email")) if created else
                  "This person already had an account and was added to the workspace.", "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(url_for("team"))

    @app.route("/team/<int:user_id>/<action>", methods=["POST"])
    @admin_required
    def team_action(user_id, action):
        if not store.member_role(g.ws, user_id):
            abort(404)
        site_only = {"toggle", "reset", "edit", "delete", "reset2fa"}
        if action in site_only and not g.user["site_admin"]:
            abort(403)
        try:
            if action == "role":
                store.set_member_role(g.user["id"], g.ws, user_id, request.form.get("role"))
            elif action == "remove":
                store.remove_member(g.user["id"], g.ws, user_id)
                flash("Member removed from the workspace. Their tasks here are now unassigned.", "success")
                return redirect(url_for("team"))
            elif action == "toggle":
                store.admin_toggle_active(g.user["id"], user_id)
            elif action == "reset":
                store.admin_reset_password(g.user["id"], user_id, request.form.get("password"))
            elif action == "reset2fa":
                store.reset_2fa(g.user["id"], user_id)
            elif action == "edit":
                f = request.form
                store.admin_update_user(g.user["id"], user_id, f.get("name"), f.get("email"), f.get("job_title"),
                                        f.get("color"))
            elif action == "delete":
                name = store.user(user_id)["name"]
                store.delete_user(g.user["id"], user_id)
                flash(tr("{0}'s account was deleted. Their tasks are now unassigned.").replace("{0}", name), "success")
                return redirect(url_for("team"))
            else:
                abort(404)
            flash("Team member updated.", "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(url_for("team"))

    # ------------------------------------------------------------ reports
    @app.route("/reports")
    @login_required
    def reports():
        a = request.args
        project = a.get("project", type=int)
        if project and not store.project(project, ws=g.ws):
            project = None
        metric = a.get("metric") if a.get("metric") in ("tasks", "hours") else "tasks"
        days = a.get("days", type=int) or 14
        bd = store.burndown(g.ws, project, days, metric)
        return render_template("reports.html", s=store.stats(g.ws), people=store.user_report(g.ws),
                               projects=store.projects(g.ws), chart=store.completed_per_day(g.ws, 30),
                               activity=store.activity(g.ws, 30), bd=bd, svg=store.chart_svg_points(bd),
                               time=store.time_report(g.ws, 30), args=a, project=project, metric=metric,
                               days=len([p for p in bd["points"] if p["remaining"] is not None]))

    @app.route("/reports/tasks.csv")
    @login_required
    def export_csv():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ID", "Title", "Project", "Status", "Priority", "Assigned to", "Created by", "Due date",
                    "Estimate (h)", "Logged (h)", "Created", "Completed"])
        for t in store.tasks(g.ws, sort="due"):
            w.writerow([t["id"], t["title"], t["project_name"] or "", STATUS_NAMES[t["status"]],
                        PRIORITY_NAMES[t["priority"]], t["assignee_name"] or "Unassigned", t["creator_name"],
                        t["due_date"] or "", round((t["estimate_minutes"] or 0) / 60, 2) or "",
                        round(t["logged_minutes"] / 60, 2) or "", t["created_at"], t["completed_at"] or ""])
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=taskflow_tasks_{date.today()}.csv"})

    @app.route("/reports/time.csv")
    @login_required
    def export_time_csv():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Start", "End", "Minutes", "Hours", "Person", "Task", "Project", "Note"])
        for e in store.time_entries_csv(g.ws):
            w.writerow([e["started_at"], e["ended_at"], e["minutes"], round(e["minutes"] / 60, 2), e["person"],
                        e["task"], e["project"], e["note"]])
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=taskflow_time_{date.today()}.csv"})

    # ------------------------------------------------------------ profile
    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        uid = g.user["id"]
        if request.method == "POST":
            f = request.form
            try:
                form = f.get("form")
                if form == "password":
                    store.change_password(uid, f.get("old"), f.get("new"), f.get("confirm"))
                    flash("Password changed.", "success")
                elif form == "delete":
                    store.delete_own_account(uid, f.get("password"))
                    session.clear()
                    flash("Your account was deleted. Goodbye!", "success")
                    return redirect(url_for("login"))
                elif form == "notify":
                    store.save_notify_settings(uid, f.get("email"), f.get("remind_days"))
                    with store.db() as con:
                        con.execute("UPDATE users SET notify_push = ? WHERE id = ?", (1 if f.get("push") else 0, uid))
                    flash("Notification settings saved.", "success")
                elif form == "photo":
                    up = request.files.get("photo")
                    store.set_photo(uid, uid, up.read() if up and up.filename else b"")
                    flash("Photo saved." if up and up.filename else "Photo removed.", "success")
                elif form == "photo_off":
                    store.set_photo(uid, uid, b"")
                    flash("Photo removed.", "success")
                elif form == "sign_out_all":
                    store.bump_session(uid)
                    session.clear()
                    flash("You were signed out on every device. Please sign in again.", "success")
                    return redirect(url_for("login"))
                elif form == "ical_new":
                    store.ical_token(uid, renew=True)
                    flash("A new calendar link was made. The old one does not work any more.", "success")
                    return redirect(url_for("profile") + "#calendar")
                elif form == "2fa_on":
                    secret = session.get("totp_setup")
                    if not secret:
                        raise TaskError("Please start again.")
                    store.enable_2fa(uid, secret, f.get("code", ""))
                    session.pop("totp_setup", None)
                    flash("Two-factor login is ON. From now on you also need the code from your app to sign in.",
                          "success")
                elif form == "2fa_off":
                    store.disable_2fa(uid, f.get("password"), f.get("code", ""))
                    flash("Two-factor login is OFF.", "success")
                else:
                    store.save_person(uid, uid, f, own=True)
                    if f.get("lang") in world.LANGUAGES:
                        session["lang"] = f.get("lang")
                    flash("Profile saved.", "success")
            except TaskError as e:
                flash(str(e), "error")
            return redirect(url_for("profile") + ("#security" if (f.get("form") or "").startswith("2fa") else ""))
        totp = None
        if not g.user["totp_enabled"]:
            session.setdefault("totp_setup", otp.new_secret())
            secret = session["totp_setup"]
            totp = {"secret": otp.grouped(secret) if hasattr(otp, "grouped") else secret,
                    "uri": otp.uri(secret, g.user["email"], issuer="TaskFlow")}
        return render_template("profile.html", s=store.stats(g.ws, uid),
                               created=store.tasks(g.ws, creator=uid, sort="updated")[:8], totp=totp,
                               ical_url=base_url() + "ical/" + store.ical_token(uid) + ".ics",
                               push_count=store.push_count(uid),
                               my_projects=[p for p in store.projects(g.ws, include_archived=True)
                                            if p["created_by"] == uid])

    # ------------------------------------------------------------ about
    @app.route("/about")
    def about():
        return render_template("about.html")

    # ------------------------------------------------------------ version 3 pages
    import admin_views
    import planning_views
    planning_views.register(app, store, login_required, admin_required, base_url, env)
    admin_views.register(app, store, site_admin_required, base_url, env)

    # ------------------------------------------------------------ version 4 pages
    import v4_views
    v4_views.register(app, store, login_required, admin_required, site_admin_required, base_url, env)

    # ------------------------------------------------------------ resumes
    import resume_views
    resume_views.register(app, store, login_required)

    # ------------------------------------------------------------ errors
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, title="Access denied",
                               text="You do not have permission to do this."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, title="Page not found",
                               text="This page or task does not exist (maybe it was deleted)."), 404

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("error.html", code=400, title="Something went wrong",
                               text=getattr(e, "description", "Bad request.")), 400

    @app.errorhandler(413)
    def too_big(e):
        return render_template("error.html", code=413, title="The file is too big",
                               text=tr("Files can be at most {0} MB.").replace("{0}", str(store.site_int("max_file_mb")))), 413

    return app


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", 5000)))
