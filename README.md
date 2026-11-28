# TaskFlow 4 — Team Task Manager with Own Board Columns, Automations, REST API, Webhooks, Calendar Sync and Single Sign-On

A full-stack web application for teams to plan, assign and track work: **several workspaces**, a **live Kanban board
with your own columns and WIP limits**, **custom fields**, **automation rules**, **task templates**, **saved views**,
**sprints**, a **Gantt chart**, a **REST API with personal keys**, **signed webhooks**, **calendar import and sync**,
**sign in with Google or Microsoft**, **dark mode**, a **command palette**, **Web Push**, a **resume builder** with
server-made PDF, **7 languages** — and a **back office where the site administrator manages everything**.

Built with **Python, Flask, PostgreSQL / SQLite, Jinja2, WebSocket (flask-sock), PostgreSQL LISTEN/NOTIFY, OpenID Connect,
AWS Signature V4 (S3), Web Push (cryptography), reportlab, Babel, Pillow, HTML, CSS and JavaScript**.

**Live demo:** https://michel-taskflow.fly.dev/ ·
**My public resume made with TaskFlow:** `/r/jean-michel-mujyambere` (PDF: `/r/jean-michel-mujyambere/resume.pdf`)

> This started as a console program (HyperionDev Software Engineering Bootcamp capstone), was rebuilt as a web app
> (version 1) and extended in versions 2, 3 and 4. The improved console version is still in `console_version/`.
> **Demo data:** built around my real portfolio — Yo Delivery, PyBank, TaskFlow, CalcWise, my Master's studies at
> Siberian Federal University and my certifications. The teammates and companies are fictional.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python app.py
```
Open **http://127.0.0.1:5000**. On the first run the file `taskflow.db` is created with demo data.
A version 2 or 3 database is upgraded automatically (new tables and columns are added, nothing is lost).

| Account | Email | Password |
|---|---|---|
| Site admin (Jean) | `admin@taskflow.demo` | `Admin@1234` |
| Members | `anna@`, `david@` (workspace admin), `maria@`, `samuel@`, `elena@`, `li@`, `fatima@`, `lucas@`, `sofia@` + `taskflow.demo` | `Demo@1234` |

Demo addresses (`.demo`, `.example`) are never emailed.

## What is new in version 4

### The "Limitations" and "Future scope" of the version 3 report — done

| Version 3 report | Version 4 |
|---|---|
| Limit: real emails need a Brevo / SMTP key | Still needed to send — but nothing is lost any more: a failed email or webhook is **tried again after 1, 5, 30 and 120 minutes** (5 tries), the admin can press **Send again**, and messages left "waiting" after a restart are picked up |
| Limit: WebSocket lives in one process; a big site needs Redis | **PostgreSQL LISTEN / NOTIFY** is the message broker (`livebus.py`): a change sends `pg_notify` in the same transaction and every worker and machine wakes its open boards at once. The Dockerfile now runs **2 Gunicorn workers**. A database "claim" makes sure only one worker runs the background jobs |
| Limit: the calendar feed is read-only | **Calendar import** of an `.ics` file and **calendar subscriptions** (Google / Outlook secret iCal address, read every 6 hours). Events from the TaskFlow feed (UID `task-<id>@taskflow`) move the due date of their task; other events become tasks and are updated, not copied, on the next import |
| Limit: push on iPhone needs "Add to Home Screen" | Apple's rule cannot be changed, so there is an **Install the app** page with steps for iPhone/iPad, Android and computers, linked from the profile |
| Limit: the estimate uses only the title | **Estimate v2**: title 60 %, project 15 %, assignee 15 %, labels 10 %, priority 5 %. A **leave-one-out report** compares it with version 3 on your own data (demo: average error 32 % → 23 %, within 25 %: 57 % → 67 %) |
| Limit: files are stored in the database | **S3-compatible storage** (Amazon S3, Cloudflare R2, Backblaze B2, MinIO, Tigris) when `S3_BUCKET` is set; requests signed with **AWS Signature V4** written in Python (checked with the official AWS test values). Without a bucket the files stay in the database |
| Limit: Kinyarwanda needs a native speaker | People **suggest better words** for their language; the site administrator approves or rejects them (Site admin → Suggested words) |
| Future: two-way calendar sync | See calendar import and subscriptions above |
| Future: Redis broker | Not needed: LISTEN / NOTIFY above |
| Future: object storage | S3 above |
| Future: custom fields and custom board columns | **Own board columns** per workspace (rename, add, reorder, each tied to one of the 4 states so reports, sprints and Gantt keep working) with **WIP limits**; **custom fields**: text, number, date, choice, yes/no, optional "required" |
| Future: SSO with Google / Microsoft | **OpenID Connect** with authorization code + **PKCE**, state and nonce; the ID token signature (RS256, provider keys), issuer, audience and expiry are checked; only verified emails; existing accounts are linked by email |
| Future: better estimate model | Estimate v2 above |

### More that was missing — now in version 4
- **Automation rules** — "when a task is created / moved to a state / commented → assign it, set the priority, add a
  label, move it, set a due date or tell somebody", optionally only in one project; pause, run counter; a change made by
  a rule never starts other rules (no endless loops).
- **Task templates** with checklist, labels, priority and estimate; start a new task from a template in one click.
- **Saved views** — save any filtered task list or board; they appear in the sidebar.
- **REST API** `/api/v1` (tasks, projects, comments) with **personal API keys** (read or read + write; only a SHA-256
  fingerprint is kept; 120 requests a minute).
- **Webhooks** for task and comment events, signed with **HMAC-SHA256** (`X-TaskFlow-Signature`), with retries; only
  public https addresses are allowed (the address is checked against private networks again at every call).
- **Weekly digest** email on Monday (overdue tasks and tasks of the next 7 days, in the person's language).
- **Sign-in limits**: 5 wrong passwords for one account or 20 from one address in 15 minutes → wait.
- **Security headers**: Content-Security-Policy, X-Frame-Options, Permissions-Policy, HSTS, `no-store` for private pages.
- **gzip** for pages, JSON, CSS and JS (the board page is about 8 times smaller); new **database indexes**.
- **Dark mode** (light / dark / like the device), **command palette** (Ctrl + K) and **keyboard shortcuts**.
- **Offline page** from the service worker; **/healthz** for the Fly.io health check; **Site admin → System** page.

## Version 3 (still inside version 4)

### The "Future scope" and "Limitations" of the version 2 report

| Version 2 report | Version 3 |
|---|---|
| Future: calendar sync (Google / Outlook) | Private **iCalendar link** per person (`/ical/<token>.ics`): tasks with alarms and sprints; renew the link any time |
| Future: recurring tasks, dependencies, Gantt chart | **Repeat** daily / weekly / every 2 weeks / monthly (31 Jan → 28/29 Feb). **"Waits for"** links, circles refused, a task cannot be finished while it waits. **Gantt chart** (14–120 days, per project) with arrows and today's line |
| Future: sprints, sprint burndown, velocity | **Sprints** planned → active → closed (one active at a time, max 60 days), backlog, close with "move open tasks to …", **sprint burndown** by tasks or hours, **velocity** of the last 8 sprints |
| Future: installable app with push | Web manifest + service worker; **Web Push** (RFC 8291 / 8292) to every browser a person turns on; "send a test" |
| Future: suggested estimate (simple ML) | **Nearest neighbours** on the words of the title (cosine similarity), weighted mean of the real time of the 5 closest finished tasks |
| Future: server PDF of resumes, more designs | **reportlab PDF** in 5 designs (+ **Elegant**, **Timeline**) with Arabic (shaped, right to left) and Chinese text |
| Future: Telegram / Slack messages | Per workspace: **Slack webhook** and **Telegram chat** for new tasks, finished tasks and reminders |
| Limit: emails need a key | Still needed — but every email, push, Slack and Telegram message is kept in an **outbox** with its result; test email button |
| Limit: files max 5 MB | The admin sets the size (1–20 MB) and the number of files per task (1–50) |
| Limit: PDF depends on the browser | Server PDF (above) |
| Limit: no push notifications | Web Push (above) |

### Gaps that were not listed, also solved
- **Trash**: deleted tasks can be restored (creator or admin) and are removed after the days set by the site admin.
- **Labels** with colours, on the list, the board and the task; filters by label and sprint.
- **One search** for tasks, comments, projects, people and labels.
- **Export a workspace** (JSON) and **import tasks from CSV** (new projects and labels are created, up to 500 rows).
- **Profile**: photo, phone, "about me"; a new email needs the present password; **sign out everywhere**; **download my data**.
- A password change or reset now **signs out every old session**.
- Confirmation questions ("Delete this …?") are translated too.

### Back office — the site administrator manages everything (Site admin → 8 tabs)
| Page | What he can do |
|---|---|
| **Site settings** | 17 settings checked by type: site name, logo upload, main colour, announcement bar, footer line, default language, which of the 7 languages are on, sign-up on/off, max workspaces per person, max resumes, file size, files per task, trash days, **maintenance mode** with its own message, email sender name and signature |
| **Accounts** | Search, add (straight into a workspace), open any account: name, email, phone, job, bio, language, colour, reminders, photo, new password, email a reset link, turn off 2FA, sign out everywhere, switch off, give/take the site admin right (never from the last one), add to / remove from workspaces, change roles, all data as JSON, delete (type the email) |
| **His own account** | Everything above on his own account, or on the normal profile page |
| **Workspaces** | Create; open any workspace: settings, invite link, add/remove members, roles, projects, sprints, labels, export, **open it as admin**, delete (type the name) |
| **Resumes** | Change who can see any resume, PDF, delete |
| **Translations** | Change any text of the interface in any of the 6 languages; the gaps `{0}`, `{1}` are checked; used at once |
| **Messages sent** | Outbox of email, push, Slack and Telegram with results; service status; send a test email |
| **Audit log** | Who did what on the whole site, with search |
| **Overview / Database / Maintenance** | Tables, services, read-only data viewer with CSV, backup, **restore a backup** (type RESTORE), send reminders, clear log, reset demo |

## Version 2 (still inside version 4)

| Future scope item | How it works now |
|---|---|
| **Email reminders one day before a deadline** | Every assignee gets a reminder on the site (bell) and by email. Each person can choose 1, 2 or 3 days before, or no reminders. Sent once per due date (a changed due date reminds again). Runs every 10 minutes inside the app, or from an outside scheduler: `GET /cron/run?key=CRON_KEY`. Email through Brevo or SMTP |
| **File attachments on tasks** | Up to 10 files per task, 5 MB each (PDF, pictures, Office, text, CSV, ZIP …). Stored **in the database**, so they survive restarts of free cloud servers. The file type is checked by its first bytes; pictures show as thumbnails; files are served with a strict Content-Security-Policy |
| **Several workspaces for different teams** | Each workspace has its own projects, tasks, members, roles (admin/member), reports and settings. Switch in the sidebar, create new ones, invite people with a **link** (`/join/<code>`) or by email, make a new link, leave, delete. A version 1 database is moved into one workspace automatically |
| **Real-time updates of the board (WebSockets)** | When a teammate moves, creates, comments or attaches a file, open boards refresh at once and other pages show a small message. Works across several servers (events table). If WebSocket is blocked, the page asks the server every few seconds instead |
| **Time tracking and simple burndown charts** | Start/stop timer (one per person; a forgotten timer counts at most 12 h), time typed by hand (`1.5`, `90m`, `1h 30m`, `1:30`), estimates on tasks, time report per person and project, time CSV, workload per member, **burndown chart** by number of tasks or by estimated hours with the ideal line and the scope line |

### Other features of version 2
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
`screenshots/v4/`: board with own columns and WIP limits (light, dark, Arabic), board columns, custom fields,
automations, webhooks, templates, new task from a template, API keys, calendar import, estimate accuracy, command
palette, sign in with Google / Microsoft, suggested words, system page, automations in Russian, phone pages, offline page.
`screenshots/`: version 3 — sprint, Gantt chart, task, labels, board, dashboard, back office, resume PDF, Arabic.

## Roles

| Role | What it can do |
|---|---|
| **Member** of a workspace | Create tasks for anyone in the workspace; edit/move tasks they created or that are assigned to them; labels, start date, repeat, "waits for"; comment, attach files, log time; restore own tasks from the trash; own profile, calendar link, push, resumes; templates, saved views, API keys, calendar import, weekly email, theme, translation suggestions |
| **Workspace admin** | Everything in that workspace: all tasks, bulk changes, sprints, labels, members and roles, invite link, Slack/Telegram, export, CSV import, delete for good, delete the workspace; **board columns, custom fields, automations, webhooks** |
| **Site administrator** | The back office above: settings, every account, workspace and resume, translations, outbox, audit log, backup and restore; can open any workspace as its admin |

The server checks every rule (hidden buttons cannot be bypassed). There is always at least one site administrator.

## Security
- Passwords hashed (Werkzeug scrypt/PBKDF2); password-reset links stored only as SHA-256 hashes
- Two-factor login (TOTP, RFC 6238); session number per account — password change, reset or "sign out everywhere" ends all old sessions
- CSRF token on every form and on the JSON APIs; parameterised SQL; auto-escaped templates
- Uploaded files: allow-list of types, first bytes checked, size limits set by the admin, strict Content-Security-Policy
- Push keys are made by the app and never shown; the calendar link is a long random secret that can be renewed
- **Version 4:** sign-in limits; Content-Security-Policy, X-Frame-Options, Permissions-Policy and HSTS headers; private
  pages are not cached; API keys and webhook secrets are long random values (keys kept only as SHA-256 hashes); webhooks
  and calendar addresses must be public https hosts (no localhost or private network: protection against SSRF);
  Google / Microsoft sign-in checks the token signature, issuer, audience, expiry, nonce and uses PKCE
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
   for real emails; `TELEGRAM_BOT_TOKEN` for Telegram messages; `CRON_KEY` to call `/cron/run?key=…` from cron-job.org.
   Web Push needs no secret (the keys are made by the app and kept in the database). Push needs HTTPS (Fly gives it).
   **Version 4 (optional):** `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` (+ `S3_ENDPOINT`, `S3_REGION`) for files in
   object storage; `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` and/or `MICROSOFT_CLIENT_ID` + `MICROSOFT_CLIENT_SECRET`
   (+ `MICROSOFT_TENANT`) for single sign-on — the redirect address is `https://<your app>/auth/sso/google/callback`
   (or `/microsoft/callback`). Live updates between the 2 workers use the direct Neon host automatically.
4. The machine sleeps when nobody visits (`min_machines_running = 0`) and wakes up on the next visit.
   The demo data is created only when the database is empty, so a restart never resets your data.

> If a password or database URL was ever shown in a chat or a screenshot, reset it in Neon and set the new one with `fly secrets set`.

## Tests
```bash
python -m unittest discover -s tests -v                       # SQLite
TEST_DATABASE_URL=postgresql://user@host/test_db python -m unittest discover -s tests   # PostgreSQL (empties that database!)
```
**249 tests**, all passing on SQLite and on PostgreSQL (version 1: 44, version 2: 65, version 3: 59, version 4: 81).
Version 4 tests: own columns and WIP limits, custom fields and their checks, automations (project rule, paused rule,
no loops), templates, saved views, the REST API (read and write keys, rights, revoked keys, rate limit), webhooks
(HMAC signature, retries with back-off, giving up after 5 tries, private addresses refused), email retries, weekly
digest, calendar import and subscriptions, estimate v2 and its report, S3 storage and the AWS Signature V4 test values,
translation suggestions, sign-in limits, Google sign-in with a fake provider and real RSA keys (bad audience, issuer,
expiry, nonce, signature), security headers, gzip, dark mode, offline page, LISTEN / NOTIFY, one-worker job claim and
upgrade of a real version 3 database (`tests/fixtures/taskflow_v3.sql`). Version 3 tests:
sprints, burndown and velocity, dependencies and circles, repeating tasks, Gantt, labels, search, estimate, trash,
CSV import/export, my data, iCalendar, Slack and Telegram (fake HTTP), Web Push with a real key pair, outbox,
site settings and their checks, languages on/off, maintenance mode, file limits, accounts, sessions, photos,
workspaces, resumes and PDF in every design, own translations, audit log, backup and restore, upgrade of a real
version 2 database (`tests/fixtures/taskflow_v2.sql`).

## Translations
The pages are written in English; `i18n.py` translates every page after it is made, using `translations/<lang>.json`
and then the site administrator's own texts (table `ui_overrides`).
`tools/collect_strings.py` finds all texts (`--missing --lang=ru` shows untranslated ones);
`tools/build_catalogs.py` builds the catalogs from `tools/tr/*.tsv` (about 1,580 texts per language).
Version 4: users suggest better words at **Suggest a translation**; the site administrator approves them.

## Project structure
```
taskflow/
├── app.py               Flask routes: pages, APIs, WebSocket, permissions, CSRF, language, maintenance mode, security headers, gzip
├── v4_store.py          (v4) columns, custom fields, automations, views, templates, API keys, webhooks, retries, digest,
│                        calendar import, estimate v2, S3 files, translation suggestions, sign-in limits, SSO accounts
├── v4_views.py          (v4) their pages, the REST API /api/v1, Google / Microsoft sign-in, /healthz, /install, /offline
├── oidc.py              (v4) OpenID Connect: PKCE, ID token check with RS256 keys
├── s3.py                (v4) S3-compatible storage with AWS Signature Version 4
├── livebus.py           (v4) PostgreSQL LISTEN / NOTIFY between workers and machines
├── planning.py          Labels, sprints, burndown, velocity, dependencies, repeating tasks, Gantt, estimate, search, trash, import/export
├── planning_views.py    Their pages + calendar feed, push, manifest, service worker, server PDF of resumes
├── admin_store.py       Site settings, accounts, workspaces, resumes, interface texts, audit log, backup restore
├── admin_views.py       The 10 pages of the back office (templates/admin/)
├── integrations.py      Outbox, Slack, Telegram, Web Push, iCalendar;  push.py  Web Push encryption (RFC 8291/8292)
├── resume_pdf.py        Resume PDF with reportlab (5 designs, Arabic and Chinese)
├── store.py             Database schema + rules: users, workspaces, projects, tasks, comments, activity, stats
├── files.py, timetrack.py, notices.py, resumes.py, resume_views.py   (version 2 features)
├── db.py                One database layer for SQLite and PostgreSQL
├── i18n.py, world.py    Translations and dates in 7 languages; translations/*.json
├── demo_data.py         Demo data: 2 workspaces, 7 projects, 53 tasks, 2 sprints, 8 labels, 5 board columns, 4 custom
│                        fields, 3 rules, 3 templates, 2 saved views, links, resumes
├── templates/  static/  Jinja2 pages; CSS (light and dark), JS (app.js, v4.js, sw.js, resume-editor.js), PDF fonts (DejaVu)
├── tests/               249 automated tests (SQLite or PostgreSQL)
├── tools/               string collector and catalog builder
├── Dockerfile, fly.toml deployment on Fly.io
└── console_version/     improved original command-line program
```

## Database (33 tables)
`users` · `workspaces` · `memberships` · `projects` · `tasks` · `subtasks` · `comments` · `attachments` · `time_entries` ·
`notifications` · `events` · `activity` · `password_resets` · `resumes` · `settings` ·
version 3: `labels` · `task_labels` · `sprints` · `task_deps` · `outbox` · `push_subs` · `ui_overrides` ·
**version 4:** `board_columns` · `custom_fields` · `field_values` · `automations` · `saved_views` · `task_templates` ·
`api_tokens` · `webhooks` · `translation_suggestions` · `login_attempts` · `calendar_subs`
(new columns: `tasks.column_id`, `tasks.ext_uid`, `outbox.attempts`, `outbox.next_try`, `attachments.storage`,
`attachments.storage_key`, `users.theme`, `users.digest`, `users.digest_at`, `users.sso_provider`, `users.sso_subject`)

## Limitations
- Real emails still need an email service key (Brevo or SMTP); without it they wait in the outbox.
- Live updates between workers need PostgreSQL with a direct (not pooled) connection; with SQLite the app runs as one process.
- Calendar subscriptions are read every 6 hours; changes go back to Google or Outlook only through the calendar feed and
  `.ics` files, not by writing into those calendars with their APIs.
- Google / Microsoft sign-in needs the app to be registered with them (client id and secret).
- Object storage needs a bucket; files saved before it was switched on stay in the database.
- The suggested estimate needs finished tasks with logged time; with few tasks it often cannot guess.

## Author
**Jean Michel Mujyambere** — Software Engineer · [github.com/Mujyambere70](https://github.com/Mujyambere70) ·
[linkedin.com/in/jeanmichel](https://linkedin.com/in/jeanmichel)
