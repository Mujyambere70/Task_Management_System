"""
demo_data.py - fills an empty database with demo data built around the author's real portfolio
(projects and studies from Jean Michel Mujyambere's CV). The teammates are fictional.
All dates are relative to today, so the app always looks "alive".
"""

import random
from datetime import date, datetime, timedelta

PASSWORD_ADMIN = "Admin@1234"
PASSWORD_USER = "Demo@1234"

# name, email, password, role, job title
USERS = [
    ("Jean Michel Mujyambere", "admin@taskflow.demo", PASSWORD_ADMIN, "admin", "Software Engineer · Project Lead"),
    ("Anna Petrova", "anna@taskflow.demo", PASSWORD_USER, "member", "Frontend Developer"),
    ("David Okafor", "david@taskflow.demo", PASSWORD_USER, "member", "Backend Developer"),
    ("Maria Ivanova", "maria@taskflow.demo", PASSWORD_USER, "member", "UX Designer"),
    ("Samuel Habimana", "samuel@taskflow.demo", PASSWORD_USER, "member", "Data Analyst"),
    ("Elena Smirnova", "elena@taskflow.demo", PASSWORD_USER, "member", "QA Engineer"),
]

# name, description, color, owner index
PROJECTS = [
    ("Yo Delivery — E-commerce", "Online marketplace like Amazon / Flipkart: catalogue, cart, coupons, checkout, "
     "order tracking and admin panel. Python · Flask · SQLite.", "#f59e0b", 0),
    ("PyBank — Online Banking", "Net-banking web app designed with OOP (Bank and BankAccount classes): transfers, "
     "statements, PIN security. Python · Flask · SQLite.", "#0ea5e9", 0),
    ("TaskFlow — Task Manager", "This app: the HyperionDev capstone console program rebuilt as a full web app.",
     "#4f46e5", 0),
    ("SFU Master's — Data Analysis", "Applied Mathematics and Informatics, Institute of Space and Information "
     "Technologies (ИКИТ), Siberian Federal University: labs, practical works, research.", "#10b981", 0),
    ("Certifications & Career", "Google certificates, AWS Cloud Practitioner, CV and portfolio.", "#8b5cf6", 0),
    ("Python Mini Projects", "Early portfolio: chess game (Pygame), finance calculator, password generator.",
     "#ec4899", 4),
]

# project index (None = no project), title, description, status, priority, assignee index, due in days, checklist
# checklist items starting with "+" are already done
TASKS = [
    # ---- Yo Delivery
    (0, "Build product catalogue with 42 products", "Categories, brands, 3 pictures per product, prices with MRP and discount.",
     "done", "high", 0, -20, []),
    (0, "Search with filters and sorting", "Filter by category, brand, price, rating, discount; sort by popularity, price, newest.",
     "done", "medium", 1, -16, []),
    (0, "Cart, coupons and checkout", "Guest cart that moves into the account at login; WELCOME100 / YO10 / BIGSALE coupons; "
     "UPI (simulated), wallet and Cash on Delivery.", "done", "high", 0, -12,
     ["+Cart with quantity and save for later", "+Coupon rules", "+Checkout with address", "+Payment options"]),
    (0, "Live order tracking timeline", "Placed → Packed → Shipped → Out for delivery → Delivered.", "done", "medium", 2, -9, []),
    (0, "Admin panel: sales dashboard and product management", "KPIs, 14-day sales chart, orders, products, customers, "
     "coupons, reviews, activity log and database viewer with CSV export.", "done", "high", 0, -6,
     ["+Dashboard", "+Products with image upload", "+Orders and status changes", "+Activity log", "+Database viewer"]),
    (0, "Product image upload with Pillow", "Accept JPG/PNG/WEBP, save as 800×800 JPG, create 3 views.", "done", "medium", 2, -5, []),
    (0, "Write 49 automated unit and integration tests", "Store logic, pages, admin panel.", "done", "high", 5, -3, []),
    (0, "Printable GST invoices", "Invoice page with tax lines that prints on one A4 page.", "review", "medium", 1, 1,
     ["+Invoice template", "+GST calculation", "Print CSS check"]),
    (0, "Mobile layout for checkout", "Checkout steps must fit a 360 px screen.", "in_progress", "medium", 3, 3,
     ["+Address step", "Payment step", "Order summary"]),
    (0, "Deploy demo to PythonAnywhere", "Public demo link for the CV.", "todo", "low", 0, 10, []),
    # ---- PyBank
    (1, "Design Bank and BankAccount classes (OOP)", "Deposit, withdraw, transfer, statement - as in the project synopsis.",
     "done", "high", 0, -18, []),
    (1, "Two-step fund transfer with PIN", "Review screen, then PIN confirmation; atomic database transaction.",
     "done", "urgent", 2, -10, []),
    (1, "Lock login after 5 failed attempts", "Lock for 15 minutes and log the event.", "done", "high", 2, -8, []),
    (1, "Statements with CSV export and printable receipts", "", "done", "medium", 1, -4, []),
    (1, "Write 34 automated tests", "", "done", "high", 5, -2, []),
    (1, "Daily transfer limits and session timeout", "Limit per day per account; sign out after 10 minutes without activity.",
     "in_progress", "high", 2, 2, ["+Daily limit check", "Session timeout", "Tests for limits"]),
    (1, "Bank-style UI with real JPG/PNG images", "Make it look like a real bank website.", "review", "medium", 3, 0, []),
    (1, "Write user guide: how to run the app", "Step by step for Windows: venv, pip install, python app.py.",
     "todo", "low", 0, 6, []),
    # ---- TaskFlow
    (2, "Improve console version: hashed passwords and input checks", "Fix lost edits, crashes on empty files, "
     "division by zero; add PBKDF2 passwords and 19 tests.", "done", "high", 0, -7, []),
    (2, "Kanban board with drag and drop", "HTML5 drag and drop + JSON API with CSRF token.", "done", "high", 1, -3, []),
    (2, "Calendar view of deadlines", "", "done", "medium", 1, -2, []),
    (2, "Admin panel: settings, database viewer, maintenance", "", "done", "high", 0, -1, []),
    (2, "Checklists and comment editing for every user", "Members manage their own projects, tasks, checklists and comments.",
     "in_progress", "high", 0, 1, ["+Subtasks table", "+Edit own comments", "+Owners manage projects", "Tests"]),
    (2, "Publish on GitHub with screenshots (Mujyambere70)", "README, screenshots folder, requirements.txt.",
     "todo", "medium", 0, 2, ["Push code", "Add screenshots", "Pin the repository"]),
    (2, "Accessibility check (keyboard and contrast)", "", "todo", "medium", 5, 5, []),
    (2, "Dark mode", "", "todo", "low", 3, 14, []),
    # ---- SFU Master's
    (3, "Computational statistics — lab work (variant 14)", "Вычислительная статистика: code in Python and R + defense report.",
     "in_progress", "high", 0, 3, ["+Load the dataset", "+Descriptive statistics", "Histogram and box plot",
                                   "Python and R versions", "Bilingual defense report"]),
    (3, "Machine learning basics — lab work (variant 14)", "Основы машинного обучения.", "todo", "high", 0, 6,
     ["Train / test split", "Fit the model", "Accuracy and confusion matrix", "Report with plots explained"]),
    (3, "PostgreSQL practical work — report on e-courses", "Системы хранения, обработки и управления данными.",
     "in_progress", "medium", 0, 2, ["+Create tables", "+Queries with JOIN", "Upload report"]),
    (3, "Data storage — normalisation exercise", "Bring the schema to 3NF.", "todo", "medium", 0, -1, []),
    (3, "Mathematical modelling — lecture assignment", "Математическое моделирование.", "todo", "medium", 0, 8, []),
    (3, "English for professional purposes — Public Speaking test", "Online test on e-courses.", "done", "medium", 0, -5, []),
    (3, "Prepare lab defense: explain every function and plot", "", "todo", "high", 0, 9, []),
    (3, "Read papers on data analysis in entrepreneurship", "For the master's research topic.", "todo", "low", 4, 12, []),
    # ---- Certifications & career
    (4, "Google Advanced Data Analytics certificate", "", "done", "high", 0, -40, []),
    (4, "Google Cybersecurity Professional certificate", "", "done", "medium", 0, -35, []),
    (4, "Google UX Design Professional certificate", "", "done", "medium", 0, -28, []),
    (4, "AWS Cloud Practitioner — finish course modules", "", "in_progress", "high", 0, 12,
     ["+Cloud concepts", "+Security and compliance", "Technology and services", "Billing and pricing"]),
    (4, "AWS Cloud Practitioner — book the exam", "", "todo", "medium", 0, 25, []),
    (4, "Update CV: SFU Master's, Yo Delivery and PyBank", "New education entries and two new projects.", "done", "high", 0, -1, []),
    (4, "Write a LinkedIn post about TaskFlow", "", "todo", "low", 0, 4, []),
    # ---- Python mini projects
    (5, "Chess Match: checkmate validation", "Rules for piece movement and checkmate.", "done", "medium", 0, -25, []),
    (5, "Chess Match: Pygame UI polish", "", "done", "low", 3, -22, []),
    (5, "Finance Calculator: bond repayment formula", "Compound interest and bond repayments.", "done", "medium", 4, -15, []),
    (5, "Password Generator: 12+ characters, custom complexity", "", "done", "low", 1, -11, []),
    # ---- no project
    (None, "Weekly team sync agenda", "Progress, blockers, next steps.", "todo", "low", 0, 1, []),
    (None, "Renew domain and SSL certificate", "Expires at the end of the month.", "todo", "urgent", 2, -3, []),
]

# task title (start), author index, text
COMMENTS = [
    ("Cart, coupons", 1, "Tested WELCOME100 - works only on the first order, as planned."),
    ("Admin panel: sales", 4, "The 14-day sales chart matches my numbers from the database."),
    ("Write 49 automated", 5, "All 49 tests pass on Windows and Linux."),
    ("Two-step fund transfer", 5, "Tried a wrong PIN 3 times - transfer is cancelled correctly."),
    ("Two-step fund transfer", 0, "Great. Please also check the transfer to the same account is blocked."),
    ("Daily transfer limits", 2, "Daily limit is done. Session timeout comes tomorrow."),
    ("Kanban board", 3, "Cards look good. Can the column turn blue when I drag over it?"),
    ("Kanban board", 1, "Done - the column is highlighted now."),
    ("Computational statistics", 0, "Remember: the teacher asks about every function and every plot at the defense."),
    ("Checklists and comment", 5, "I will write the tests for owners vs. other members."),
    ("Bank-style UI", 0, "Use only JPG/PNG pictures, no SVG for photos."),
    ("Mobile layout for checkout", 3, "Address step is ready on 360 px. Working on payment now."),
]


def ts(d, hour=10, minute=0):
    moment = min(datetime(d.year, d.month, d.day, hour, minute), datetime.now() - timedelta(minutes=7))
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def load_demo(store):
    rnd = random.Random(14)
    today = date.today()
    user_ids = [store.create_user(n, e, p, role=r, job_title=j) for n, e, p, r, j in USERS]
    admin = user_ids[0]
    project_ids = [store.save_project(user_ids[o], n, d, c) for n, d, c, o in PROJECTS]

    with store.db() as con:
        con.execute("DELETE FROM activity")            # replace with a realistic history below
        events, task_ids = [], {}
        for i, (p, title, desc, status, prio, who, due_in, checklist) in enumerate(TASKS):
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
            cur = con.execute(
                "INSERT INTO tasks (project_id, title, description, status, priority, assignee_id, created_by, "
                "due_date, created_at, updated_at, completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (project_ids[p] if p is not None else None, title, desc, status, prio, assignee, creator,
                 due.isoformat(), ts(created, 9, rnd.randint(0, 59)), updated, completed))
            tid = cur.lastrowid
            task_ids[title] = tid
            events.append((ts(created, 9, 5), creator, tid, "task_created", title))
            for item in checklist:
                done = item.startswith("+") or status == "done"
                con.execute("INSERT INTO subtasks (task_id, title, done, created_by, created_at) VALUES (?,?,?,?,?)",
                            (tid, item.lstrip("+"), 1 if done else 0, assignee, ts(created, 9, 30)))
            if completed:
                events.append((completed, assignee, tid, "task_completed", "In Review → Done"))
            elif status != "todo":
                events.append((updated, assignee, tid, "status_changed",
                               "To Do → " + {"in_progress": "In Progress", "review": "In Review"}[status]))

        for n, (start, who, body) in enumerate(COMMENTS):
            tid = next(v for k, v in task_ids.items() if k.startswith(start))
            when = ts(today - timedelta(days=rnd.randint(0, 3)), 10 + n % 8, rnd.randint(0, 59))
            con.execute("INSERT INTO comments (task_id, user_id, body, created_at) VALUES (?,?,?,?)",
                        (tid, user_ids[who], body, when))
            events.append((when, user_ids[who], tid, "commented", body[:80]))

        for name, *_ in USERS:
            events.append((ts(today - timedelta(days=45), 9), admin, None, "user_added", f"{name} joined the team"))
        for when, uid, tid, action, details in sorted(events, key=lambda e: e[0]):
            con.execute("INSERT INTO activity (user_id, task_id, action, details, created_at) VALUES (?,?,?,?,?)",
                        (uid, tid, action, details, when))
