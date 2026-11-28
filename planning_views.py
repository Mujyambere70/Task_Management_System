"""
planning_views.py - pages of TaskFlow 3 for the members of a workspace.

    /sprints, /sprints/<id>        sprints, sprint burndown, velocity
    /gantt                         Gantt chart with dependencies
    /labels                        labels of the workspace
    /search?q=                     one search box for everything
    /trash                         deleted tasks: restore or delete for good
    /tasks/<id>/deps               "waits for" links
    /api/estimate                  suggested estimate for a title
    /ical/<token>.ics              private calendar feed
    /push/...                      Web Push subscription; /manifest.webmanifest and /sw.js make the site installable
    /workspace/export.json, /workspace/import   all data of a workspace out, tasks from CSV in
    /resumes/<id>/resume.pdf       the resume as a PDF made on the server
    /brand/logo.png                the logo chosen in the site settings
"""

import base64
import io
from datetime import date, timedelta

from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for

import i18n
from core import TaskError, fmt_minutes
from planning import REPEATS, SPRINT_STATES

BASE_LOGO = None


def register(app, store, login_required, admin_required, base_url, env):

    def tr(text):
        return i18n.translate_text(text)

    # ------------------------------------------------------------ sprints
    @app.route("/sprints", methods=["GET", "POST"])
    @login_required
    def sprints():
        if request.method == "POST":
            if g.user["role"] != "admin":
                abort(403)
            try:
                sid = store.save_sprint(g.user["id"], g.ws, request.form)
                flash("Sprint saved.", "success")
                return redirect(url_for("sprint_page", sprint_id=sid))
            except TaskError as e:
                flash(str(e), "error")
        all_s = store.sprints(g.ws)
        today = date.today()
        nxt = today + timedelta(days=13)                       # a two-week sprint by default
        return render_template("sprints.html", sprints=all_s, velocity=store.velocity(g.ws), STATES=SPRINT_STATES,
                               new_start=today.isoformat(), new_end=nxt.isoformat())

    @app.route("/sprints/<int:sprint_id>", methods=["GET", "POST"])
    @login_required
    def sprint_page(sprint_id):
        s = store.sprint(sprint_id, g.ws)
        if not s:
            abort(404)
        if request.method == "POST":
            if g.user["role"] != "admin":
                abort(403)
            f = request.form
            try:
                a = f.get("action")
                if a in SPRINT_STATES:
                    store.set_sprint_state(g.user["id"], g.ws, sprint_id, a, f.get("move_to"))
                    flash("Sprint updated.", "success")
                elif a == "delete":
                    store.delete_sprint(g.user["id"], g.ws, sprint_id)
                    flash("Sprint deleted. Its tasks are kept.", "success")
                    return redirect(url_for("sprints"))
                elif a == "add_tasks":
                    ids = [int(x) for x in f.getlist("task") if x.isdigit()]
                    with store.db() as con:
                        for tid in ids:
                            con.execute("UPDATE tasks SET sprint_id = ? WHERE id = ? AND workspace_id = ?",
                                        (sprint_id, tid, g.ws))
                    flash(tr("{0} tasks added to the sprint.").replace("{0}", str(len(ids))), "success")
                elif a == "remove_task":
                    with store.db() as con:
                        con.execute("UPDATE tasks SET sprint_id = NULL WHERE id = ? AND workspace_id = ? AND sprint_id = ?",
                                    (int(f.get("task", 0) or 0), g.ws, sprint_id))
                else:
                    store.save_sprint(g.user["id"], g.ws, f, sprint_id)
                    flash("Sprint saved.", "success")
            except TaskError as e:
                flash(str(e), "error")
            return redirect(url_for("sprint_page", sprint_id=sprint_id))
        metric = request.args.get("metric") if request.args.get("metric") in ("tasks", "hours") else "tasks"
        bd = store.sprint_burndown(g.ws, sprint_id, metric)
        tasks = store.sprint_tasks(g.ws, sprint_id)
        backlog = [t for t in store.tasks(g.ws, status="open", sort="priority") if not t["sprint_id"]]
        return render_template("sprint.html", s=s, tasks=tasks, bd=bd, svg=store.chart_svg_points(bd), metric=metric,
                               backlog=backlog, others=[x for x in store.sprints(g.ws)
                                                        if x["id"] != sprint_id and x["status"] != "closed"],
                               STATES=SPRINT_STATES, tlabels=store.task_labels([t["id"] for t in tasks]),
                               est=sum(t["estimate_minutes"] or 0 for t in tasks),
                               logged=sum(t["logged_minutes"] for t in tasks))

    # ------------------------------------------------------------ Gantt chart
    @app.route("/gantt")
    @login_required
    def gantt():
        a = request.args
        project = a.get("project", type=int)
        if project and not store.project(project, ws=g.ws):
            project = None
        start = a.get("start") or None
        try:
            if start:
                date.fromisoformat(start)
        except ValueError:
            start = None
        days = a.get("days", type=int) or 42
        gd = store.gantt(g.ws, project, days, start)
        return render_template("gantt.html", gd=gd, svg=store.gantt_svg(gd), projects=store.projects(g.ws),
                               project=project, days=gd["days"], start=gd["start"])

    # ------------------------------------------------------------ labels
    @app.route("/labels", methods=["GET", "POST"])
    @login_required
    def labels():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "delete":
                    if g.user["role"] != "admin":
                        abort(403)
                    store.delete_label(g.user["id"], g.ws, int(f.get("id", 0) or 0))
                    flash("Label deleted.", "success")
                else:
                    lid = int(f["id"]) if f.get("id", "").isdigit() else None
                    if lid and g.user["role"] != "admin":
                        abort(403)
                    store.save_label(g.user["id"], g.ws, f.get("name"), f.get("color"), lid)
                    flash("Label saved.", "success")
            except TaskError as e:
                flash(str(e), "error")
            return redirect(url_for("labels"))
        return render_template("labels.html", labels=store.labels(g.ws))

    # ------------------------------------------------------------ search
    @app.route("/search")
    @login_required
    def search():
        res = store.search(g.ws, request.args.get("q", ""))
        return render_template("search.html", res=res, q=request.args.get("q", ""),
                               tlabels=store.task_labels([t["id"] for t in res["tasks"]]) if res else {})

    # ------------------------------------------------------------ trash
    @app.route("/trash", methods=["GET", "POST"])
    @login_required
    def trash():
        if request.method == "POST":
            f = request.form
            tid = int(f.get("id", 0) or 0)
            try:
                if f.get("action") == "restore":
                    store.restore_task(g.user, g.ws, tid)
                    flash("Task restored.", "success")
                    return redirect(url_for("task_detail", task_id=tid))
                if f.get("action") == "purge":
                    store.purge_task(g.user, g.ws, tid)
                    flash("Task deleted for good.", "success")
            except TaskError as e:
                flash(str(e), "error")
            return redirect(url_for("trash"))
        return render_template("trash.html", rows=store.trash(g.ws), days=store.site_int("trash_days"))

    # ------------------------------------------------------------ dependencies
    @app.route("/tasks/<int:task_id>/deps", methods=["POST"])
    @login_required
    def task_deps(task_id):
        t = store.task(task_id, ws=g.ws)
        if not t:
            abort(404)
        if not store.can_edit(g.user, t):
            abort(403)
        f = request.form
        try:
            if f.get("action") == "remove":
                store.remove_dependency(g.user["id"], g.ws, task_id, int(f.get("blocked_by", 0) or 0))
            else:
                store.add_dependency(g.user["id"], g.ws, task_id, f.get("blocked_by"))
                flash("Saved: this task waits for the other one.", "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(url_for("task_detail", task_id=task_id) + "#deps")

    # ------------------------------------------------------------ suggested estimate
    @app.route("/api/estimate")
    @login_required
    def api_estimate():
        a = request.args
        s = store.suggest_estimate(g.ws, a.get("title", ""), a.get("project", type=int), exclude=a.get("task", type=int),
                                   assignee_id=a.get("assignee", type=int), priority=a.get("priority") or None,
                                   labels=a.getlist("label"))
        if not s:
            return jsonify(ok=False, text=tr("No similar finished tasks yet."))
        return jsonify(ok=True, minutes=s["minutes"], value=fmt_minutes(s["minutes"], "en"),
                       text=tr("About {0}, from {1} similar finished tasks.").replace("{0}", fmt_minutes(s["minutes"]))
                       .replace("{1}", str(s["count"])), examples=s["examples"])

    @app.route("/tasks/<int:task_id>/estimate", methods=["POST"])
    @login_required
    def task_use_estimate(task_id):
        t = store.task(task_id, ws=g.ws)
        if not t:
            abort(404)
        if not store.can_edit(g.user, t):
            abort(403)
        s = store.suggest_estimate(g.ws, t["title"], t["project_id"], exclude=task_id, assignee_id=t["assignee_id"],
                                   priority=t["priority"], labels=[lb["id"] for lb in store.task_labels([task_id])[task_id]])
        if s:
            with store.db() as con:
                con.execute("UPDATE tasks SET estimate_minutes = ? WHERE id = ?", (s["minutes"], task_id))
                store.log(con, g.user["id"], "task_updated", "estimate (suggested)", task_id, ws=g.ws)
            flash(tr("Estimate set to {0}.").replace("{0}", fmt_minutes(s["minutes"])), "success")
        return redirect(url_for("task_detail", task_id=task_id))

    # ------------------------------------------------------------ iCal feed (no login: the secret link is the key)
    @app.route("/ical/<token>.ics")
    def ical_feed(token):
        u = store.user_by_ical(token)
        if not u:
            abort(404)
        body = store.ical(u, base_url())
        return Response(body, mimetype="text/calendar", headers={
            "Content-Disposition": "inline; filename=taskflow.ics", "Cache-Control": "private, max-age=600"})

    # ------------------------------------------------------------ Web Push and the installable app
    @app.route("/push/key")
    @login_required
    def push_key():
        return jsonify(key=store.vapid()["public"])

    @app.route("/push/subscribe", methods=["POST"])
    @login_required
    def push_subscribe():
        try:
            store.save_push_sub(g.user["id"], request.get_json(silent=True) or {})
        except TaskError as e:
            return jsonify(ok=False, error=tr(str(e))), 400
        return jsonify(ok=True)

    @app.route("/push/unsubscribe", methods=["POST"])
    @login_required
    def push_unsubscribe():
        data = request.get_json(silent=True) or {}
        store.delete_push_subs(g.user["id"], data.get("endpoint"))
        return jsonify(ok=True)

    @app.route("/push/test", methods=["POST"])
    @login_required
    def push_test():
        with store.db() as con:
            store.queue_push(con, g.user["id"], store.site("site_name"), tr("Notifications work on this device."), "/")
        flash("A test notification was sent to your devices.", "success")
        return redirect(url_for("profile") + "#push")

    @app.route("/manifest.webmanifest")
    def manifest():
        name = store.site("site_name")
        data = {"name": name, "short_name": name[:12], "start_url": "/", "scope": "/", "display": "standalone",
                "background_color": "#f5f6fa", "theme_color": store.site("accent"),
                "icons": [{"src": url_for("brand_logo"), "sizes": "128x128", "type": "image/png"},
                          {"src": url_for("static", filename="img/icon-192.png"), "sizes": "192x192", "type": "image/png"},
                          {"src": url_for("static", filename="img/icon-512.png"), "sizes": "512x512", "type": "image/png"}]}
        return jsonify(data), 200, {"Content-Type": "application/manifest+json"}

    @app.route("/sw.js")
    def service_worker():
        resp = send_file(app.static_folder + "/sw.js", mimetype="text/javascript")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.route("/brand/logo.png")
    def brand_logo():
        logo = store.site("logo")
        if logo:
            resp = send_file(io.BytesIO(base64.b64decode(logo)), mimetype="image/png")
        else:
            resp = send_file(app.static_folder + "/img/logo.png", mimetype="image/png")
        resp.headers["Cache-Control"] = "public, max-age=300"
        return resp

    # ------------------------------------------------------------ workspace data out and in
    @app.route("/workspace/export.json")
    @admin_required
    def workspace_export():
        body = store.export_workspace(g.ws)
        return Response(body, mimetype="application/json", headers={
            "Content-Disposition": f"attachment; filename=taskflow_workspace_{g.ws}_{date.today()}.json"})

    @app.route("/workspace/import", methods=["POST"])
    @admin_required
    def workspace_import():
        up = request.files.get("file")
        if not up or not up.filename:
            flash("Choose a CSV file.", "error")
            return redirect(url_for("workspace_settings") + "#import")
        try:
            text = up.read(2 * 1024 * 1024).decode("utf-8-sig")
        except UnicodeDecodeError:
            flash("The file must be a CSV saved as UTF-8.", "error")
            return redirect(url_for("workspace_settings") + "#import")
        try:
            made, errors = store.import_tasks_csv(g.user["id"], g.ws, text)
            flash(tr("{0} tasks imported.").replace("{0}", str(made)), "success" if made else "info")
            for e in errors[:5]:
                flash(e, "error")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(url_for("workspace_settings") + "#import")

    # ------------------------------------------------------------ resume as a server-made PDF
    @app.route("/r/<slug>/resume.pdf")
    def resume_pdf(slug):
        import resume_pdf as rp
        r = store.resume_by_slug(slug)
        if not r or not (r["visibility"] in ("link", "public") or
                         (g.user and (g.user["id"] == r["user_id"] or g.user["site_admin"]))):
            abort(404)
        photo = store.resume_photo(r["id"]) if r["has_photo"] else None
        pdf = rp.make_pdf(r, photo, tr=lambda s: i18n.translate_text(s, r["lang"]))
        name = (r["slug"] or "resume") + ".pdf"
        return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f"inline; filename={name}"})

    app.jinja_env.globals.update(REPEATS=REPEATS, SPRINT_STATES=SPRINT_STATES)
