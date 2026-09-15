"""Idempotent Slice 0 migration for an existing runway.db.

Additive only: adds the new `tasks` columns, creates `people` and `events`,
backfills sane defaults, and adds indexes. Safe to run repeatedly.

Usage:
    python migrate_slice0.py                 # uses ./runway.db (or $DATABASE_URL)
    python migrate_slice0.py path/to/x.db

Does NOT drop or rename anything. `task_type` is kept and coexists with the
new `item_type`; the current UI keeps working until Slice A migrates it.
"""
import os
import sqlite3
import sys


def db_path_from_env_or_arg():
    if len(sys.argv) > 1:
        return sys.argv[1]
    url = os.environ.get("DATABASE_URL", "sqlite:///runway.db")
    return url.replace("sqlite:///", "", 1) if url.startswith("sqlite:///") else url


# column name -> DDL fragment added via ALTER TABLE tasks ADD COLUMN <frag>
NEW_TASK_COLUMNS = {
    "stream": "stream TEXT NOT NULL DEFAULT 'task' "
              "CHECK(stream IN ('task','commitment','delegation','waiting'))",
    "item_type": "item_type TEXT CHECK(item_type IS NULL OR item_type IN "
                 "('People','Delivery','Technical','Stakeholder','Strategy',"
                 "'Hiring','Operational','Personal-admin'))",
    "priority": "priority TEXT NOT NULL DEFAULT 'Normal' "
                "CHECK(priority IN ('Critical','Important','Normal','Delegate','Ignore'))",
    "effort_minutes": "effort_minutes INTEGER CHECK(effort_minutes IS NULL OR effort_minutes > 0)",
    "mode": "mode TEXT NOT NULL DEFAULT 'reactive' CHECK(mode IN ('reactive','proactive'))",
    "person_id": "person_id INTEGER REFERENCES people(id)",
    "is_blocking": "is_blocking INTEGER NOT NULL DEFAULT 0 CHECK(is_blocking >= 0)",
    "last_touched_at": "last_touched_at TIMESTAMP",
}

# legacy task_type -> new item_type
TYPE_MAP = {
    "incident": "Operational",
    "rfc": "Technical",
    "1on1": "People",
    "hiring": "Hiring",
    "delivery": "Delivery",
    "other": "Operational",
}

# rough seed: cognitive_load (1-5) -> effort_minutes; users re-estimate later
LOAD_TO_EFFORT = {1: 30, 2: 60, 3: 120, 4: 240, 5: 480}


def existing_columns(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def main():
    path = db_path_from_env_or_arg()
    print(f"Migrating {path} ...")
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()

    # people + events first (tasks.person_id FK references people)
    cur.executescript(
        """
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

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            item_id INTEGER,
            person_id INTEGER,
            event_type TEXT NOT NULL CHECK(event_type IN
                ('created','status_changed','touched','completed','delegated',
                 'followed_up','note_added','interaction')),
            payload TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(item_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(person_id) REFERENCES people(id)
        );
        CREATE INDEX IF NOT EXISTS idx_events_user_id ON events(user_id);
        CREATE INDEX IF NOT EXISTS idx_events_item_id ON events(item_id);
        """
    )

    have = existing_columns(cur, "tasks")
    added = []
    for name, ddl in NEW_TASK_COLUMNS.items():
        if name not in have:
            # SQLite cannot ADD a NOT NULL column without a default; all our
            # NOT NULL additions carry one, so this is safe on populated tables.
            cur.execute(f"ALTER TABLE tasks ADD COLUMN {ddl}")
            added.append(name)

    # Backfills (only touch rows that still have the old/empty value)
    cur.execute(
        "UPDATE tasks SET last_touched_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) "
        "WHERE last_touched_at IS NULL"
    )
    cur.execute("UPDATE tasks SET item_type = NULL WHERE item_type = ''")
    for legacy, new in TYPE_MAP.items():
        cur.execute("UPDATE tasks SET item_type = ? WHERE item_type IS NULL AND task_type = ?",
                    (new, legacy))
    for load, mins in LOAD_TO_EFFORT.items():
        cur.execute(
            "UPDATE tasks SET effort_minutes = ? "
            "WHERE effort_minutes IS NULL AND cognitive_load = ?",
            (mins, load)
        )

    cur.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_tasks_stream ON tasks(stream);
        CREATE INDEX IF NOT EXISTS idx_tasks_person_id ON tasks(person_id);
        """
    )

    con.commit()
    con.close()
    print(f"  columns added: {added or 'none (already present)'}")
    print("  people/events ensured; backfill applied. Done.")


if __name__ == "__main__":
    main()
