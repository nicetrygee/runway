-- Runway schema — fresh installs (Slice 0 foundation)
-- Existing DBs: run migrate_slice0.py instead; it applies the same changes additively.
-- Convention: every new enum here is mirrored by a VALID_* list in code and by AGENTS.md.

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    hash TEXT NOT NULL
);

-- Kept named `tasks` for continuity; conceptually these are "items".
-- task_type is retained (legacy, still used by the current UI) and coexists
-- with item_type until Slice A migrates the capture/validation UI over.
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,

    -- legacy taxonomy (unchanged)
    task_type TEXT NOT NULL CHECK(task_type IN ('incident','rfc','1on1','hiring','delivery','other')),
    status TEXT NOT NULL DEFAULT 'backlog' CHECK(status IN ('backlog','in_progress','blocked','done')),
    blast_radius TEXT,
    sprint TEXT,
    cognitive_load INTEGER DEFAULT 1 CHECK(cognitive_load BETWEEN 1 AND 5),
    due_date TEXT,
    notes TEXT,

    -- Slice 0 additions -------------------------------------------------
    -- direction of the item; three of the four EM questions are the same
    -- relationship seen from a different side.
    stream TEXT NOT NULL DEFAULT 'task'
        CHECK(stream IN ('task','commitment','delegation','waiting')),
    -- new EM taxonomy; nullable while task_type still drives the UI.
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
