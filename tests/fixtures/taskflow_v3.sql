BEGIN TRANSACTION;
CREATE TABLE activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    task_id INTEGER,
    action TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
, workspace_id INTEGER);
INSERT INTO "activity" VALUES(1,1,NULL,'user_added','Old Person joined','2026-11-28 09:08:09',NULL);
INSERT INTO "activity" VALUES(2,1,NULL,'workspace_created','Old''s workspace','2026-11-28 09:08:09',1);
INSERT INTO "activity" VALUES(3,1,1,'task_created','Task from version 3','2026-11-28 09:08:09',1);
CREATE TABLE attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL,
    user_id INTEGER REFERENCES users(id),
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    edited_at TEXT
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER,
    to_user INTEGER,
    user_id INTEGER,
    kind TEXT NOT NULL,
    task_id INTEGER,
    text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
INSERT INTO "events" VALUES(1,1,NULL,1,'task_created',1,'Old Person created “Task from version 3”','2026-11-28 09:08:09');
CREATE TABLE labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, user_id)
);
INSERT INTO "memberships" VALUES(1,1,1,'admin','2026-11-28 09:08:09');
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id INTEGER,
    task_id INTEGER,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0,
    emailed TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'waiting',
    created_at TEXT NOT NULL
);
CREATE TABLE password_resets (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
, workspace_id INTEGER);
CREATE TABLE push_subs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE resumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    theme TEXT NOT NULL DEFAULT 'modern',
    accent TEXT NOT NULL DEFAULT '#4f46e5',
    font TEXT NOT NULL DEFAULT 'inter',
    lang TEXT NOT NULL DEFAULT 'en',
    visibility TEXT NOT NULL DEFAULT 'private',
    data TEXT NOT NULL,
    photo TEXT NOT NULL DEFAULT '',
    views INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE sprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE TABLE subtasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE task_deps (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    blocked_by INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, blocked_by)
);
CREATE TABLE task_labels (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    label_id INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, label_id)
);
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo',
    priority TEXT NOT NULL DEFAULT 'medium',
    assignee_id INTEGER REFERENCES users(id),
    created_by INTEGER NOT NULL REFERENCES users(id),
    due_date TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
, workspace_id INTEGER, estimate_minutes INTEGER, reminded_for TEXT NOT NULL DEFAULT '', start_date TEXT, sprint_id INTEGER, repeat TEXT NOT NULL DEFAULT '', repeat_parent INTEGER, deleted_at TEXT, deleted_by INTEGER);
INSERT INTO "tasks" VALUES(1,NULL,'Task from version 3','','review','high',NULL,1,NULL,'2026-11-28 09:08:09','2026-11-28 09:08:09',NULL,1,NULL,'',NULL,NULL,'',NULL,NULL,NULL);
CREATE TABLE time_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    minutes INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE ui_overrides (
    lang TEXT NOT NULL,
    english TEXT NOT NULL,
    text TEXT NOT NULL,
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (lang, english)
);
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    job_title TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
, lang TEXT NOT NULL DEFAULT 'en', notify_email INTEGER NOT NULL DEFAULT 1, remind_days INTEGER NOT NULL DEFAULT 1, totp_secret TEXT NOT NULL DEFAULT '', totp_enabled INTEGER NOT NULL DEFAULT 0, totp_last_step INTEGER NOT NULL DEFAULT 0, last_ws INTEGER, phone TEXT NOT NULL DEFAULT '', bio TEXT NOT NULL DEFAULT '', photo TEXT NOT NULL DEFAULT '', session_ver INTEGER NOT NULL DEFAULT 1, ical_token TEXT NOT NULL DEFAULT '', notify_push INTEGER NOT NULL DEFAULT 1);
INSERT INTO "users" VALUES(1,'Old Person','old3@teamtest.example','scrypt:32768:8:1$2nfm5sEE9gX3JMpL$947bc53f7641bf513cb20645e65eeada4f5f2d62c54a3b98d33ac1108036f155b8283a7365abb45f13462854a910fb307d8f650a0722b19a1ba294b445e41df6','admin','','#4f46e5',1,'2026-11-28 09:08:09','en',1,1,'',0,0,1,'','','',1,'',1);
CREATE TABLE workspaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#4f46e5',
    invite_code TEXT NOT NULL DEFAULT '',
    default_priority TEXT NOT NULL DEFAULT 'medium',
    created_by INTEGER,
    created_at TEXT NOT NULL
, slack_url TEXT NOT NULL DEFAULT '', telegram_chat TEXT NOT NULL DEFAULT '');
INSERT INTO "workspaces" VALUES(1,'Old''s workspace','','#4f46e5','ukycfdCBfIis','medium',1,'2026-11-28 09:08:09','','');
CREATE INDEX idx_tasks_assignee ON tasks(assignee_id);
CREATE INDEX idx_labels_ws ON labels(workspace_id);
CREATE INDEX idx_sprints_ws ON sprints(workspace_id);
CREATE INDEX idx_push_user ON push_subs(user_id);
CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_activity_time ON activity(created_at);
CREATE INDEX idx_memberships_user ON memberships(user_id);
CREATE INDEX idx_time_task ON time_entries(task_id);
CREATE INDEX idx_notifications_user ON notifications(user_id);
CREATE INDEX idx_events_ws ON events(workspace_id);
CREATE INDEX idx_attachments_task ON attachments(task_id);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('users',1);
INSERT INTO "sqlite_sequence" VALUES('activity',3);
INSERT INTO "sqlite_sequence" VALUES('workspaces',1);
INSERT INTO "sqlite_sequence" VALUES('memberships',1);
INSERT INTO "sqlite_sequence" VALUES('tasks',1);
INSERT INTO "sqlite_sequence" VALUES('events',1);
COMMIT;