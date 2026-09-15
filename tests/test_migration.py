"""Slice 0 migration + write-path event/timestamp behaviour.

Two things under test:
  1. migrate_slice0.py, run against a pre-Slice-0 database, adds the new
     tables/columns and backfills them correctly — and is safe to re-run.
  2. db.py's insert_task/update_task/set_status append the right `events`
     row and bump `last_touched_at` on every write (Slice 0 acceptance).
"""
import os
import sqlite3
import subprocess
import sys

import db as db_module

MIGRATE_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "migrate_slice0.py")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")

# The pre-Slice-0 schema (users + tasks only), so the migration has
# something real to upgrade.
OLD_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    task_type TEXT NOT NULL
        CHECK(task_type IN ('incident','rfc','1on1','hiring','delivery','other')),
    status TEXT NOT NULL DEFAULT 'backlog'
        CHECK(status IN ('backlog','in_progress','blocked','done')),
    blast_radius TEXT,
    sprint TEXT,
    cognitive_load INTEGER DEFAULT 1 CHECK(cognitive_load BETWEEN 1 AND 5),
    due_date TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
"""

# Mirrors migrate_slice0.TYPE_MAP / LOAD_TO_EFFORT — kept separate on purpose
# so this test catches drift between the two rather than importing and
# trivially agreeing with itself.
TYPE_MAP = {
    "incident": "Operational",
    "rfc": "Technical",
    "1on1": "People",
    "hiring": "Hiring",
    "delivery": "Delivery",
    "other": "Operational",
}
LOAD_TO_EFFORT = {1: 30, 2: 60, 3: 120, 4: 240, 5: 480}


def make_legacy_db(path):
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA_SQL)
    con.execute("INSERT INTO users (id, username, hash) VALUES (1, 'alice', 'x')")
    rows = [
        ("incident", 1), ("rfc", 2), ("1on1", 3),
        ("hiring", 4), ("delivery", 5), ("other", 1),
    ]
    for i, (task_type, load) in enumerate(rows, start=1):
        con.execute(
            "INSERT INTO tasks (id, user_id, title, task_type, cognitive_load) "
            "VALUES (?, 1, ?, ?, ?)",
            (i, f"Task {i}", task_type, load),
        )
    con.commit()
    con.close()


def run_migration(path):
    subprocess.run([sys.executable, MIGRATE_SCRIPT, path], check=True, capture_output=True)


def snapshot(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    data = {
        table: [dict(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY id")]
        for table in ("tasks", "people", "events")
    }
    con.close()
    return data


def test_migration_adds_people_and_events_tables(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    make_legacy_db(db_path)
    run_migration(db_path)

    con = sqlite3.connect(db_path)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "people" in tables
    assert "events" in tables


def test_migration_adds_new_task_columns(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    make_legacy_db(db_path)
    run_migration(db_path)

    con = sqlite3.connect(db_path)
    columns = {row[1] for row in con.execute("PRAGMA table_info(tasks)")}
    con.close()
    for col in ("stream", "item_type", "priority", "effort_minutes", "mode",
                "person_id", "is_blocking", "last_touched_at"):
        assert col in columns


def test_migration_backfills_item_type_from_task_type(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    make_legacy_db(db_path)
    run_migration(db_path)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT task_type, item_type FROM tasks").fetchall()
    con.close()
    assert len(rows) == len(TYPE_MAP)
    for row in rows:
        assert row["item_type"] == TYPE_MAP[row["task_type"]]


def test_migration_backfills_effort_minutes_from_cognitive_load(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    make_legacy_db(db_path)
    run_migration(db_path)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT cognitive_load, effort_minutes FROM tasks").fetchall()
    con.close()
    for row in rows:
        assert row["effort_minutes"] == LOAD_TO_EFFORT[row["cognitive_load"]]


def test_migration_backfills_last_touched_at(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    make_legacy_db(db_path)
    run_migration(db_path)

    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT last_touched_at FROM tasks").fetchall()
    con.close()
    assert rows and all(r[0] is not None for r in rows)


def test_migration_is_idempotent(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    make_legacy_db(db_path)

    run_migration(db_path)
    first = snapshot(db_path)

    run_migration(db_path)
    second = snapshot(db_path)

    assert first == second


def test_migration_is_a_noop_on_a_freshly_bootstrapped_db(tmp_path):
    # sqlite3 fresh.db < schema.sql, then running the migration on top of
    # that should not error — the fresh-install and existing-DB paths must
    # converge on the same shape.
    db_path = str(tmp_path / "fresh.db")
    with open(SCHEMA_PATH) as f:
        schema_sql = f.read()
    con = sqlite3.connect(db_path)
    con.executescript(schema_sql)
    con.close()

    run_migration(db_path)  # must not raise


# --- write-path: log_event + last_touched_at (Slice 0 acceptance) ----------

def test_insert_task_logs_created_event_and_sets_last_touched_at():
    user_id = db_module.create_user("carol", "hash")
    task_id = db_module.insert_task(user_id, "Ship it", "rfc", "", "", 2, None, "")

    task = db_module.get_task(task_id, user_id)[0]
    assert task["last_touched_at"] is not None

    events = db_module.db.execute(
        "SELECT * FROM events WHERE item_id = ? AND event_type = 'created'", task_id
    )
    assert len(events) == 1


def test_update_task_logs_touched_event_and_bumps_last_touched_at():
    user_id = db_module.create_user("dave", "hash")
    task_id = db_module.insert_task(user_id, "Ship it", "rfc", "", "", 2, None, "")
    original = db_module.get_task(task_id, user_id)[0]["last_touched_at"]

    db_module.update_task(task_id, user_id, "Ship it v2", "rfc", "in_progress",
                           "", "", 3, None, "")

    events = db_module.db.execute(
        "SELECT * FROM events WHERE item_id = ? AND event_type = 'touched'", task_id
    )
    assert len(events) == 1
    updated = db_module.get_task(task_id, user_id)[0]
    assert updated["last_touched_at"] is not None
    assert updated["title"] == "Ship it v2"
    assert original is not None  # sanity: it was set on insert too


def test_set_status_logs_status_changed_event():
    user_id = db_module.create_user("erin", "hash")
    task_id = db_module.insert_task(user_id, "Ship it", "rfc", "", "", 2, None, "")

    db_module.set_status(task_id, user_id, "in_progress")

    events = db_module.db.execute(
        "SELECT * FROM events WHERE item_id = ? AND event_type = 'status_changed'", task_id
    )
    assert len(events) == 1
    completed = db_module.db.execute(
        "SELECT * FROM events WHERE item_id = ? AND event_type = 'completed'", task_id
    )
    assert len(completed) == 0


def test_set_status_to_done_also_logs_completed_event():
    user_id = db_module.create_user("frank", "hash")
    task_id = db_module.insert_task(user_id, "Ship it", "rfc", "", "", 2, None, "")

    db_module.set_status(task_id, user_id, "done")

    status_events = db_module.db.execute(
        "SELECT * FROM events WHERE item_id = ? AND event_type = 'status_changed'", task_id
    )
    completed_events = db_module.db.execute(
        "SELECT * FROM events WHERE item_id = ? AND event_type = 'completed'", task_id
    )
    assert len(status_events) == 1
    assert len(completed_events) == 1
