# TaskFlow 2 — Team Task Management System + Resume Builder

A full-stack web application for teams to plan, assign and track work — now with **several workspaces**,
a **live Kanban board (WebSocket)**, **email reminders one day before a deadline**, **file attachments**,
**time tracking**, **burndown charts**, a **resume (CV) builder with a link to share**, and **7 languages**.

Built with **Python, Flask, PostgreSQL / SQLite, Jinja2, WebSocket (flask-sock), Babel, Pillow, HTML, CSS and JavaScript**.

**Live demo:** https://michel-taskflow.fly.dev/ (after deployment, see below) ·
**My public resume made with TaskFlow:** `/r/jean-michel-mujyambere`

> This started as a console program (HyperionDev Software Engineering Bootcamp capstone), was rebuilt as a web app
> (version 1) and extended in version 2. The improved console version is still in `console_version/`.
> **Demo data:** built around my real portfolio — Yo Delivery, PyBank, TaskFlow, CalcWise, my Master's studies at
> Siberian Federal University and my certifications. The teammates and companies are fictional.

## Quick start / Как запустить

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python app.py
```
Open **http://127.0.0.1:5000**. On the first run the file `taskflow.db` is created with demo data.

| Account | Email | Password |
|---|---|---|
| Site admin (Jean) | `admin@taskflow.demo` | `Admin@1234` |
| Members | `anna@`, `david@` (workspace admin), `maria@`, `samuel@`, `elena@`, `li@`, `fatima@`, `lucas@`, `sofia@` + `taskflow.demo` | `Demo@1234` |

Each demo person has a different interface language (Anna — Russian, Fatima — Arabic, Li — Chinese, Samuel — Kinyarwanda …).
Demo addresses (`.demo`) are never emailed.

## What is new in version 2 (the "Future scope" of version 1, done)

| Future scope item | How it works now |
|---|---|
| **Email reminders one day before a deadline** | Every assignee gets a reminder on the site (bell) and by email. Each person can choose 1, 2 or 3 days before, or no reminders. Sent once per due date (a changed due date reminds again). Runs every 10 minutes inside the app, or from an outside scheduler: `GET /cron/run?key=CRON_KEY`. Email through Brevo or SMTP |
| **File attachments on tasks** | Up to 10 files per task, 5 MB each (PDF, pictures, Office, text, CSV, ZIP …). Stored **in the database**, so they survive restarts of free cloud servers. The file type is checked by its first bytes; pictures show as thumbnails; files are served with a strict Content-Security-Policy |
| **Several workspaces for different teams** | Each workspace has its own projects, tasks, members, roles (admin/member), reports and settings. Switch in the sidebar, create new ones, invite people with a **link** (`/join/<code>`) or by email, make a new link, leave, delete. A version 1 database is moved into one workspace automatically |
| **Real-time updates of the board (WebSockets)** | When a teammate moves, creates, comments or attaches a file, open boards refresh at once and other pages show a small message. Works across several servers (events table). If WebSocket is blocked, the page asks the server every few seconds instead |
| **Time tracking and simple burndown charts** | Start/stop timer (one per person; a forgotten timer counts at most 12 h), time typed by hand (`1.5`, `90m`, `1h 30m`, `1:30`), estimates on tasks, time report per person and project, time CSV, workload per member, **burndown chart** by number of tasks or by estimated hours with the ideal line and the scope line |

### Other improvements
- **Resume builder**: every user can make up to 10 resumes — photo (cut to a square), contacts, links, summary and
  11 kinds of sections (experience, education, projects, skills, certificates, languages with level dots, achievements,
  interests, references, free sections, free text). Reorder and hide sections. 3 designs (Modern, Classic, Compact),
  any accent color, 3 fonts, headings in 7 languages (Arabic right to left). **Autosave with live preview**.
  Visibility: only me / anyone with the link / public in **Explore resumes** (search by name, skill, city).
  Share by link, QR code, LinkedIn, WhatsApp, Telegram, email; **print or save as PDF** (A4). Import from the open
  **JSON Resume** format, export to JSON, duplicate. View counter. The site admin can remove a public resume.
- **7 languages** for the whole interface, dates and emails: English, Russian, French, Spanish, Arabic (right to left),
  Chinese, Kinyarwanda. The language follows the browser, can be chosen on every page and is saved in the profile.
  Notifications and emails are written in the language of the person who receives them.
- **PostgreSQL** (Neon) online, SQLite on the laptop — the same code. Data stays after restarts and redeploys.
- **Notifications** under the bell: assigned task, @mention (`@Anna`), comment on your task, deadline reminder, added to a workspace.
- **Password reset by email** (one-time link, 30 minutes) and **two-factor login** (authenticator app, QR code).
- **Site admin** panel: sign-up on/off, service status (database, email, WebSocket, scheduler), database viewer with
  CSV for every table (secrets hidden), full **backup** as one JSON file, send reminders now, clear log, reset demo data.
- **About** page with the developer card and links to the other projects.
- Deployment files for **Fly.io** (`Dockerfile`, `fly.toml` with `min_machines_running = 0`) and **Gunicorn** instead of the Flask development server.

## Screenshots
`screenshots/`: dashboard, live board, task with files and timer, burndown chart, time report, team and workload,
workspace switcher, resume editor, public resume, Explore page, the board in Arabic (right to left), notifications in Russian.

## Roles

| Role | What it can do |
|---|---|
| **Member** of a workspace | Create tasks for anyone in the workspace; edit/move tasks they created or that are assigned to them; comment, attach files, log time on any task; own projects; own resumes |
| **Workspace admin** | Everything in that workspace: all tasks, bulk changes, members and their roles, invite link, workspace settings, cleaning up and deleting the workspace |
| **Site administrator** (first account) | Site settings, database viewer, backup, maintenance, edit/deactivate/delete accounts, reset passwords and two-factor login, remove public resumes |

The server checks every rule (hidden buttons cannot be bypassed). People never see data of workspaces they are not in.

## Security
- Passwords hashed (Werkzeug scrypt/PBKDF2); one-time password-reset links stored only as SHA-256 hashes
- Two-factor login (TOTP, RFC 6238) — a code cannot be used twice; 5 wrong codes end the attempt
- CSRF token on every form and on the JSON / drag-and-drop APIs
- Parameterised SQL (no SQL injection), auto-escaped templates (no XSS), checked resume links (only `https://`, `mailto:`)
- Uploaded files: allow-list of types, first bytes checked, size limits, served with `Content-Security-Policy: default-src 'none'`
- Secrets only in environment variables / Fly secrets — **never in the code or GitHub, never paste them into a chat**

## Deploy on Fly.io + Neon (PostgreSQL)
1. Create a free PostgreSQL database on **neon.tech** and copy its connection string (keep it secret).
2. Install `flyctl`, then in this folder:
   ```bash
   fly launch --no-deploy              # keeps the existing fly.toml (choose your own app name if taken)
   fly secrets set DATABASE_URL="postgresql://...neon.tech/...?sslmode=require" \
                   SECRET_KEY="<a long random text>" APP_URL="https://michel-taskflow.fly.dev"
   fly deploy
   ```
3. Optional secrets: `BREVO_API_KEY` + `ALERT_EMAIL_FROM` (or `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`)
   for real emails; `CRON_KEY` to call `/cron/run?key=…` from cron-job.org.
4. The machine sleeps when nobody visits (`min_machines_running = 0`) and wakes up on the next visit.
   The demo data is created only when the database is empty, so a restart never resets your data.

> If a password or database URL was ever shown in a chat or a screenshot, reset it in Neon and set the new one with `fly secrets set`.

## Tests
```bash
python -m unittest discover -s tests -v                       # SQLite
TEST_DATABASE_URL=postgresql://user@host/test_db python -m unittest discover -s tests   # PostgreSQL (empties that database!)
```
**109 tests**, all passing on SQLite and on PostgreSQL: sign-in, registration and invites, CSRF, tasks, filters,
board API, permissions in and between workspaces, admin tools, workspaces (create, switch, invite, leave, delete,
migration of a version 1 database), files (types, size, limits, access), time tracking and burndown numbers,
reminders (once per date, settings, demo addresses never emailed), notifications and @mentions, live events,
password reset, two-factor login, the resume builder (validation, visibility, photos, import/export, XSS),
and all 7 languages on every page.

## Translations
The pages are written in English; `i18n.py` translates every page after it is made, using `translations/<lang>.json`.
`tools/collect_strings.py` finds all texts (and `--missing --lang=ru` shows untranslated ones);
`tools/build_catalogs.py` builds the catalogs from the tables in `tools/tr/*.tsv`.
The Kinyarwanda texts should be checked by a native speaker.

## Project structure
```
taskflow/
├── app.py             Flask routes: pages, APIs, WebSocket, permissions, CSRF, language
├── resume_views.py    Resume builder pages: my resumes, editor, public page, Explore
├── store.py           Database schema + rules: users, workspaces, projects, tasks, comments, activity, stats
├── files.py           Attachments (stored in the database)
├── timetrack.py       Timer, time entries, time report, burndown chart, workload
├── notices.py         Notifications, emails, deadline reminders, live events, password reset, two-factor login
├── resumes.py         Resume data: sections, validation, photo, JSON Resume import
├── db.py              One database layer for SQLite and PostgreSQL
├── notify.py          Email through Brevo or SMTP;  otp.py  TOTP codes
├── i18n.py, world.py  Translations and dates in 7 languages; translations/*.json
├── demo_data.py       Demo data: 2 workspaces, 7 projects, 45 tasks, time entries, files, resumes
├── templates/         Jinja2 pages (auth/, resume/, public pages)   static/  CSS, JS (app.js, resume-editor.js)
├── tests/             109 automated tests (SQLite or PostgreSQL)
├── tools/             string collector and catalog builder
├── Dockerfile, fly.toml, .dockerignore     deployment on Fly.io
└── console_version/   improved original command-line program
```

## Database (15 tables)
`users` · `workspaces` · `memberships` · `projects` · `tasks` · `subtasks` · `comments` · `attachments` · `time_entries` ·
`notifications` · `events` · `activity` · `password_resets` · `resumes` · `settings`

## Author
**Jean Michel Mujyambere** — Software Engineer · [github.com/Mujyambere70](https://github.com/Mujyambere70) ·
[linkedin.com/in/jeanmichel](https://linkedin.com/in/jeanmichel)
