# TaskFlow — Team Task Management System

A full-stack web application for teams to plan, assign and track work: Kanban board with drag and drop,
task list with filters, calendar, projects, comments, team management, live reports and an activity log.

Built with **Python, Flask, SQLite, Jinja2, HTML, CSS and JavaScript**. No other dependencies.

> This started as a console program (HyperionDev Software Engineering Bootcamp capstone) and was rebuilt as a web app.
> The improved console version is still in `console_version/`.
> **Demo data:** built around my real portfolio — my projects Yo Delivery, PyBank, TaskFlow and the Python mini projects
> (chess game, finance calculator, password generator), my Master's studies at Siberian Federal University and my
> certifications (Google Data Analytics, Cybersecurity, UX Design, AWS Cloud Practitioner). The teammates are fictional.

## Quick start / Как запустить

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python app.py
```
Open **http://127.0.0.1:5000**. On the first run the database `taskflow.db` is created with a demo team.

| Account | Email | Password |
|---|---|---|
| Admin | `admin@taskflow.demo` | `Admin@1234` |
| Member | `anna@taskflow.demo` (also david, maria, samuel, elena) | `Demo@1234` |

To start again with fresh demo data: stop the app, delete `taskflow.db`, run `python app.py`.

## Features

| Page | What you can do |
|---|---|
| **Dashboard** | Greeting with your open / overdue / due-today numbers, KPI cards, your tasks (overdue first), team activity feed, tasks-by-status chart, completed-per-day chart, project progress |
| **Board** | Kanban columns *To Do → In Progress → In Review → Done*; **drag and drop** cards (saved instantly), filter by project, person, priority, text |
| **All tasks / My tasks** | Table with status tabs, search, filters (person, project, priority, overdue / today / next 7 days), 5 sort options, one-click "mark done", **CSV export** |
| **Task page** | Status switcher, description, **comments**, assignee, due date, project, creator, full change history |
| **Create / edit task** | Title, description, project, assignee, status, priority (Low / Medium / High / Urgent), due date — all validated |
| **Calendar** | Month view of all deadlines, overdue tasks in red, completed tasks crossed out |
| **Projects** | Project cards with progress bars; admin can create, edit (name, description, color) and archive |
| **Team** | Each person's assigned / done / overdue tasks; admin can add members, change roles, reset passwords, deactivate accounts |
| **Reports** | Task overview and user overview (same numbers as the original console reports), charts, full activity log, print and CSV |
| **Profile** | Change your name, email, job title, avatar color and password (admins too) |
| **Admin panel** | Workspace settings (name, open/closed sign-up, default priority), read-only **database viewer** for every table with **CSV backup**, maintenance (clear activity log, remove old completed tasks, reset to demo data) |

### What the admin can manage
| Area | Add | Change | Remove |
|---|---|---|---|
| Tasks | create for anyone | edit any task, change status, **bulk change** status / priority / assignee | delete any task, **bulk delete**, clean up old completed tasks |
| Projects | create | name, description, color, archive / restore | delete (keep the tasks or delete them too) |
| People | add member with temporary password | name, email, job title, color, role (admin/member), reset password, activate / deactivate | delete account (their tasks become unassigned) |
| Comments | write | — | delete any comment (members only their own) |
| Own account | — | name, email, job title, color, password (Profile or Admin panel → My personal details) | — |
| Workspace | — | name, sign-up on/off, default priority | clear activity log, reset all data |

Safety rules: an admin cannot delete, deactivate or demote himself (so there is always an admin); every admin action is written to the activity log; the database viewer never shows password hashes.

**Roles:** the first registered user becomes admin. Admins can change everything (see below).

### What every user can do with their own things
| Thing | Create | Change | Delete |
|---|---|---|---|
| Tasks | for anyone | tasks they created or that are assigned to them (title, status, priority, due date, person, project, drag on board) | tasks they created |
| Projects | yes - they become the **owner** | their own projects: name, description, color, archive | their own projects (keep the tasks or delete them too) |
| Checklist items | inside tasks they can edit | tick / untick (no page reload), rename | yes |
| Comments | on any task | their own comments (shown as "edited") | their own comments |
| Account | sign up | name, email, job title, avatar color, password | delete own account (password needed) |

Pages for this: **Created by me** in the menu, the **Profile** page (my tasks, my projects, details, password, delete account)
and the filter *Created by* on the task list. The server checks every rule, so hidden buttons cannot be bypassed.

## Security
- Passwords hashed with Werkzeug (PBKDF2/scrypt) — never stored as text
- CSRF token on every form and on the drag-and-drop API
- Parameterised SQL (no SQL injection), auto-escaped templates (no XSS)
- Permission checks on the server for every change (not only hidden buttons)
- Safe redirects after login, HTTP-only session cookie, deactivated users are signed out
- Every important action is written to the activity log

## Tests
```bash
python -m unittest discover -s tests -v
```
46 tests: login/logout, registration rules, CSRF, creating/editing/deleting tasks, validation, filters,
drag-and-drop API, comments, member vs admin permissions, team management, projects, profile, reports and CSV, and all admin tools
(edit/delete users, delete projects and comments, bulk actions, settings, database viewer, maintenance),
and what members can do with their own projects, tasks, checklists, comments and account.

## Project structure
```
taskflow/
├── app.py            Flask routes (pages, API, permissions, CSRF)
├── store.py          SQLite database + business rules (users, projects, tasks, comments, activity, stats)
├── demo_data.py      Demo data from my portfolio: 6 projects, 47 tasks, checklists, comments (dates relative to today)
├── templates/        Jinja2 pages: base layout, dashboard, board, tasks, task detail/form,
│                     calendar, projects, team, reports, profile, admin, auth/ (login, register)
├── static/           style.css (responsive design), app.js (drag & drop, dialogs, toasts), img/
├── tests/test_app.py automated tests
└── console_version/  improved original command-line program
```

## Database
`users` · `projects` · `tasks` (status, priority, assignee, due date, completed time) · `subtasks` (checklists) · `comments` · `activity` · `settings`.
Open `taskflow.db` with **DB Browser for SQLite** to see the data.

## Author
**Mujyambere Jean Michel** — [github.com/Mujyambere70](https://github.com/Mujyambere70)
