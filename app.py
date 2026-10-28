"""
TaskFlow - team task management web app (Flask + SQLite).

Run:   pip install -r requirements.txt
       python app.py            -> http://127.0.0.1:5000
Demo:  admin@taskflow.demo / Admin@1234   (more users in README)
"""

import calendar
import csv
import io
import os
import secrets
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)

from store import (COLORS, PRIORITIES, PRIORITY_NAMES, STATUS_NAMES, STATUSES, Store, TaskError)

BASE = os.path.dirname(os.path.abspath(__file__))


def create_app(db_path=None, seed=True, testing=False):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("TASKFLOW_SECRET") or secrets.token_hex(32),
        TESTING=testing,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    store = Store(db_path or os.path.join(BASE, "taskflow.db"))
    app.store = store
    if seed and store.count_users() == 0:
        from demo_data import load_demo
        load_demo(store)

    # ------------------------------------------------------------ helpers
    def login_required(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if not g.user:
                return redirect(url_for("login", next=request.path))
            return view(*a, **kw)
        return wrapper

    def admin_required(view):
        @wraps(view)
        @login_required
        def wrapper(*a, **kw):
            if g.user["role"] != "admin":
                abort(403)
            return view(*a, **kw)
        return wrapper

    def get_task_or_404(task_id):
        t = store.task(task_id)
        if not t:
            abort(404)
        return t

    def safe_next(target):
        return target if target and target.startswith("/") and not target.startswith("//") else url_for("dashboard")

    @app.before_request
    def load_user_and_check_csrf():
        g.user = store.user(session["uid"]) if "uid" in session else None
        if g.user and not g.user["active"]:
            session.clear()
            g.user = None
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        if request.method == "POST":
            token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not token or not secrets.compare_digest(token, session["csrf"]):
                abort(400, "Your session expired. Please reload the page and try again.")

    @app.context_processor
    def globals_for_templates():
        return dict(user=g.get("user"), csrf_token=session.get("csrf", ""), STATUSES=STATUSES, PRIORITIES=PRIORITIES,
                    STATUS_NAMES=STATUS_NAMES, PRIORITY_NAMES=PRIORITY_NAMES, COLORS=COLORS,
                    today=date.today(), now_hour=datetime.now().hour, can_edit=lambda t: g.user and store.can_edit(g.user, t),
                    can_delete=lambda t: g.user and store.can_delete(g.user, t),
                    can_manage=lambda p: g.user and store.can_manage_project(g.user, p),
                    nav_projects=store.projects() if g.get("user") else [], settings=store.settings())

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
            return "Today"
        if diff == 1:
            return "Tomorrow"
        if diff == -1:
            return "Yesterday"
        return d.strftime("%b %d") if d.year == date.today().year else d.strftime("%b %d, %Y")

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
        delta = datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        s = int(delta.total_seconds())
        if s < 60:
            return "just now"
        if s < 3600:
            return f"{s // 60} min ago"
        if s < 86400:
            return f"{s // 3600} h ago"
        if s < 7 * 86400:
            return f"{s // 86400} d ago"
        return ts[:10]

    @app.template_filter("nice_date")
    def nice_date(value):
        if not value:
            return "—"
        return datetime.fromisoformat(value).strftime("%d %b %Y")

    # ------------------------------------------------------------ auth
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            try:
                u = store.authenticate(request.form.get("email"), request.form.get("password"))
            except TaskError as e:
                flash(str(e), "error")
                return render_template("auth/login.html", email=request.form.get("email", "")), 403
            if not u:
                flash("Wrong email or password.", "error")
                return render_template("auth/login.html", email=request.form.get("email", "")), 401
            session.clear()
            session["uid"] = u["id"]
            session["csrf"] = secrets.token_hex(16)
            session.permanent = bool(request.form.get("remember"))
            flash(f"Welcome back, {u['name'].split()[0]}!", "success")
            return redirect(safe_next(request.args.get("next")))
        return render_template("auth/login.html", email="")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(url_for("dashboard"))
        if store.settings()["allow_registration"] != "1" and store.count_users() > 0:
            flash("New sign-ups are turned off. Please ask your admin to add you.", "error")
            return redirect(url_for("login"))
        if request.method == "POST":
            f = request.form
            try:
                if f.get("password") != f.get("confirm"):
                    raise TaskError("Passwords do not match.")
                uid = store.create_user(f.get("name"), f.get("email"), f.get("password"),
                                        job_title=f.get("job_title", ""))
            except TaskError as e:
                flash(str(e), "error")
                return render_template("auth/register.html", form=f), 400
            session.clear()
            session["uid"] = uid
            session["csrf"] = secrets.token_hex(16)
            flash("Your account is ready. Welcome to TaskFlow!", "success")
            return redirect(url_for("dashboard"))
        return render_template("auth/register.html", form={})

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("login"))

    # ------------------------------------------------------------ dashboard
    @app.route("/")
    @login_required
    def dashboard():
        uid = g.user["id"]
        mine = [t for t in store.tasks(assignee=uid, status="open")]
        return render_template(
            "dashboard.html",
            my=store.stats(uid), team=store.stats(),
            overdue=[t for t in mine if t["due_date"] and t["due_date"] < date.today().isoformat()],
            upcoming=[t for t in mine if not t["due_date"] or t["due_date"] >= date.today().isoformat()][:7],
            activity=store.activity(12), chart=store.completed_per_day(14), projects=store.projects())

    # ------------------------------------------------------------ board
    @app.route("/board")
    @login_required
    def board():
        a = request.args
        tasks = store.tasks(q=a.get("q", ""), assignee=a.get("assignee", ""), project=a.get("project", ""),
                            priority=a.get("priority", ""), sort="priority")
        columns = {key: [t for t in tasks if t["status"] == key] for key, _ in STATUSES}
        return render_template("board.html", columns=columns, users=store.users(active_only=True),
                               projects=store.projects(), args=a)

    @app.route("/api/tasks/<int:task_id>/status", methods=["POST"])
    @login_required
    def api_status(task_id):
        t = get_task_or_404(task_id)
        if not store.can_edit(g.user, t):
            return jsonify(ok=False, error="You can only move tasks you created or that are assigned to you."), 403
        data = request.get_json(silent=True) or {}
        try:
            store.set_status(g.user["id"], task_id, data.get("status"))
        except TaskError as e:
            return jsonify(ok=False, error=str(e)), 400
        return jsonify(ok=True, status=data.get("status"), label=STATUS_NAMES[data.get("status")])

    # ------------------------------------------------------------ task list
    @app.route("/tasks")
    @login_required
    def task_list():
        a = request.args
        tasks = store.tasks(q=a.get("q", "").strip(), status=a.get("status", ""), priority=a.get("priority", ""),
                            assignee=a.get("assignee", ""), project=a.get("project", ""), due=a.get("due", ""),
                            sort=a.get("sort", "due"), creator=a.get("creator", ""))
        return render_template("tasks.html", tasks=tasks, users=store.users(), projects=store.projects(), args=a)

    @app.route("/my-tasks")
    @login_required
    def my_tasks():
        return redirect(url_for("task_list", assignee=g.user["id"], status="open"))

    @app.route("/tasks/new", methods=["GET", "POST"])
    @login_required
    def task_new():
        if request.method == "POST":
            try:
                tid = store.create_task(g.user["id"], request.form)
                flash("Task created.", "success")
                return redirect(url_for("task_detail", task_id=tid))
            except TaskError as e:
                flash(str(e), "error")
                form = request.form
        else:
            form = {"status": request.args.get("status", "todo"), "project_id": request.args.get("project", ""),
                    "assignee_id": str(g.user["id"]), "priority": store.settings()["default_priority"]}
        return render_template("task_form.html", form=form, task=None,
                               users=store.users(active_only=True), projects=store.projects())

    @app.route("/tasks/<int:task_id>")
    @login_required
    def task_detail(task_id):
        t = get_task_or_404(task_id)
        return render_template("task_detail.html", t=t, comments=store.comments(task_id),
                               subtasks=store.subtasks(task_id), history=store.activity(30, task_id=task_id))

    # ----- checklist inside a task (anyone who can edit the task)
    def editable_task(task_id):
        t = get_task_or_404(task_id)
        if not store.can_edit(g.user, t):
            abort(403)
        return t

    def back_to_checklist(task_id):
        return redirect(url_for("task_detail", task_id=task_id) + "#checklist")

    @app.route("/tasks/<int:task_id>/checklist", methods=["POST"])
    @login_required
    def checklist_add(task_id):
        editable_task(task_id)
        try:
            store.add_subtask(g.user["id"], task_id, request.form.get("title"))
        except TaskError as e:
            flash(str(e), "error")
        return back_to_checklist(task_id)

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
        return back_to_checklist(sub["task_id"])

    @app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
    @login_required
    def task_edit(task_id):
        t = get_task_or_404(task_id)
        if not store.can_edit(g.user, t):
            abort(403)
        form = {k: ("" if t[k] is None else str(t[k])) for k in
                ("title", "description", "status", "priority", "assignee_id", "project_id", "due_date")}
        if request.method == "POST":
            try:
                store.update_task(g.user["id"], task_id, request.form)
                flash("Task updated.", "success")
                return redirect(url_for("task_detail", task_id=task_id))
            except TaskError as e:
                flash(str(e), "error")
                form = request.form
        return render_template("task_form.html", form=form, task=t,
                               users=store.users(active_only=True), projects=store.projects())

    @app.route("/tasks/<int:task_id>/status", methods=["POST"])
    @login_required
    def task_status(task_id):
        t = get_task_or_404(task_id)
        if not store.can_edit(g.user, t):
            abort(403)
        try:
            store.set_status(g.user["id"], task_id, request.form.get("status"))
            flash(f"Status changed to {STATUS_NAMES[request.form['status']]}.", "success")
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
        store.delete_task(g.user["id"], task_id)
        flash(f"Task “{t['title']}” deleted.", "success")
        return redirect(url_for("task_list"))

    @app.route("/tasks/<int:task_id>/comment", methods=["POST"])
    @login_required
    def task_comment(task_id):
        get_task_or_404(task_id)
        try:
            store.add_comment(g.user["id"], task_id, request.form.get("body"))
        except TaskError as e:
            flash(str(e), "error")
        return redirect(url_for("task_detail", task_id=task_id) + "#comments")

    # ------------------------------------------------------------ calendar
    @app.route("/calendar")
    @login_required
    def calendar_view():
        try:
            month = datetime.strptime(request.args.get("m", ""), "%Y-%m").date()
        except ValueError:
            month = date.today().replace(day=1)
        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)
        tasks = store.tasks_between(weeks[0][0], weeks[-1][-1])
        by_day = {}
        for t in tasks:
            by_day.setdefault(t["due_date"], []).append(t)
        prev_m = (month - timedelta(days=1)).replace(day=1)
        next_m = (month + timedelta(days=32)).replace(day=1)
        return render_template("calendar.html", weeks=weeks, month=month, by_day=by_day,
                               prev_m=prev_m.strftime("%Y-%m"), next_m=next_m.strftime("%Y-%m"))

    # ------------------------------------------------------------ projects
    @app.route("/projects")
    @login_required
    def projects():
        return render_template("projects.html", projects=store.projects(include_archived=True))

    def managed_project(project_id):
        p = store.project(project_id)
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
            store.save_project(g.user["id"], f.get("name"), f.get("description"), f.get("color"), pid)
            flash("Project saved.", "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(url_for("projects"))

    @app.route("/projects/<int:project_id>/archive", methods=["POST"])
    @login_required
    def project_archive(project_id):
        managed_project(project_id)
        store.toggle_archive(g.user["id"], project_id)
        flash("Project updated.", "success")
        return redirect(url_for("projects"))

    @app.route("/projects/<int:project_id>/delete", methods=["POST"])
    @login_required
    def project_delete(project_id):
        managed_project(project_id)
        try:
            store.delete_project(g.user["id"], project_id, delete_tasks=request.form.get("mode") == "tasks")
            flash("Project deleted.", "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(url_for("projects"))

    @app.route("/comments/<int:comment_id>/edit", methods=["POST"])
    @login_required
    def comment_edit(comment_id):
        try:
            task_id = store.edit_comment(g.user, comment_id, request.form.get("body"))
            flash("Comment updated.", "success")
        except PermissionError:
            abort(403)
        except TaskError as e:
            if "not found" in str(e):
                abort(404)
            flash(str(e), "error")
            return redirect(request.referrer or url_for("dashboard"))
        return redirect(url_for("task_detail", task_id=task_id) + "#comments")

    @app.route("/comments/<int:comment_id>/delete", methods=["POST"])
    @login_required
    def comment_delete(comment_id):
        try:
            task_id = store.delete_comment(g.user, comment_id)
        except TaskError:
            abort(404)
        except PermissionError:
            abort(403)
        flash("Comment deleted.", "success")
        return redirect(url_for("task_detail", task_id=task_id) + "#comments")

    @app.route("/tasks/bulk", methods=["POST"])
    @admin_required
    def task_bulk():
        f = request.form
        try:
            n = store.bulk_update(g.user["id"], f.getlist("ids"), f.get("action"), f.get("value"))
            flash(f"{n} task{'s' if n != 1 else ''} updated.", "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(safe_next(f.get("next")))

    # ------------------------------------------------------------ admin panel
    @app.route("/admin")
    @admin_required
    def admin_panel():
        table = request.args.get("table", "tasks")
        if table not in store.TABLES:
            table = "tasks"
        cols, rows, total = store.table_rows(table, limit=200)
        return render_template("admin.html", counts=store.table_counts(), table=table, cols=cols, rows=rows,
                               total=total, tab=request.args.get("tab", "settings"))

    @app.route("/admin/settings", methods=["POST"])
    @admin_required
    def admin_settings():
        f = request.form
        try:
            store.save_settings(g.user["id"], f.get("workspace_name"), f.get("allow_registration"),
                                f.get("default_priority"))
            flash("Settings saved.", "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(url_for("admin_panel", tab="settings"))

    @app.route("/admin/data/<table>.csv")
    @admin_required
    def admin_table_csv(table):
        try:
            cols, rows, _ = store.table_rows(table, limit=1_000_000)
        except TaskError:
            abort(404)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        w.writerows(rows)
        return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=taskflow_{table}_{date.today()}.csv"})

    @app.route("/admin/maintenance", methods=["POST"])
    @admin_required
    def admin_maintenance():
        f = request.form
        action = f.get("action")
        days = int(f["days"]) if f.get("days", "").isdigit() else 0
        if action == "clear_activity":
            n = store.clear_activity(g.user["id"], days)
            flash(f"{n} activity entries removed.", "success")
        elif action == "clean_tasks":
            n = store.delete_completed_tasks(g.user["id"], days)
            flash(f"{n} completed tasks removed.", "success")
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

    # ------------------------------------------------------------ team (admin)
    @app.route("/team")
    @login_required
    def team():
        return render_template("team.html", people=store.user_report())

    @app.route("/team/add", methods=["POST"])
    @admin_required
    def team_add():
        f = request.form
        try:
            store.create_user(f.get("name"), f.get("email"), f.get("password"), role=f.get("role"),
                              job_title=f.get("job_title", ""), by=g.user["id"])
            flash(f"{f.get('name')} was added to the team.", "success")
        except TaskError as e:
            flash(str(e), "error")
        return redirect(url_for("team"))

    @app.route("/team/<int:user_id>/<action>", methods=["POST"])
    @admin_required
    def team_action(user_id, action):
        if not store.user(user_id):
            abort(404)
        try:
            if action == "role":
                store.admin_set_role(g.user["id"], user_id, request.form.get("role"))
            elif action == "toggle":
                store.admin_toggle_active(g.user["id"], user_id)
            elif action == "reset":
                store.admin_reset_password(g.user["id"], user_id, request.form.get("password"))
            elif action == "edit":
                f = request.form
                store.admin_update_user(g.user["id"], user_id, f.get("name"), f.get("email"), f.get("job_title"),
                                        f.get("color"))
            elif action == "delete":
                name = store.user(user_id)["name"]
                store.delete_user(g.user["id"], user_id)
                flash(f"{name}'s account was deleted. Their tasks are now unassigned.", "success")
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
        return render_template("reports.html", s=store.stats(), people=store.user_report(),
                               projects=store.projects(), chart=store.completed_per_day(30),
                               activity=store.activity(30))

    @app.route("/reports/tasks.csv")
    @login_required
    def export_csv():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ID", "Title", "Project", "Status", "Priority", "Assigned to", "Created by",
                    "Due date", "Created", "Completed"])
        for t in store.tasks(sort="due"):
            w.writerow([t["id"], t["title"], t["project_name"] or "", STATUS_NAMES[t["status"]],
                        PRIORITY_NAMES[t["priority"]], t["assignee_name"] or "Unassigned", t["creator_name"],
                        t["due_date"] or "", t["created_at"], t["completed_at"] or ""])
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=taskflow_tasks_{date.today()}.csv"})

    # ------------------------------------------------------------ profile
    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("form") == "password":
                    store.change_password(g.user["id"], f.get("old"), f.get("new"), f.get("confirm"))
                    flash("Password changed.", "success")
                elif f.get("form") == "delete":
                    store.delete_own_account(g.user["id"], f.get("password"))
                    session.clear()
                    flash("Your account was deleted. Goodbye!", "success")
                    return redirect(url_for("login"))
                else:
                    store.update_profile(g.user["id"], f.get("name"), f.get("email"), f.get("job_title"), f.get("color"))
                    flash("Profile saved.", "success")
            except TaskError as e:
                flash(str(e), "error")
            return redirect(url_for("profile"))
        uid = g.user["id"]
        return render_template("profile.html", s=store.stats(uid),
                               created=store.tasks(creator=uid, sort="updated")[:8],
                               my_projects=[p for p in store.projects(include_archived=True) if p["created_by"] == uid])

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

    return app


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
