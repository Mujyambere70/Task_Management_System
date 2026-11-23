"""
demo_data.py - fills an empty database with demo data built around the author's real portfolio
(projects and studies from Jean Michel Mujyambere's CV). The teammates are fictional.
All dates are relative to today, so the app always looks "alive".
"""

import io
import random
from datetime import date, datetime, timedelta

from core import TaskError

PASSWORD_ADMIN = "Admin@1234"
PASSWORD_USER = "Demo@1234"

# name, email, password, job title, language of the interface (all teammates are fictional)
USERS = [
    ("Jean Michel Mujyambere", "admin@taskflow.demo", PASSWORD_ADMIN, "Software Engineer · Project Lead", "en"),
    ("Anna Petrova", "anna@taskflow.demo", PASSWORD_USER, "Frontend Developer", "ru"),
    ("David Okafor", "david@taskflow.demo", PASSWORD_USER, "Backend Developer", "en"),
    ("Maria Ivanova", "maria@taskflow.demo", PASSWORD_USER, "UX Designer", "ru"),
    ("Samuel Habimana", "samuel@taskflow.demo", PASSWORD_USER, "Data Analyst", "rw"),
    ("Elena Smirnova", "elena@taskflow.demo", PASSWORD_USER, "QA Engineer", "ru"),
    ("Li Wei", "li@taskflow.demo", PASSWORD_USER, "Data Scientist", "zh"),
    ("Fatima Haddad", "fatima@taskflow.demo", PASSWORD_USER, "UX Researcher", "ar"),
    ("Lucas Martin", "lucas@taskflow.demo", PASSWORD_USER, "Statistics Student", "fr"),
    ("Sofía García", "sofia@taskflow.demo", PASSWORD_USER, "Machine Learning Student", "es"),
]

# workspace name, description, color, [(user index, role)]
WORKSPACES = [
    ("Jean's Dev Studio", "Portfolio web apps: Yo Delivery, PyBank, TaskFlow and CalcWise.", "#4f46e5",
     [(0, "admin"), (1, "member"), (2, "admin"), (3, "member"), (4, "member"), (5, "member")]),
    ("SFU Data Analysis Group", "Master's study group, Siberian Federal University: labs, research and exams.",
     "#10b981", [(0, "admin"), (4, "member"), (5, "member"), (6, "member"), (7, "member"), (8, "member"), (9, "member")]),
]

# workspace index, name, description, color, owner index
PROJECTS = [
    (0, "Yo Delivery — E-commerce", "Online marketplace: catalogue, cart, coupons, checkout, order tracking and "
     "admin panel. Python · Flask · PostgreSQL.", "#f59e0b", 0),
    (0, "PyBank — Online Banking", "Net-banking web app designed with OOP: transfers, statements, cards, PIN "
     "security. Python · Flask · PostgreSQL.", "#0ea5e9", 0),
    (0, "TaskFlow — Task Manager", "This app: the HyperionDev capstone console program rebuilt as a full web app. "
     "Version 2: workspaces, live board, reminders, files, time tracking, resumes.", "#4f46e5", 0),
    (0, "CalcWise — Finance Calculator", "Investment, loan and bond calculators with charts.", "#ec4899", 4),
    (0, "Certifications & Career", "Google certificates, AWS Cloud Practitioner, CV and portfolio.", "#8b5cf6", 0),
    (1, "Lab works", "Computational statistics, machine learning, PostgreSQL, mathematical modelling.", "#10b981", 0),
    (1, "Master's research", "Data analysis in science and entrepreneurship.", "#14b8a6", 0),
]

# project index (None = no project, in the first workspace), title, description, status, priority,
# assignee index, due in days, checklist (items starting with "+" are already done)
TASKS = [
    # ---- Yo Delivery
    (0, "Build product catalogue with 42 products", "Categories, brands, 3 pictures per product, prices with MRP and discount.",
     "done", "high", 0, -20, []),
    (0, "Search with filters and sorting", "Filter by category, brand, price, rating, discount; sort by popularity, price, newest.",
     "done", "medium", 1, -16, []),
    (0, "Cart, coupons and checkout", "Guest cart that moves into the account at login; coupons; card payment in test mode "
     "and Cash on Delivery.", "done", "high", 0, -12,
     ["+Cart with quantity and save for later", "+Coupon rules", "+Checkout with address", "+Payment options"]),
    (0, "Live order tracking timeline", "Placed → Packed → Shipped → Out for delivery → Delivered.", "done", "medium", 2, -9, []),
    (0, "Admin panel: sales dashboard and product management", "KPIs, 14-day sales chart, orders, products, customers, "
     "coupons, reviews, activity log and database viewer with CSV export.", "done", "high", 0, -6,
     ["+Dashboard", "+Products with image upload", "+Orders and status changes", "+Activity log", "+Database viewer"]),
    (0, "Write automated tests for checkout and admin", "Store logic, pages, admin panel.", "done", "high", 5, -3, []),
    (0, "Printable invoices", "Invoice page with tax lines that prints on one A4 page. @Jean please check the totals.",
     "review", "medium", 1, 1, ["+Invoice template", "+Tax calculation", "Print CSS check"]),
    (0, "Mobile layout for checkout", "Checkout steps must fit a 360 px screen.", "in_progress", "medium", 3, 3,
     ["+Address step", "Payment step", "Order summary"]),
    (0, "Move Yo Delivery to Gunicorn on Fly.io", "Replace the Flask development server; min_machines_running = 0.",
     "todo", "high", 2, 1, ["Dockerfile CMD", "fly.toml", "Smoke test online"]),
    # ---- PyBank
    (1, "Design Bank and BankAccount classes (OOP)", "Deposit, withdraw, transfer, statement - as in the project synopsis.",
     "done", "high", 0, -18, []),
    (1, "Two-step fund transfer with PIN", "Review screen, then PIN confirmation; atomic database transaction.",
     "done", "urgent", 2, -10, []),
    (1, "Lock login after 5 failed attempts", "Lock for 15 minutes and log the event.", "done", "high", 2, -8, []),
    (1, "Statements with CSV export and printable receipts", "", "done", "medium", 1, -4, []),
    (1, "Daily transfer limits and session timeout", "Limit per day per account; sign out after 10 minutes without activity.",
     "in_progress", "high", 2, 2, ["+Daily limit check", "Session timeout", "Tests for limits"]),
    (1, "Bank-style UI with real pictures", "Make it look like a real bank website.", "review", "medium", 3, 0, []),
    (1, "Write user guide: how to run the app", "Step by step for Windows: venv, pip install, python app.py.",
     "todo", "low", 0, 6, []),
    # ---- TaskFlow
    (2, "Several workspaces for different teams", "Memberships with roles, invite links, workspace switcher.",
     "done", "urgent", 0, -6, ["+Tables and migration of version 1 data", "+Invite link", "+Switcher in the sidebar"]),
    (2, "Real-time board updates with WebSockets", "flask-sock + events table, polling as a fallback.",
     "done", "high", 2, -4, []),
    (2, "Email reminders one day before a deadline", "Brevo or SMTP; demo addresses are never emailed.",
     "done", "high", 2, -3, []),
    (2, "File attachments on tasks", "Up to 10 files of 5 MB per task, stored in the database.", "done", "high", 1, -3, []),
    (2, "Time tracking and burndown charts", "Timer, manual entries, estimates, time report, burndown by tasks or hours.",
     "done", "high", 4, -2, ["+Timer", "+Manual time", "+Burndown SVG chart"]),
    (2, "Resume builder with a shareable link", "3 designs, photo, sections, public link, Explore page, print to PDF.",
     "in_progress", "high", 0, 1, ["+Editor with live preview", "+Public page", "+Explore", "Import from JSON Resume"]),
    (2, "Translate the interface into 7 languages", "English, Russian, French, Spanish, Arabic (RTL), Chinese, Kinyarwanda.",
     "review", "high", 3, 0, ["+Catalogs", "+Right-to-left layout", "Native speaker check for Kinyarwanda"]),
    (2, "Deploy TaskFlow v2 to Fly.io with Neon PostgreSQL", "Dockerfile, fly.toml, secrets DATABASE_URL and SECRET_KEY.",
     "todo", "urgent", 0, 2, ["fly launch --no-deploy", "fly secrets set", "fly deploy", "Update README links"]),
    (2, "Accessibility check (keyboard and contrast)", "", "todo", "medium", 5, 5, []),
    (2, "Dark mode", "", "todo", "low", 3, 14, []),
    (2, "Sprints, Gantt chart and task dependencies", "Version 3: sprint burndown and velocity, Gantt bars with arrows, "
     "\"waits for\" links that block finishing too early.", "in_progress", "high", 0, 4,
     ["+Sprints and burndown", "+Gantt chart", "Dependency arrows on the board"]),
    (2, "Site administrator back office", "Accounts, workspaces, resumes, translations, messages sent, audit log, "
     "site settings and backup restore.", "review", "urgent", 2, 2, ["+Accounts", "+Site settings", "Restore test"]),
    (2, "Web Push, calendar feed and Slack / Telegram messages", "", "todo", "high", 2, 6, []),
    (2, "Resume as a PDF made on the server", "Same designs as the web page, with Arabic and Chinese fonts.",
     "todo", "medium", 1, 8, []),
    # ---- CalcWise
    (3, "Investment calculator: simple and compound interest", "", "done", "medium", 4, -15, []),
    (3, "Bond repayment calculator with chart", "Monthly repayment and total interest.", "done", "medium", 4, -11, []),
    (3, "Retry Fly.io deploy with a local build", "flyctl deploy --local-only after the remote builder error.",
     "in_progress", "high", 0, 1, ["+Check Dockerfile", "Deploy", "Check calcwise.fly.dev"]),
    # ---- Certifications & career
    (4, "Google Advanced Data Analytics certificate", "", "done", "high", 0, -40, []),
    (4, "Google UX Design Professional certificate", "", "done", "medium", 0, -28, []),
    (4, "AWS Cloud Practitioner — finish course modules", "", "in_progress", "high", 0, 12,
     ["+Cloud concepts", "+Security and compliance", "Technology and services", "Billing and pricing"]),
    (4, "Update GitHub profile links to fly.dev", "The old Render links do not work any more.", "todo", "high", 0, 1, []),
    (4, "Write a LinkedIn post about TaskFlow v2", "", "todo", "low", 0, 4, []),
    # ---- SFU: lab works (second workspace)
    (5, "Computational statistics — lab work (variant 14)", "Вычислительная статистика: code in Python and R + defense report.",
     "in_progress", "high", 0, 3, ["+Load the dataset", "+Descriptive statistics", "Histogram and box plot",
                                   "Python and R versions", "Bilingual defense report"]),
    (5, "Machine learning basics — lab work (variant 14)", "Основы машинного обучения.", "todo", "high", 0, 6,
     ["Train / test split", "Fit the model", "Accuracy and confusion matrix", "Report with plots explained"]),
    (5, "PostgreSQL practical work — report on e-courses", "Системы хранения, обработки и управления данными.",
     "in_progress", "medium", 0, 2, ["+Create tables", "+Queries with JOIN", "Upload report"]),
    (5, "Share clean datasets for the group", "CSV files with a short description of every column.", "done", "medium", 6, -4, []),
    (5, "Regression exercise: compare Python and R output", "", "review", "medium", 8, 1, []),
    (5, "Mathematical modelling — lecture assignment", "Математическое моделирование.", "todo", "medium", 9, 8, []),
    # ---- SFU: research
    (6, "Read papers on data analysis in entrepreneurship", "For the master's research topic.", "in_progress", "medium",
     0, 12, ["+Find 10 papers", "Summaries", "Choose the method"]),
    (6, "Interview guide for small business owners", "", "todo", "medium", 7, 9, []),
    (6, "Literature table in a shared spreadsheet", "", "done", "low", 9, -2, []),
    # ---- no project
    (None, "Weekly team sync agenda", "Progress, blockers, next steps.", "todo", "low", 0, 1, []),
    (None, "Renew domain and SSL certificate", "Expires at the end of the month.", "todo", "urgent", 2, -3, []),
]

# task title (start), author index, text
COMMENTS = [
    ("Cart, coupons", 1, "Tested the welcome coupon - works only on the first order, as planned."),
    ("Admin panel: sales", 4, "The 14-day sales chart matches my numbers from the database."),
    ("Two-step fund transfer", 5, "Tried a wrong PIN 3 times - transfer is cancelled correctly."),
    ("Two-step fund transfer", 0, "Great. Please also check the transfer to the same account is blocked."),
    ("Daily transfer limits", 2, "Daily limit is done. Session timeout comes tomorrow."),
    ("Real-time board", 3, "I moved a card on my laptop and it moved on my phone at once!"),
    ("Real-time board", 2, "Works through the Fly.io proxy too. If WebSocket is blocked the page asks every few seconds."),
    ("Printable invoices", 1, "@Jean the totals are ready for your check."),
    ("Translate the interface", 4, "I will check the Kinyarwanda texts this week."),
    ("Computational statistics", 0, "Remember: the teacher asks about every function and every plot at the defense."),
    ("Regression exercise", 6, "My R and Python coefficients are the same up to 4 decimals."),
    ("Mobile layout for checkout", 3, "Address step is ready on 360 px. Working on payment now. @David can you review?"),
]

ESTIMATE = {"urgent": 300, "high": 240, "medium": 150, "low": 60}     # minutes, changed a little per task


def ts(d, hour=10, minute=0):
    moment = min(datetime(d.year, d.month, d.day, hour, minute), datetime.now() - timedelta(minutes=7))
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _wireframe_png():
    """A small picture for the demo attachment (made here, so no file is needed)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    img = Image.new("RGB", (360, 640), "#f5f6fa")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 360, 64], fill="#f59e0b")
    for i, y in enumerate((96, 200, 304)):
        d.rounded_rectangle([20, y, 340, y + 88], 12, fill="#ffffff", outline="#e7e9f0")
        d.rectangle([36, y + 18, 120 + i * 40, y + 30], fill="#c7cbe0")
        d.rectangle([36, y + 44, 300, y + 54], fill="#e5e7ef")
    d.rounded_rectangle([20, 560, 340, 612], 12, fill="#4f46e5")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def load_demo(store):
    rnd = random.Random(14)
    today = date.today()
    try:
        user_ids = [store.create_user(n, e, p, job_title=j, lang=lang) for n, e, p, j, lang in USERS]
    except TaskError:
        return                       # another server process filled the empty database at the same moment
    admin = user_ids[0]
    ws_ids = []
    with store.db() as con:
        con.execute("DELETE FROM activity")            # replaced with a realistic history below
    for name, desc, color, members in WORKSPACES:
        ws = store.create_workspace(admin, name, desc, color)
        for idx, role in members:
            if idx:
                store.add_member(ws, user_ids[idx], role)
        ws_ids.append(ws)
    with store.db() as con:
        con.execute("UPDATE users SET last_ws = ?", (ws_ids[0],))
        con.execute("DELETE FROM activity")
        con.execute("DELETE FROM events")
    project_ids, project_ws = [], []
    for w, n, d, c, o in PROJECTS:
        project_ids.append(store.save_project(user_ids[o], ws_ids[w], n, d, c))
        project_ws.append(ws_ids[w])

    with store.db() as con:
        con.execute("DELETE FROM activity")
        con.execute("DELETE FROM events")
        events, task_ids, done_tasks, open_tasks = [], {}, [], []
        for i, (p, title, desc, status, prio, who, due_in, checklist) in enumerate(TASKS):
            ws = project_ws[p] if p is not None else ws_ids[0]
            due = today + timedelta(days=due_in)
            created = min(today, due) - timedelta(days=rnd.randint(3, 12))
            assignee = user_ids[who]
            creator = admin if (i % 4 or who == 0) else assignee
            completed = None
            if status == "done":
                last = min(today, due)
                span = max(1, (last - created).days)
                completed = ts(created + timedelta(days=rnd.randint(1, span)), rnd.randint(9, 19), rnd.randint(0, 59))
            updated = completed or ts(min(today, created + timedelta(days=rnd.randint(1, 5))), rnd.randint(9, 19))
            estimate = None if i % 5 == 4 else ESTIMATE[prio] + rnd.choice((-30, 0, 30, 60))
            tid = con.execute(
                "INSERT INTO tasks (project_id, title, description, status, priority, assignee_id, created_by, "
                "due_date, created_at, updated_at, completed_at, workspace_id, estimate_minutes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (project_ids[p] if p is not None else None, title, desc, status, prio, assignee, creator,
                 due.isoformat(), ts(created, 9, rnd.randint(0, 59)), updated, completed, ws, estimate)).lastrowid
            task_ids[title] = (tid, ws)
            (done_tasks if completed else open_tasks).append((tid, ws, assignee, created, completed, estimate, status))
            events.append((ts(created, 9, 5), creator, tid, ws, "task_created", title))
            for item in checklist:
                done = item.startswith("+") or status == "done"
                con.execute("INSERT INTO subtasks (task_id, title, done, created_by, created_at) VALUES (?,?,?,?,?)",
                            (tid, item.lstrip("+"), 1 if done else 0, assignee, ts(created, 9, 30)))
            if completed:
                events.append((completed, assignee, tid, ws, "task_completed", "In Review → Done"))
            elif status != "todo":
                events.append((updated, assignee, tid, ws, "status_changed",
                               "To Do → " + {"in_progress": "In Progress", "review": "In Review"}[status]))

        # time entries: finished tasks used about their estimate, open tasks a part of it
        for tid, ws, who, created, completed, estimate, status in done_tasks + open_tasks:
            if status == "todo" or not estimate:
                continue
            total = estimate * (rnd.choice((0.8, 0.9, 1.0, 1.1, 1.25)) if completed else rnd.choice((0.3, 0.5, 0.7)))
            end_day = datetime.strptime(completed, "%Y-%m-%d %H:%M:%S").date() if completed else today
            parts = rnd.randint(1, 3)
            for k in range(parts):
                minutes = max(15, int(total / parts) // 5 * 5)
                day = max(created, end_day - timedelta(days=rnd.randint(0, 3)))
                start = datetime(day.year, day.month, day.day, rnd.randint(9, 15), rnd.choice((0, 15, 30, 45)))
                if start + timedelta(minutes=minutes) > datetime.now():
                    start = datetime.now() - timedelta(minutes=minutes + 30)
                con.execute("INSERT INTO time_entries (task_id, workspace_id, user_id, started_at, ended_at, minutes, note, "
                            "created_at) VALUES (?,?,?,?,?,?,?,?)",
                            (tid, ws, who, start.strftime("%Y-%m-%d %H:%M:%S"),
                             (start + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S"), minutes,
                             rnd.choice(("", "", "Coding", "Testing", "Review and fixes", "Research")), ts(day, 18)))
                events.append((ts(day, 18), who, tid, ws, "time_logged", f"{minutes} min"))

        for n, (start, who, body) in enumerate(COMMENTS):
            tid, ws = next(v for k, v in task_ids.items() if k.startswith(start))
            when = ts(today - timedelta(days=rnd.randint(0, 3)), 10 + n % 8, rnd.randint(0, 59))
            con.execute("INSERT INTO comments (task_id, user_id, body, created_at) VALUES (?,?,?,?)",
                        (tid, user_ids[who], body, when))
            events.append((when, user_ids[who], tid, ws, "commented", body[:80]))

        for w, (name, _d, _c, members) in enumerate(WORKSPACES):
            events.append((ts(today - timedelta(days=46), 9), admin, None, ws_ids[w], "workspace_created", name))
            for idx, role in members:
                events.append((ts(today - timedelta(days=45), 9), admin, None, ws_ids[w], "user_added",
                               f"{USERS[idx][0]} joined as {role}"))
        for when, uid, tid, ws, action, details in sorted(events, key=lambda e: e[0]):
            con.execute("INSERT INTO activity (user_id, task_id, action, details, created_at, workspace_id) "
                        "VALUES (?,?,?,?,?,?)", (uid, tid, action, details, when, ws))

        # a few notifications under the bell of the admin (in his language)
        def note(kind, text, title=None, who=None):
            tid, ws = task_ids[title] if title else (None, ws_ids[0])
            store.notify(con, admin, kind, text.replace("{0}", USERS[who][0] if who is not None else "")
                         .replace("{1}", title or ""), ws=ws, task_id=tid)
        note("mention", "{0} mentioned you in “{1}”.", "Printable invoices", 1)
        note("assigned", "{0} assigned “{1}” to you.", "Deploy TaskFlow v2 to Fly.io with Neon PostgreSQL", 2)
        note("comment", "{0} commented on “{1}”.", "Regression exercise: compare Python and R output", 6)
        note("reminder", "“{1}” is due tomorrow.", "Update GitHub profile links to fly.dev")
        con.execute("DELETE FROM events")              # old live events are not needed

    # files on tasks
    png = _wireframe_png()
    if png:
        store.add_attachment(user_ids[3], task_ids["Mobile layout for checkout"][0], "checkout-wireframe.png", png)
    csv_data = "student,hours_studied,score\n" + "\n".join(
        f"S{i:02d},{h},{min(100, 38 + h * 6 + rnd.randint(-6, 6))}" for i, h in enumerate(
            [rnd.randint(1, 10) for _ in range(24)], 1)) + "\n"
    store.add_attachment(user_ids[6], task_ids["Share clean datasets for the group"][0], "study_hours.csv",
                         csv_data.encode())
    store.add_attachment(admin, task_ids["Deploy TaskFlow v2 to Fly.io with Neon PostgreSQL"][0], "deploy-steps.md",
                         DEPLOY_NOTES.encode())

    # resumes
    import resumes
    rid = store.create_resume(admin, "My CV (English)", resumes.clean_resume_data(JEAN_RESUME), "modern", "#4f46e5", "en")
    with store.db() as con:
        con.execute("UPDATE resumes SET slug = 'jean-michel-mujyambere', visibility = 'public', views = 128 WHERE id = ?",
                    (rid,))
    copy = store.duplicate_resume(admin, rid)
    with store.db() as con:
        con.execute("UPDATE resumes SET title = 'Резюме (русские заголовки)', lang = 'ru', theme = 'classic', "
                    "accent = '#0f766e', slug = 'jean-michel-ru', visibility = 'link' WHERE id = ?", (copy,))
    for idx, title, data, theme, accent, lang, views in SAMPLE_RESUMES:
        r = store.create_resume(user_ids[idx], title, resumes.clean_resume_data(data), theme, accent, lang)
        with store.db() as con:
            con.execute("UPDATE resumes SET visibility = 'public', views = ? WHERE id = ?", (views, r))
    _planning_demo(store, admin, ws_ids, task_ids, today)
    with store.db() as con:
        con.execute("DELETE FROM events")


def _planning_demo(store, admin, ws_ids, task_ids, today):
    """Version 3 demo data: labels, two sprints (one closed, one active), "waits for" links, a weekly task and
    start dates for the Gantt chart."""
    labels = {}
    for ws_i, name, color in [(0, "bug", "#ef4444"), (0, "feature", "#4f46e5"), (0, "deploy", "#0ea5e9"),
                              (0, "design", "#ec4899"), (0, "docs", "#10b981"), (1, "lab work", "#14b8a6"),
                              (1, "R", "#0ea5e9"), (1, "Python", "#f59e0b")]:
        labels[name] = store.save_label(admin, ws_ids[ws_i], name, color)
    tagged = {
        "Printable invoices": ["feature"], "Mobile layout for checkout": ["design", "bug"],
        "Move Yo Delivery to Gunicorn on Fly.io": ["deploy"], "Daily transfer limits and session timeout": ["feature"],
        "Bank-style UI with real pictures": ["design"], "Write user guide: how to run the app": ["docs"],
        "Deploy TaskFlow v2 to Fly.io with Neon PostgreSQL": ["deploy"], "Dark mode": ["design", "feature"],
        "Sprints, Gantt chart and task dependencies": ["feature"], "Site administrator back office": ["feature"],
        "Web Push, calendar feed and Slack / Telegram messages": ["feature"],
        "Resume as a PDF made on the server": ["feature"], "Renew domain and SSL certificate": ["deploy"],
        "Retry Fly.io deploy with a local build": ["deploy", "bug"], "Accessibility check (keyboard and contrast)": ["bug"],
        "Computational statistics — lab work (variant 14)": ["lab work", "Python", "R"],
        "Machine learning basics — lab work (variant 14)": ["lab work", "Python"],
        "Regression exercise: compare Python and R output": ["Python", "R"],
    }
    ws0 = ws_ids[0]
    with store.db() as con:
        for title, names in tagged.items():
            tid, ws = task_ids[title]
            store.set_task_labels(con, ws, tid, [labels[n] for n in names])
        # sprints of the first workspace: the last two weeks (closed) and these two weeks (active)
        s1 = con.execute("INSERT INTO sprints (workspace_id, name, goal, start_date, end_date, status, created_by, created_at, "
                         "closed_at) VALUES (?,?,?,?,?,?,?,?,?)", (ws0, "Sprint 1 — shop and bank", "Finish the shop checkout and the bank transfers.",
                                                      (today - timedelta(days=20)).isoformat(),
                                                      (today - timedelta(days=7)).isoformat(), "closed", admin,
                                                      ts(today - timedelta(days=21), 9), ts(today - timedelta(days=7), 18))).lastrowid
        s2 = con.execute("INSERT INTO sprints (workspace_id, name, goal, start_date, end_date, status, created_by, created_at) "
                         "VALUES (?,?,?,?,?,?,?,?)", (ws0, "Sprint 2 — TaskFlow 3", "Planning tools, admin back office and the new deploy.",
                                                      (today - timedelta(days=6)).isoformat(),
                                                      (today + timedelta(days=7)).isoformat(), "active", admin,
                                                      ts(today - timedelta(days=7), 9))).lastrowid
        for r in con.execute("SELECT id, status, completed_at, due_date FROM tasks WHERE workspace_id = ?", (ws0,)).fetchall():
            first_half = r["completed_at"] and r["completed_at"][:10] <= (today - timedelta(days=7)).isoformat()
            sid = s1 if first_half else (s2 if r["status"] != "done" or r["completed_at"][:10] >= (today - timedelta(days=6)).isoformat() else None)
            if r["due_date"] and r["due_date"] > (today + timedelta(days=9)).isoformat():
                sid = None                                               # far away: stays in the backlog
            con.execute("UPDATE tasks SET sprint_id = ? WHERE id = ?", (sid, r["id"]))
        # start dates so the Gantt chart shows real lengths of work
        for title, back in [("Sprints, Gantt chart and task dependencies", 5), ("Site administrator back office", 4),
                            ("Web Push, calendar feed and Slack / Telegram messages", -2), ("Resume as a PDF made on the server", 1),
                            ("Deploy TaskFlow v2 to Fly.io with Neon PostgreSQL", 1), ("Mobile layout for checkout", 4),
                            ("Daily transfer limits and session timeout", 3), ("Printable invoices", 3)]:
            tid, _ = task_ids[title]
            con.execute("UPDATE tasks SET start_date = ? WHERE id = ?", ((today - timedelta(days=back)).isoformat(), tid))
        # "waits for" links
        for task, waits_for in [("Update GitHub profile links to fly.dev", "Deploy TaskFlow v2 to Fly.io with Neon PostgreSQL"),
                                ("Write a LinkedIn post about TaskFlow v2", "Update GitHub profile links to fly.dev"),
                                ("Web Push, calendar feed and Slack / Telegram messages", "Site administrator back office"),
                                ("Resume as a PDF made on the server", "Sprints, Gantt chart and task dependencies")]:
            con.execute("INSERT INTO task_deps (task_id, blocked_by) VALUES (?,?)", (task_ids[task][0], task_ids[waits_for][0]))
        # a task that comes back every week
        con.execute("UPDATE tasks SET repeat = 'weekly' WHERE id = ?", (task_ids["Weekly team sync agenda"][0],))
        con.execute("DELETE FROM activity WHERE action = 'labels_changed'")


DEPLOY_NOTES = """# Deploy TaskFlow v2 (Fly.io + Neon)

1. fly launch --no-deploy
2. fly secrets set DATABASE_URL=... SECRET_KEY=... APP_URL=https://<app>.fly.dev
3. fly deploy

Never paste the database password into a chat or into GitHub.
"""


def _section(kind, items=None, title=None, body=""):
    from resumes import new_section
    s = new_section(kind, title)
    s["items"], s["body"] = items or [], body
    return s


# The author's real CV (no phone number in a public page)
JEAN_RESUME = {
    "name": "Jean Michel Mujyambere",
    "headline": "Software Engineer | Python Development | Data Analytics | UX Design | Machine Learning",
    "email": "mujyamberejean9@gmail.com", "phone": "", "location": "Krasnoyarsk, Russia", "website": "",
    "links": [{"label": "GitHub", "url": "https://github.com/Mujyambere70"},
              {"label": "LinkedIn", "url": "https://linkedin.com/in/jeanmichel"}],
    "summary": "Results-driven Software Engineer with 3+ years of technical and educational experience in Python, "
               "Data Analytics and Full-Stack Development. Google certificates in Advanced Data Analytics, Cybersecurity "
               "and UX Design. Master's student in Applied Mathematics and Informatics (Data Analysis) at Siberian "
               "Federal University.",
    "sections": [
        _section("experience", [
            {"role": "Python & Analytics Intern", "org": "YBI Foundation", "place": "Remote", "start": "11/2022",
             "end": "01/2023", "bullets": "Built machine learning models for business analytics (+15% prediction accuracy)\n"
                                         "Made data-driven web apps with Python, SQL and Tableau"},
            {"role": "Mathematics / Chemistry Teacher", "org": "Essifih Muhanga", "place": "Rwanda", "start": "10/2021",
             "end": "02/2022", "bullets": "Improved student pass rates by 25% with interactive lesson plans"},
            {"role": "Freelance Virtual Assistant", "org": "Parke Lane People Limited", "place": "Remote",
             "start": "01/2020", "end": "01/2022",
             "bullets": "Automated scheduling and data entry: admin time -40%\nWorked with 50+ remote clients, 95% task completion"},
        ]),
        _section("projects", [
            {"name": "Yo Delivery", "tech": "Python, Flask, PostgreSQL, Jinja2", "link": "https://yo-delivery.fly.dev/",
             "bullets": "E-commerce and delivery platform: catalogue, cart, coupons, checkout, order tracking, admin panel\n"
                        "7 languages, automated tests"},
            {"name": "PyBank", "tech": "Python, Flask, PostgreSQL, OOP", "link": "https://michel-pybank.fly.dev/",
             "bullets": "Online banking system: transfers, statements, PIN, login lock, daily limits"},
            {"name": "TaskFlow", "tech": "Python, Flask, PostgreSQL, WebSocket", "link": "https://github.com/Mujyambere70",
             "bullets": "Team task manager: workspaces, live Kanban board, reminders, files, time tracking, burndown charts\n"
                        "Resume builder with a shareable link"},
            {"name": "CalcWise", "tech": "Python, Flask, Chart.js", "link": "https://calcwise.fly.dev/",
             "bullets": "Finance calculator: investments, loans and bonds; manual computation time -70%"},
        ]),
        _section("education", [
            {"degree": "Master's, Applied Mathematics and Informatics (Data Analysis)", "school": "Siberian Federal University",
             "place": "Krasnoyarsk, Russia", "start": "09/2026", "end": "Present", "bullets": "State-funded, expected 2028"},
            {"degree": "Preparatory Programme for International Students (Engineering & Technology)",
             "school": "Siberian Federal University", "place": "Krasnoyarsk, Russia", "start": "01/2025", "end": "08/2026",
             "bullets": "Russian language, Mathematics, Physics, Informatics, Chemistry"},
            {"degree": "Bachelor of Computer Applications (AI & ML)", "school": "Maharishi Markandeshwar University",
             "place": "India", "start": "08/2022", "end": "06/2025"},
            {"degree": "Software Engineering Bootcamp", "school": "HyperionDev", "start": "12/2023", "end": "06/2024"},
            {"degree": "Advanced Diploma in Mathematics/Chemistry with Education", "school": "University of Rwanda",
             "place": "Rwanda", "start": "10/2018", "end": "11/2022"},
        ]),
        _section("skills", [
            {"group": "Programming", "items": "Python, SQL, R, Java, JavaScript (ES6), HTML, CSS"},
            {"group": "Web", "items": "Flask, Django, Jinja2, Bootstrap, jQuery, REST"},
            {"group": "Data", "items": "PostgreSQL, SQLite, Data analytics, Tableau, Dashboards, Machine learning"},
            {"group": "Tools", "items": "Git, GitHub, Linux, Docker, AWS, Figma, JIRA, Agile"},
        ]),
        _section("certifications", [
            {"name": "Google Advanced Data Analytics", "issuer": "Google"},
            {"name": "Google Cybersecurity Professional", "issuer": "Google"},
            {"name": "Google UX Design Professional", "issuer": "Google"},
            {"name": "Google Project Management Professional", "issuer": "Google"},
            {"name": "AWS Cloud Practitioner", "issuer": "Amazon Web Services", "date": "In progress"},
        ]),
        _section("languages", [
            {"name": "English", "level": "Native", "score": 5},
            {"name": "Kinyarwanda", "level": "Proficient", "score": 5},
            {"name": "French", "level": "Intermediate", "score": 3},
            {"name": "Russian", "level": "Intermediate (B1-B2)", "score": 3},
        ]),
        _section("awards", [
            {"name": "3 data-driven apps with 80% client satisfaction"},
            {"name": "6 projects delivered 100% on time in 2022"},
            {"name": "Student engagement +60% with interactive lessons"},
        ], title="Key achievements"),
        _section("interests", [{"name": "Open source"}, {"name": "AI / ML research"}, {"name": "Cybersecurity workshops"}]),
    ],
}

# fictional people for the Explore page: user index, title, content, theme, accent, heading language, views
SAMPLE_RESUMES = [
    (1, "Frontend CV", {
        "name": "Anna Petrova", "headline": "Frontend Developer · JavaScript, CSS, accessibility",
        "email": "anna@taskflow.demo", "location": "Novosibirsk, Russia",
        "links": [{"label": "Portfolio", "url": "https://example.com/anna"}],
        "summary": "Frontend developer who loves clean interfaces that work on every screen. (Demo person.)",
        "sections": [
            _section("experience", [{"role": "Frontend Developer", "org": "Northwind Studio (fictional)", "start": "2023",
                                     "end": "Present", "bullets": "Built 12 responsive pages\nCut page load time by 35%"}]),
            _section("skills", [{"group": "Frontend", "items": "JavaScript, TypeScript, HTML, CSS, Vue, Figma"}]),
            _section("languages", [{"name": "Russian", "level": "Native", "score": 5},
                                   {"name": "English", "level": "B2", "score": 4}]),
        ]}, "compact", "#0f766e", "en", 41),
    (6, "数据科学简历", {
        "name": "Li Wei", "headline": "Data Scientist · Python, statistics, forecasting",
        "email": "li@taskflow.demo", "location": "Krasnoyarsk, Russia",
        "summary": "I turn data into clear charts and simple forecasts. (Demo person.)",
        "sections": [
            _section("education", [{"degree": "Master's, Data Analysis", "school": "Example University (fictional)",
                                    "start": "2025", "end": "Present"}]),
            _section("projects", [{"name": "Sales forecast", "tech": "Python, pandas, Prophet",
                                   "bullets": "Weekly forecast with 8% average error"}]),
            _section("skills", [{"group": "Data", "items": "Python, R, SQL, pandas, scikit-learn"}]),
            _section("languages", [{"name": "中文", "level": "Native", "score": 5},
                                   {"name": "English", "level": "C1", "score": 4}]),
        ]}, "classic", "#b91c1c", "zh", 23),
    (7, "سيرة ذاتية", {
        "name": "Fatima Haddad", "headline": "UX Researcher · interviews, usability tests",
        "email": "fatima@taskflow.demo", "location": "Casablanca, Morocco",
        "summary": "I help teams understand their users. (Demo person.)",
        "sections": [
            _section("experience", [{"role": "UX Researcher", "org": "Bluefin Labs (fictional)", "start": "2022",
                                     "end": "Present", "bullets": "30+ usability tests\nPersonas and journey maps"}]),
            _section("skills", [{"group": "Research", "items": "Interviews, Surveys, Usability testing, Figma"}]),
            _section("languages", [{"name": "العربية", "level": "Native", "score": 5},
                                   {"name": "Français", "level": "C1", "score": 4}]),
        ]}, "modern", "#7c3aed", "ar", 17),
]
