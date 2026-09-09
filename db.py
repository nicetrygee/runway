"""All database access lives here — one function per read/write.

Includes log_event: an event append is just another INSERT on the same `db`
handle, so a task write and its event row stay atomic in the same function
call. events.py (read-side helpers) imports `db` from here rather than the
other way around, keeping the import graph acyclic.
"""
import json
import os

from cs50 import SQL

db = SQL(os.environ.get("DATABASE_URL", "sqlite:///runway.db"))


def log_event(user_id, item_id, event_type, payload=None, person_id=None):
    return db.execute(
        """INSERT INTO events (user_id, item_id, person_id, event_type, payload)
           VALUES (?, ?, ?, ?, ?)""",
        user_id, item_id, person_id, event_type,
        json.dumps(payload) if payload is not None else None
    )


def get_user_by_username(username):
    return db.execute("SELECT * FROM users WHERE username = ?", username)


def create_user(username, password_hash):
    return db.execute("INSERT INTO users (username, hash) VALUES (?, ?)",
                       username, password_hash)


def tasks_for_user(user_id):
    return db.execute(
        "SELECT * FROM tasks WHERE user_id = ? ORDER BY cognitive_load DESC, due_date ASC",
        user_id
    )


def get_task(task_id, user_id):
    return db.execute("SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                       task_id, user_id)


def insert_task(user_id, title, task_type, blast_radius, sprint, cognitive_load,
                 due_date, notes, *, stream="task", item_type=None, priority="Normal",
                 effort_minutes=None, mode="reactive", person_id=None):
    item_id = db.execute(
        """INSERT INTO tasks (user_id, title, task_type, blast_radius, sprint,
           cognitive_load, due_date, notes, stream, item_type, priority,
           effort_minutes, mode, person_id, last_touched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        user_id, title, task_type, blast_radius, sprint, cognitive_load, due_date, notes,
        stream, item_type, priority, effort_minutes, mode, person_id
    )
    log_event(user_id, item_id, "created", person_id=person_id)
    return item_id


def get_or_create_person(user_id, name):
    name = name.strip()
    rows = db.execute("SELECT id FROM people WHERE user_id = ? AND name = ?", user_id, name)
    if rows:
        return rows[0]["id"]
    return db.execute("INSERT INTO people (user_id, name) VALUES (?, ?)", user_id, name)


def update_task(task_id, user_id, title, task_type, status, blast_radius, sprint,
                 cognitive_load, due_date, notes):
    db.execute(
        """UPDATE tasks SET title=?, task_type=?, status=?, blast_radius=?,
           sprint=?, cognitive_load=?, due_date=?, notes=?,
           updated_at=CURRENT_TIMESTAMP, last_touched_at=CURRENT_TIMESTAMP
           WHERE id=? AND user_id=?""",
        title, task_type, status, blast_radius, sprint, cognitive_load, due_date, notes,
        task_id, user_id
    )
    log_event(user_id, task_id, "touched")


def delete_task(task_id, user_id):
    db.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", task_id, user_id)


def set_status(task_id, user_id, status):
    db.execute(
        """UPDATE tasks SET status=?, updated_at=CURRENT_TIMESTAMP,
           last_touched_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?""",
        status, task_id, user_id
    )
    log_event(user_id, task_id, "status_changed", payload={"status": status})
    if status == "done":
        log_event(user_id, task_id, "completed")


def completed_since(user_id):
    return db.execute(
        """SELECT * FROM tasks WHERE user_id = ? AND status = 'done'
           AND updated_at >= datetime('now', '-7 days')
           ORDER BY updated_at DESC""",
        user_id
    )
