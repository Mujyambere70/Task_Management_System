"""
v4_views.py - pages and addresses added in TaskFlow version 4.

Workspace admins
    /workspace/tools/board          own board columns, WIP limits
    /workspace/tools/fields         custom fields of tasks
    /workspace/tools/automations    "when ... then ..." rules
    /workspace/tools/webhooks       signed calls to other systems
Every member
    /templates                      task templates (and "use" -> a new task with its checklist)
    /views/save, /views/<id>/delete saved filters of the task list and the board (sidebar)
    /developer                      personal API keys and the API guide
    /calendar/import                import an .ics file, follow a calendar address
    /reports/estimates              how good the suggested estimates are (version 3 vs version 4)
    /translate/suggest              suggest better words for your language
    /settings/theme, /settings/digest   dark mode, weekly email
    /install, /offline              install the app (also on iPhone), the page shown without internet
Site administrator
    /admin/suggestions              approve or reject translation suggestions
    /admin/system                   health, storage, live updates, sign-in providers
    /admin/outbox/<id>/retry        send a failed email or webhook again
Anyone
    /auth/sso/<provider>[/callback] sign in with Google or Microsoft
    /healthz                        health check for Fly.io (database answers)
REST API (key in the "Authorization: Bearer tf_..." header)
    GET  /api/v1/me | /api/v1/projects | /api/v1/tasks | /api/v1/tasks/<id>
    POST /api/v1/tasks | /api/v1/tasks/<id>/comments      PATCH, DELETE /api/v1/tasks/<id>
"""

import time
from collections import defaultdict, deque

from flask import abort, flash, g, jsonify, redirect, render_template, request, session, url_for

import i18n
import oidc
import world
from core import TaskError, fmt_minutes
from v4_store import ACTIONS, BASE_STATES, FIELD_KINDS, TRIGGERS, WEBHOOK_EVENTS

API_PER_MINUTE = 120
SECTIONS = ("board", "fields", "automations", "webhooks")


def register(app, store, login_required, admin_required, site_admin_required, base_url, env):
    def tr(text):
        return i18n.translate_text(text)

    @app.context_processor
    def v4_globals():
        u = g.get("user")
        return dict(saved_views=store.saved_views(u["id"], g.ws) if u and g.ws else [],
                    theme=(u or {}).get("theme") or "auto", SSO=oidc.enabled(env))

    # ------------------------------------------------------------ workspace tools (admins)
    @app.route("/workspace/tools/<section>", methods=["GET", "POST"])
    @admin_required
    def workspace_tools(section):
        if section not in SECTIONS:
            abort(404)
        new_secret = None
        if request.method == "POST":
            f, a, me = request.form, request.form.get("action", ""), g.user["id"]
            num = lambda k: f.get(k, type=int) or 0                         # noqa: E731
            try:
                if section == "board":
                    if a == "start":
                        store.start_custom_columns(me, g.ws)
                    elif a == "save":
                        store.save_column(me, g.ws, f.get("name"), f.get("base_status"), f.get("wip_limit"),
                                          num("column_id") or None)
                    elif a == "delete":
                        store.delete_column(me, g.ws, num("column_id"))
                    elif a in ("up", "down"):
                        store.move_column(me, g.ws, num("column_id"), 1 if a == "down" else -1)
                    elif a == "stop":
                        store.stop_custom_columns(me, g.ws)
                elif section == "fields":
                    if a == "delete":
                        store.delete_field(me, g.ws, num("field_id"))
                    else:
                        store.save_field(me, g.ws, f.get("name"), f.get("kind"), f.get("options", ""),
                                         f.get("required") == "1", num("field_id") or None)
                elif section == "automations":
                    if a == "toggle":
                        store.toggle_automation(me, g.ws, num("rule_id"))
                    elif a == "delete":
                        store.delete_automation(me, g.ws, num("rule_id"))
                    else:
                        store.save_automation(me, g.ws, f)
                elif section == "webhooks":
                    if a == "delete":
                        store.delete_webhook(me, g.ws, num("hook_id"))
                    else:
                        _hid, new_secret = store.save_webhook(me, g.ws, f.get("url"), f.getlist("events"))
                flash(tr("Saved."), "success")
            except TaskError as e:
                flash(tr(str(e)), "error")
            if not new_secret:
                return redirect(url_for("workspace_tools", section=section))
        return render_template("workspace_tools.html", section=section, columns=store.columns(g.ws),
                               fields=store.fields(g.ws), rules=store.automations(g.ws), hooks=store.webhooks(g.ws),
                               new_secret=new_secret, BASE_STATES=BASE_STATES, FIELD_KINDS=FIELD_KINDS,
                               TRIGGERS=TRIGGERS, ACTIONS=ACTIONS, EVENTS=sorted(set(WEBHOOK_EVENTS.values())),
                               members=store.users(g.ws, active_only=True), projects=store.projects(g.ws),
                               labels=store.labels(g.ws))

    # ------------------------------------------------------------ templates
    @app.route("/templates", methods=["GET", "POST"])
    @login_required
    def templates_page():
        edit = store.template(g.ws, request.args.get("edit", type=int) or 0)
        if request.method == "POST":
            data = request.form.to_dict()
            data["labels"] = request.form.getlist("labels")
            try:
                store.save_template(g.user["id"], g.ws, data, request.form.get("template_id", type=int) or None)
                flash(tr("Template saved."), "success")
                return redirect(url_for("templates_page"))
            except TaskError as e:
                flash(tr(str(e)), "error")
                edit = dict(data, steps=data.get("checklist", "").split("\n"),
                            labels=[int(x) for x in data["labels"] if x.isdigit()])
        return render_template("templates_page.html", items=store.templates(g.ws), edit=edit,
                               projects=store.projects(g.ws), labels=store.labels(g.ws))

    @app.route("/templates/<int:tid>/use", methods=["POST"])
    @login_required
    def template_use(tid):
        f = request.form
        try:
            new = store.task_from_template(g.user["id"], g.ws, tid, {
                "due_date": f.get("due_date", ""), "assignee_id": f.get("assignee_id") or str(g.user["id"])})
            flash(tr("Task created."), "success")
            return redirect(url_for("task_detail", task_id=new))
        except TaskError as e:
            flash(tr(str(e)), "error")
            return redirect(url_for("templates_page"))

    @app.route("/templates/<int:tid>/delete", methods=["POST"])
    @login_required
    def template_delete(tid):
        t = store.template(g.ws, tid)
        if not t:
            abort(404)
        if g.user["role"] != "admin" and t["created_by"] != g.user["id"]:
            abort(403)
        store.delete_template(g.user["id"], g.ws, tid)
        flash(tr("Template deleted."), "success")
        return redirect(url_for("templates_page"))

    # ------------------------------------------------------------ saved views
    @app.route("/views/save", methods=["POST"])
    @login_required
    def view_save():
        page = request.form.get("page", "tasks")
        query = request.form.get("query", "")
        try:
            store.save_view(g.user["id"], g.ws, request.form.get("name"), page, query)
            flash(tr("View saved. You find it in the sidebar."), "success")
        except TaskError as e:
            flash(tr(str(e)), "error")
        return redirect(url_for("board" if page == "board" else "task_list") + ("?" + query if query else ""))

    @app.route("/views/<int:vid>/delete", methods=["POST"])
    @login_required
    def view_delete(vid):
        store.delete_view(g.user["id"], vid)
        flash(tr("View deleted."), "success")
        return redirect(request.referrer if (request.referrer or "").startswith(request.host_url) else url_for("task_list"))

    # ------------------------------------------------------------ developer: API keys
    @app.route("/developer", methods=["GET", "POST"])
    @login_required
    def developer():
        token = None
        if request.method == "POST":
            try:
                if request.form.get("action") == "revoke":
                    store.revoke_api_token(g.user["id"], request.form.get("token_id", type=int) or 0)
                    flash(tr("The key was deleted. Programs that used it cannot sign in any more."), "success")
                    return redirect(url_for("developer"))
                token = store.create_api_token(g.user["id"], g.ws, request.form.get("name"), request.form.get("scope", "read"))
            except TaskError as e:
                flash(tr(str(e)), "error")
                return redirect(url_for("developer"))
        return render_template("developer.html", tokens=store.api_tokens(g.user["id"]), new_token=token,
                               api_base=base_url().rstrip("/") + "/api/v1")

    # ------------------------------------------------------------ calendar import / subscriptions
    @app.route("/calendar/import", methods=["GET", "POST"])
    @login_required
    def calendar_import():
        if request.method == "POST":
            a = request.form.get("action", "file")
            try:
                if a == "file":
                    up = request.files.get("file")
                    data = up.read(2 * 1024 * 1024 + 1) if up else b""
                    if not data:
                        raise TaskError("Choose an .ics file.")
                    if len(data) > 2 * 1024 * 1024:
                        raise TaskError("The calendar is bigger than 2 MB.")
                    r = store.import_ics(g.user["id"], g.ws, data.decode("utf-8", "replace"), request.form.get("project_id"))
                    flash(tr("Calendar imported: {0} new tasks, {1} updated.").replace("{0}", str(r["created"]))
                          .replace("{1}", str(r["updated"])), "success")
                elif a == "follow":
                    store.add_calendar_sub(g.user["id"], g.ws, request.form.get("name"), request.form.get("url"),
                                           request.form.get("project_id"))
                    flash(tr("The calendar is followed. TaskFlow reads it again every 6 hours."), "success")
                elif a == "sync":
                    sid = request.form.get("sub_id", type=int) or 0
                    if any(s["id"] == sid for s in store.calendar_subs(g.user["id"], g.ws)):
                        flash(tr(store.sync_calendar_sub(sid) or ""), "success")
                elif a == "unfollow":
                    store.delete_calendar_sub(g.user["id"], request.form.get("sub_id", type=int) or 0)
                    flash(tr("The calendar is not followed any more."), "success")
            except TaskError as e:
                flash(tr(str(e)), "error")
            return redirect(url_for("calendar_import"))
        return render_template("calendar_import.html", subs=store.calendar_subs(g.user["id"], g.ws),
                               projects=store.projects(g.ws),
                               feed=url_for("ical_feed", token=store.ical_token(g.user["id"]), _external=True)
                               if "ical_feed" in app.view_functions else "")

    # ------------------------------------------------------------ estimates report
    @app.route("/reports/estimates")
    @login_required
    def estimate_report():
        return render_template("estimates.html", acc=store.estimate_accuracy(g.ws))

    # ------------------------------------------------------------ translations by the users
    @app.route("/translate/suggest", methods=["GET", "POST"])
    @login_required
    def translate_suggest():
        lang = request.values.get("lang") or (g.lang if g.lang != "en" else "rw")
        english = request.values.get("english", "")
        current = i18n.translate_text(english, lang) if english else ""
        if request.method == "POST":
            try:
                store.suggest_translation(g.user["id"], lang, english, request.form.get("text"), request.form.get("note", ""))
                flash(tr("Thank you! The site administrator will look at your suggestion."), "success")
                return redirect(url_for("translate_suggest", lang=lang))
            except TaskError as e:
                flash(tr(str(e)), "error")
        return render_template("translate_suggest.html", s_lang=lang, english=english, current=current,
                               langs={k: v for k, v in world.LANGUAGES.items() if k != "en"})

    @app.route("/admin/suggestions", methods=["GET", "POST"])
    @site_admin_required
    def admin_suggestions():
        if request.method == "POST":
            try:
                store.decide_suggestion(g.user["id"], request.form.get("sid", type=int) or 0,
                                        request.form.get("action") == "approve", request.form.get("text") or None)
                flash(tr("Saved."), "success")
            except TaskError as e:
                flash(tr(str(e)), "error")
            return redirect(url_for("admin_suggestions"))
        return render_template("admin/suggestions.html", rows=store.translation_suggestions(), section="suggestions",
                               done=store.translation_suggestions("approved")[:20], LANGUAGES=world.LANGUAGES)

    # ------------------------------------------------------------ system page and outbox retry
    @app.route("/admin/system")
    @site_admin_required
    def admin_system():
        return render_template("admin/system.html", section="system", health=store.health(),
                               storage=store.storage_summary(), sso=oidc.enabled(env), db=store.describe_database(),
                               live="PostgreSQL LISTEN / NOTIFY" if store.is_postgres else "one process (SQLite)",
                               email=store.notifier.email_provider() if store.notifier else None,
                               jobs=app.config["JOBS"])

    @app.route("/admin/outbox/<int:oid>/retry", methods=["POST"])
    @site_admin_required
    def admin_outbox_retry(oid):
        flash(tr("Result:") + " " + tr(store.retry_outbox_now(g.user["id"], oid) or "not found"), "success")
        return redirect(url_for("admin_outbox"))

    # ------------------------------------------------------------ personal settings
    @app.route("/settings/theme", methods=["POST"])
    @login_required
    def set_theme():
        try:
            store.set_theme(g.user["id"], request.form.get("theme", "auto"))
        except TaskError as e:
            return jsonify(ok=False, error=tr(str(e))), 400
        if request.headers.get("X-CSRF-Token"):
            return jsonify(ok=True)
        return redirect(request.referrer if (request.referrer or "").startswith(request.host_url) else url_for("profile"))

    @app.route("/settings/digest", methods=["POST"])
    @login_required
    def set_digest():
        on = request.form.get("digest") == "1"
        store.set_digest(g.user["id"], on)
        flash(tr("You will get a short email every Monday with your tasks of the week.") if on
              else tr("The weekly email is off."), "success")
        return redirect(url_for("profile") + "#notifications")

    @app.route("/install")
    def install():
        return render_template("install.html")

    @app.route("/offline")
    def offline():
        return render_template("offline.html")

    @app.route("/healthz")
    def healthz():
        try:
            return jsonify(ok=True, version=app.config.get("VERSION_TEXT", ""), **store.health())
        except Exception:
            return jsonify(ok=False, database="not answering"), 503

    # ------------------------------------------------------------ single sign-on
    def sso_redirect_uri(name):
        return base_url().rstrip("/") + url_for("sso_callback", provider=name)

    @app.route("/auth/sso/<provider>")
    def sso_start(provider):
        cfg = oidc.config(env, provider)
        if not cfg:
            abort(404)
        url, saved = oidc.start(cfg, sso_redirect_uri(provider))
        session["sso"] = saved
        session["sso_next"] = request.args.get("next", "")
        return redirect(url)

    @app.route("/auth/sso/<provider>/callback")
    def sso_callback(provider):
        cfg = oidc.config(env, provider)
        if not cfg:
            abort(404)
        saved = session.pop("sso", None)
        try:
            who = oidc.finish(cfg, saved, request.args, sso_redirect_uri(provider), http=store.http)
            u = store.sso_user(provider, who["sub"], who["email"], who["name"])
        except (oidc.SSOError, TaskError) as e:
            flash(tr(str(e)), "error")
            return redirect(url_for("login"))
        except Exception:
            flash(tr("Sign-in with {0} did not work. Please try again.").replace("{0}", cfg["name"]), "error")
            return redirect(url_for("login"))
        nxt = session.pop("sso_next", "")
        resp = app.finish_login(u, False)
        if nxt.startswith("/") and not nxt.startswith("//") and not u["totp_enabled"]:
            return redirect(nxt)
        return resp

    # ------------------------------------------------------------ REST API v1
    hits = defaultdict(deque)

    def api_error(code, text):
        return jsonify(ok=False, error=text), code

    def api_task(t):
        keys = ("id", "title", "description", "status", "priority", "assignee_id", "assignee_name", "project_id",
                "project_name", "due_date", "start_date", "estimate_minutes", "created_by", "created_at", "updated_at",
                "completed_at")
        out = {k: t.get(k) for k in keys}
        out["labels"] = [lb["name"] for lb in store.task_labels([t["id"]])[t["id"]]]
        names = {f["id"]: f["name"] for f in store.fields(g.ws)}
        out["fields"] = {names[k]: v for k, v in store.field_values([t["id"]])[t["id"]].items() if k in names}
        return out

    @app.before_request
    def api_sign_in():
        if not request.path.startswith("/api/v1/"):
            return None
        head = request.headers.get("Authorization", "")
        found = store.api_auth(head[7:].strip()) if head.startswith("Bearer ") else None
        if not found:
            return api_error(401, "Send a valid key: Authorization: Bearer tf_...")
        user, ws, scope = found
        q = hits[user["id"]]
        nowt = time.time()
        while q and nowt - q[0] > 60:
            q.popleft()
        if len(q) >= API_PER_MINUTE:
            resp = api_error(429, f"Too many requests: at most {API_PER_MINUTE} per minute.")
            resp[0].headers["Retry-After"] = "60"
            return resp
        q.append(nowt)
        if request.method not in ("GET", "HEAD") and scope != "write":
            return api_error(403, "This key can only read.")
        g.user, g.ws, g.api_scope = user, ws, scope
        g.workspace = store.workspace(ws)
        return None

    @app.route("/api/v1/me")
    def api_me():
        return jsonify(ok=True, id=g.user["id"], name=g.user["name"], email=g.user["email"], role=g.user["role"],
                       workspace={"id": g.ws, "name": g.workspace["name"]}, scope=g.api_scope)

    @app.route("/api/v1/projects")
    def api_projects():
        return jsonify(ok=True, projects=[{"id": p["id"], "name": p["name"], "description": p["description"],
                                           "archived": bool(p.get("archived"))} for p in store.projects(g.ws)])

    @app.route("/api/v1/tasks", methods=["GET", "POST"])
    def api_tasks():
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            fields = {k: str(data[k]) if data.get(k) is not None else "" for k in
                      ("title", "description", "status", "priority", "assignee_id", "project_id", "due_date", "estimate")}
            try:
                tid = store.create_task(g.user["id"], g.ws, fields)
            except TaskError as e:
                return api_error(400, str(e))
            return jsonify(ok=True, task=api_task(store.task(tid, ws=g.ws))), 201
        a = request.args
        limit = max(1, min(200, a.get("limit", 50, type=int)))
        tasks = store.tasks(g.ws, q=a.get("q", ""), status=a.get("status", ""), priority=a.get("priority", ""),
                            assignee=a.get("assignee", ""), project=a.get("project", ""), sort=a.get("sort", "due"))
        return jsonify(ok=True, count=len(tasks), tasks=[api_task(t) for t in tasks[:limit]])

    @app.route("/api/v1/tasks/<int:task_id>", methods=["GET", "PATCH", "DELETE"])
    def api_task_one(task_id):
        t = store.task(task_id, ws=g.ws)
        if not t:
            return api_error(404, "Task not found.")
        if request.method == "GET":
            return jsonify(ok=True, task=api_task(t))
        if request.method == "DELETE":
            if not store.can_delete(g.user, t):
                return api_error(403, "You cannot delete this task.")
            store.delete_task(g.user["id"], g.ws, task_id)
            return jsonify(ok=True, deleted=task_id)
        if not store.can_edit(g.user, t):
            return api_error(403, "You can only change tasks you created or that are assigned to you.")
        data = request.get_json(silent=True) or {}
        fields = {k: "" if t[k] is None else str(t[k]) for k in
                  ("title", "description", "status", "priority", "assignee_id", "project_id", "due_date", "start_date",
                   "sprint_id", "repeat")}
        fields["estimate"] = fmt_minutes(t["estimate_minutes"], "en") if t["estimate_minutes"] else ""
        for k in ("title", "description", "status", "priority", "assignee_id", "project_id", "due_date", "estimate"):
            if k in data:
                fields[k] = "" if data[k] is None else str(data[k])
        try:
            if set(data) == {"status"}:
                store.set_status(g.user["id"], g.ws, task_id, fields["status"])
            else:
                store.update_task(g.user["id"], g.ws, task_id, fields)
        except TaskError as e:
            return api_error(400, str(e))
        return jsonify(ok=True, task=api_task(store.task(task_id, ws=g.ws)))

    @app.route("/api/v1/tasks/<int:task_id>/comments", methods=["POST"])
    def api_comment(task_id):
        if not store.task(task_id, ws=g.ws):
            return api_error(404, "Task not found.")
        body = ((request.get_json(silent=True) or {}).get("body") or "").strip()
        try:
            store.add_comment(g.user["id"], g.ws, task_id, body)
        except TaskError as e:
            return api_error(400, str(e))
        return jsonify(ok=True), 201

    app.config["VERSION_TEXT"] = "4.0"
