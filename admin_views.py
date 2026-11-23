"""
admin_views.py - the back office of the site administrator in TaskFlow 3.

    /admin                     overview, services, data viewer, maintenance (version 2, still here)
    /admin/settings            the 17 site settings and the logo
    /admin/users               every account: add, open, change everything, delete
    /admin/users/<id>          one account
    /admin/workspaces          every workspace; /admin/workspaces/<id> one workspace and its members
    /admin/resumes             every resume
    /admin/translations        the words of the interface in 6 languages
    /admin/outbox              emails, Slack, Telegram and push messages the site wrote
    /admin/audit               who did what
    /admin/restore             restore a backup file

The site administrator manages his own details on the normal profile page (/profile) - photo, name, email,
phone, job, bio, language, password, two-factor login, devices - and can also open his account here.
"""

import i18n
import world
from flask import Response, abort, flash, g, redirect, render_template, request, session, url_for

from admin_store import SITE_SETTINGS, picture_to_b64
from core import TaskError
from resumes import VISIBILITY


def register(app, store, site_admin_required, base_url, env):

    def tr(text):
        return i18n.translate_text(text)

    def done(text, endpoint, **kw):
        flash(text, "success")
        return redirect(url_for(endpoint, **kw))

    # ------------------------------------------------------------ settings
    @app.route("/admin/site", methods=["GET", "POST"])
    @site_admin_required
    def admin_site():
        if request.method == "POST":
            f = request.form
            values = {}
            for key, (_, _, typ, _, _) in SITE_SETTINGS.items():
                if typ == "image":
                    continue
                if typ == "langs":
                    values[key] = ",".join(f.getlist(key))
                elif typ == "bool":
                    values[key] = f.get(key, "")
                elif key in f:
                    values[key] = f.get(key)
            try:
                up = request.files.get("logo")
                if up and up.filename:
                    values["logo"] = picture_to_b64(up.read(), size=128, square=False)
                elif f.get("logo_off"):
                    values["logo"] = ""
                n = store.save_site(g.user["id"], values)
            except TaskError as e:
                flash(str(e), "error")
                return redirect(url_for("admin_site"))
            return done(tr("{0} setting(s) saved.").replace("{0}", str(n)), "admin_site")
        groups = {}
        for key, row in SITE_SETTINGS.items():
            groups.setdefault(row[0], []).append((key,) + row[1:])
        return render_template("admin/site.html", groups=groups, V=store.site(), section="site")

    # ------------------------------------------------------------ accounts
    @app.route("/admin/users", methods=["GET", "POST"])
    @site_admin_required
    def admin_users():
        if request.method == "POST":
            try:
                uid = store.admin_create_user(g.user["id"], request.form)
                return done(tr("Account made for {0}.").replace("{0}", request.form.get("name", "")), "admin_user",
                            user_id=uid)
            except TaskError as e:
                flash(str(e), "error")
        q = request.args.get("q", "")
        return render_template("admin/users.html", people=store.users_admin(q), q=q, section="users",
                               workspaces=store.workspaces_admin())

    @app.route("/admin/users/<int:user_id>", methods=["GET", "POST"])
    @site_admin_required
    def admin_user(user_id):
        u = store.user(user_id)
        if not u:
            abort(404)
        if request.method == "POST":
            f = request.form
            a = f.get("action")
            me = g.user["id"]
            try:
                if a == "details":
                    store.save_person(me, user_id, f, own=False)
                    flash("Account updated.", "success")
                elif a == "photo":
                    up = request.files.get("photo")
                    store.set_photo(me, user_id, up.read() if up and up.filename else b"")
                    flash("Photo saved." if up and up.filename else "Photo removed.", "success")
                elif a == "password":
                    if f.get("password") != f.get("password2"):
                        raise TaskError("New passwords do not match.")
                    store.admin_reset_password(me, user_id, f.get("password"))
                    flash("New password set. The person is signed out everywhere.", "success")
                elif a == "reset_link":
                    store.admin_send_reset(me, user_id, base_url())
                    flash("A link to choose a new password was emailed.", "success")
                elif a == "no_2fa":
                    store.reset_2fa(me, user_id)
                    flash("Two-factor login is off for this account.", "success")
                elif a == "sign_out":
                    store.bump_session(user_id)
                    with store.db() as con:
                        store.log(con, me, "signed_out_everywhere", u["name"])
                    if user_id == me:
                        session.clear()
                        return redirect(url_for("login"))
                    flash("The person is signed out on every device.", "success")
                elif a == "toggle":
                    store.admin_toggle(me, user_id)
                    flash("Account updated.", "success")
                elif a == "site_admin":
                    store.set_site_admin(me, user_id, f.get("on") == "1")
                    flash("Account updated.", "success")
                elif a == "add_ws":
                    store.add_member(int(f.get("workspace", 0) or 0), user_id, f.get("role") or "member", by=me)
                    flash("Added to the workspace.", "success")
                elif a == "ws_role":
                    store.site_set_role(me, int(f.get("workspace", 0) or 0), user_id, f.get("role"))
                    flash("Role changed.", "success")
                elif a == "remove_ws":
                    store.remove_member(me, int(f.get("workspace", 0) or 0), user_id)
                    flash("Removed from the workspace.", "success")
                elif a == "delete":
                    if (f.get("confirm") or "").strip().lower() != u["email"]:
                        raise TaskError("Type the email of the account to delete it.")
                    store.admin_delete_person(me, user_id)
                    return done(tr("{0}'s account was deleted. Their tasks are now unassigned.").replace("{0}", u["name"]),
                                "admin_users")
                else:
                    abort(400)
            except TaskError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_user", user_id=user_id))
        return render_template("admin/user.html", u=u, section="users", ws_of=store.memberships(user_id),
                               all_ws=store.workspaces_admin(),
                               resumes=[r for r in store.resumes_admin() if r["owner_id"] == user_id],
                               languages=world.LANGUAGES, push_count=store.push_count(user_id), VIS=VISIBILITY)

    @app.route("/admin/users/<int:user_id>/photo.jpg")
    @app.login_required
    def user_photo(user_id):
        u = store.user(user_id)
        if not u or not u.get("photo"):
            abort(404)
        import base64
        resp = Response(base64.b64decode(u["photo"]), mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "private, max-age=300"
        return resp

    @app.route("/admin/users/<int:user_id>/data.json")
    @site_admin_required
    def admin_user_data(user_id):
        if not store.user(user_id):
            abort(404)
        return Response(store.my_data(user_id), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=taskflow_user_{user_id}.json"})

    @app.route("/profile/data.json")
    @app.login_required
    def my_data():
        return Response(store.my_data(g.user["id"]), mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=taskflow_my_data.json"})

    # ------------------------------------------------------------ workspaces
    @app.route("/admin/workspaces", methods=["GET", "POST"])
    @site_admin_required
    def admin_workspaces():
        if request.method == "POST":
            try:
                ws = store.create_workspace(g.user["id"], request.form.get("name"), request.form.get("description"),
                                            request.form.get("color"))
                return done("Workspace created.", "admin_workspace", ws=ws)
            except TaskError as e:
                flash(str(e), "error")
        return render_template("admin/workspaces.html", rows=store.workspaces_admin(), section="workspaces")

    @app.route("/admin/workspaces/<int:ws>", methods=["GET", "POST"])
    @site_admin_required
    def admin_workspace(ws):
        w = store.workspace(ws)
        if not w:
            abort(404)
        if request.method == "POST":
            f = request.form
            a = f.get("action")
            me = g.user["id"]
            try:
                if a == "details":
                    store.save_workspace(me, ws, f.get("name"), f.get("description"), f.get("color"),
                                         f.get("default_priority"))
                    flash("Settings saved.", "success")
                elif a == "add":
                    store.site_add_member(me, ws, f.get("email"), f.get("role") or "member")
                    flash("Member added.", "success")
                elif a == "role":
                    store.site_set_role(me, ws, int(f.get("user", 0) or 0), f.get("role"))
                    flash("Role changed.", "success")
                elif a == "remove":
                    store.remove_member(me, ws, int(f.get("user", 0) or 0))
                    flash("Member removed from the workspace. Their tasks here are now unassigned.", "success")
                elif a == "invite":
                    store.new_invite_code(me, ws)
                    flash("A new invite link was made. The old link does not work any more.", "success")
                elif a == "open":
                    session["ws"] = ws
                    return done("You opened this workspace as site administrator.", "dashboard")
                elif a == "delete":
                    store.admin_delete_workspace(me, ws, f.get("confirm"))
                    if session.get("ws") == ws:
                        session.pop("ws", None)
                    return done("Workspace deleted.", "admin_workspaces")
                else:
                    abort(400)
            except TaskError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_workspace", ws=ws))
        row = next(x for x in store.workspaces_admin() if x["id"] == ws)
        return render_template("admin/workspace.html", w=w, row=row, members=store.users(ws), section="workspaces",
                               invite_link=base_url() + "join/" + w["invite_code"], projects=store.projects(ws, True),
                               sprints=store.sprints(ws), labels=store.labels(ws))

    @app.route("/admin/workspaces/<int:ws>/export.json")
    @site_admin_required
    def admin_workspace_export(ws):
        if not store.workspace(ws):
            abort(404)
        return Response(store.export_workspace(ws), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=taskflow_workspace_{ws}.json"})

    # ------------------------------------------------------------ resumes
    @app.route("/admin/resumes", methods=["GET", "POST"])
    @site_admin_required
    def admin_resumes():
        if request.method == "POST":
            f = request.form
            rid = int(f.get("id", 0) or 0)
            try:
                if f.get("action") == "delete":
                    store.admin_delete_resume(g.user["id"], rid)
                    flash("Resume deleted.", "success")
                else:
                    store.admin_set_resume_visibility(g.user["id"], rid, f.get("visibility"))
                    flash("Resume updated.", "success")
            except TaskError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_resumes"))
        return render_template("admin/resumes.html", rows=store.resumes_admin(), VISIBILITY=VISIBILITY,
                               section="resumes")

    # ------------------------------------------------------------ translations
    @app.route("/admin/translations", methods=["GET", "POST"])
    @site_admin_required
    def admin_translations():
        lang = request.values.get("lang", "ru")
        if lang not in world.LANGUAGES or lang == "en":
            lang = "ru"
        if request.method == "POST":
            try:
                store.save_ui_override(g.user["id"], lang, request.form.get("english"), request.form.get("text"))
                flash("Translation saved. Everybody sees it now.", "success")
            except TaskError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_translations", lang=lang, q=request.form.get("q", ""),
                                    only=request.form.get("only", "")))
        q = " ".join(request.args.get("q", "").split()).lower()[:80]
        only = request.args.get("only", "")
        exact, patterns = i18n.catalog(lang)
        built_in = dict(exact)
        import json
        import os
        path = os.path.join(i18n.CATALOG_DIR, f"{lang}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                built_in.update(json.load(fh))
        mine = store.ui_overrides(lang)
        rows = []
        for en in sorted(set(built_in) | set(mine), key=str.lower):
            if only == "changed" and en not in mine:
                continue
            if q and q not in en.lower() and q not in built_in.get(en, "").lower() and q not in mine.get(en, "").lower():
                continue
            rows.append((en, built_in.get(en, ""), mine.get(en, "")))
            if len(rows) >= 60:
                break
        return render_template("admin/translations.html", tr_lang=lang, q=q, only=only, rows=rows, total=len(built_in),
                               changed=len(mine), languages=world.LANGUAGES, section="translations")

    # ------------------------------------------------------------ outbox
    @app.route("/admin/outbox", methods=["GET", "POST"])
    @site_admin_required
    def admin_outbox():
        if request.method == "POST":
            try:
                status = store.send_test_email(g.user["id"], request.form.get("to"))
                flash(tr("Test email: {0}").replace("{0}", tr(status)), "success" if status == "sent" else "info")
            except TaskError as e:
                flash(str(e), "error")
            return redirect(url_for("admin_outbox"))
        channel = request.args.get("channel", "")
        return render_template("admin/outbox.html", rows=store.outbox(200, channel), channel=channel,
                               provider=store.notifier.email_provider() if store.notifier else None,
                               telegram=bool(env.get("TELEGRAM_BOT_TOKEN")), section="outbox")

    # ------------------------------------------------------------ audit log
    @app.route("/admin/audit")
    @site_admin_required
    def admin_audit():
        q = request.args.get("q", "")
        return render_template("admin/audit.html", rows=store.audit(q), q=q, section="audit")

    # ------------------------------------------------------------ restore a backup
    @app.route("/admin/restore", methods=["POST"])
    @site_admin_required
    def admin_restore():
        up = request.files.get("file")
        if request.form.get("confirm") != "RESTORE":
            flash("Type RESTORE to confirm.", "error")
            return redirect(url_for("admin_panel", tab="maintenance"))
        if not up or not up.filename:
            flash("Choose a backup file.", "error")
            return redirect(url_for("admin_panel", tab="maintenance"))
        try:
            n = store.restore_backup(g.user["id"], up.read(20 * 1024 * 1024 + 1))
        except TaskError as e:
            flash(str(e), "error")
            return redirect(url_for("admin_panel", tab="maintenance"))
        session.clear()
        flash(tr("The backup was restored ({0} rows). Please sign in again.").replace("{0}", str(n)), "success")
        return redirect(url_for("login"))
