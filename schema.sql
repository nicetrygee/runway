-- Runway schema — the complete current schema, for fresh installs.
-- Existing DBs: run the migrate_*.py scripts instead (see AGENTS.md); together
-- they bring an older db to exactly this schema.
-- Convention: every new enum here is mirrored by a VALID_* list in code and by AGENTS.md.

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    hash TEXT NOT NULL
);

-- Kept named `tasks` for continuity; conceptually these are "items".
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'backlog' CHECK(status IN ('backlog','in_progress','blocked','done')),
    blast_radius TEXT,
    sprint TEXT,
    due_date TEXT,
    notes TEXT,

    -- EM item model ----------------------------------------------------
    -- direction of the item; three of the four EM questions are the same
    -- relationship seen from a different side.
    stream TEXT NOT NULL DEFAULT 'task'
        CHECK(stream IN ('task','commitment','delegation','waiting')),
    -- EM taxonomy. The app requires it on every write, but the column stays
    -- nullable: SQLite can't add NOT NULL to an existing column in place, and
    -- migrated dbs must match this schema.
    item_type TEXT
        CHECK(item_type IS NULL OR item_type IN
            ('People','Delivery','Technical','Stakeholder','Strategy','Hiring','Operational','Personal-admin')),
    priority TEXT NOT NULL DEFAULT 'Normal'
        CHECK(priority IN ('Critical','Important','Normal','Delegate','Ignore')),
    -- canonical minutes: 5/30/60/120/240/480 (labels live in code); nullable = not estimated.
    effort_minutes INTEGER CHECK(effort_minutes IS NULL OR effort_minutes > 0),
    -- reactive vs proactive, for strategic-time protection and the weekly balance.
    mode TEXT NOT NULL DEFAULT 'reactive'
        CHECK(mode IN ('reactive','proactive')),
    -- counterparty: to-whom (commitment) / delegated-to / waited-on.
    person_id INTEGER,
    -- how many people/things this item is holding up (bottleneck signal).
    is_blocking INTEGER NOT NULL DEFAULT 0 CHECK(is_blocking >= 0),
    -- staleness / neglect / follow-up aging; bumped on every write.
    last_touched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- -------------------------------------------------------------------

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(person_id) REFERENCES people(id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_stream ON tasks(stream);
CREATE INDEX IF NOT EXISTS idx_tasks_person_id ON tasks(person_id);

-- Lightweight people record — not an HR system. The join point for
-- commitments, delegation, waiting-on, and the later people-attention view.
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    role TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_people_user_id ON people(user_id);

-- Append-only history. Source of truth for the weekly review, neglect
-- detection, and (later) EM-memory Q&A. Never UPDATE or DELETE these rows.
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    item_id INTEGER,
    person_id INTEGER,
    event_type TEXT NOT NULL
        CHECK(event_type IN
            ('created','status_changed','touched','completed','delegated','followed_up','note_added','interaction')),
    payload TEXT,                       -- JSON string, event-specific
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(item_id) REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY(person_id) REFERENCES people(id)
);

CREATE INDEX IF NOT EXISTS idx_events_user_id ON events(user_id);
CREATE INDEX IF NOT EXISTS idx_events_item_id ON events(item_id);

-- Per-user preferences (weekly-review available_hours, meeting_hours_this_week).
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id),
    UNIQUE(user_id, key)
);

CREATE INDEX IF NOT EXISTS idx_settings_user_id ON settings(user_id);
