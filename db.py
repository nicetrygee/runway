"""All database access lives here — one function per read/write.

Includes log_event: an event append is just another INSERT on the same `db`
handle, so a task write and its event row stay atomic in the same function
call. events.py (read-side helpers) imports `db` from here rather than the
other way around, keeping the import graph acyclic. recommend.py must stay
DB-free, so the row -> recommend.Item mapping (Slice B) lives here instead.
"""
import json
import os
from datetime import date, datetime

from cs50 import SQL

from recommend import Item

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


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def candidate_items_for_user(user_id):
    """Rows in the user's own court: Slice B's recommend() candidate set.

    Mirrors recommend.py's candidate filter at the SQL layer for efficiency;
    recommend() re-filters defensively so it stays correct standalone.
    """
    rows = db.execute(
        """SELECT * FROM tasks WHERE user_id = ?
           AND stream IN ('task', 'commitment')
           AND status != 'done'
           AND priority != 'Ignore'""",
        user_id
    )
    return [
        Item(
            id=row["id"],
            title=row["title"],
            stream=row["stream"],
            priority=row["priority"],
            status=row["status"],
            effort_minutes=row["effort_minutes"],
            due_date=_parse_date(row["due_date"]),
            is_blocking=row["is_blocking"],
            mode=row["mode"],
            item_type=row["item_type"],
            last_touched_at=_parse_datetime(row["last_touched_at"]),
        )
        for row in rows
    ]


def completed_since(user_id):
    return db.execute(
        """SELECT * FROM tasks WHERE user_id = ? AND status = 'done'
           AND updated_at >= datetime('now', '-7 days')
           ORDER BY updated_at DESC""",
        user_id
    )


# --- Slice C: relationship surfaces (commitments / delegated / waiting) ---

def items_by_stream(user_id, stream):
    """Items in one stream, with the counterparty's name joined in.

    Stalest/most-urgent first: items with a due date sort by that date
    (earliest first), items without one fall to the back sorted by how
    long they've gone untouched.
    """
    return db.execute(
        """SELECT tasks.*, people.name AS person_name
           FROM tasks LEFT JOIN people ON tasks.person_id = people.id
           WHERE tasks.user_id = ? AND tasks.stream = ?
           ORDER BY tasks.due_date IS NULL, tasks.due_date ASC, tasks.last_touched_at ASC""",
        user_id, stream
    )


def completed_between(user_id, start, end):
    """Tasks marked done within [start, end) — used by the weekly review's
    calendar-week window (completed_since above is a rolling 7 days,
    used by /summary)."""
    return db.execute(
        """SELECT * FROM tasks WHERE user_id = ? AND status = 'done'
           AND updated_at >= ? AND updated_at < ?
           ORDER BY updated_at DESC""",
        user_id, start, end
    )


def open_items(user_id):
    """Tasks not yet done — the EM's outstanding load."""
    return db.execute(
        "SELECT * FROM tasks WHERE user_id = ? AND status != 'done' ORDER BY due_date ASC",
        user_id
    )


def people_for_user(user_id):
    return db.execute(
        "SELECT * FROM people WHERE user_id = ? ORDER BY name ASC", user_id
    )


def get_person(person_id, user_id):
    return db.execute(
        "SELECT * FROM people WHERE id = ? AND user_id = ?", person_id, user_id
    )


def create_person(user_id, name, role, notes):
    return db.execute(
        "INSERT INTO people (user_id, name, role, notes) VALUES (?, ?, ?, ?)",
        user_id, name, role, notes
    )


def open_items_for_person(person_id, user_id):
    """All non-done items involving this person, across every stream."""
    return db.execute(
        """SELECT * FROM tasks WHERE person_id = ? AND user_id = ? AND status != 'done'
           ORDER BY due_date IS NULL, due_date ASC, last_touched_at ASC""",
        person_id, user_id
    )


def follow_up(item_id, user_id):
    """Log that the user chased this item: bumps last_touched_at and
    appends a followed_up event, same shape as set_status."""
    db.execute(
        """UPDATE tasks SET last_touched_at=CURRENT_TIMESTAMP
           WHERE id=? AND user_id=?""",
        item_id, user_id
    )
    log_event(user_id, item_id, "followed_up")


def set_relationship(item_id, user_id, stream, person_id):
    """Move an existing item onto a different stream and/or counterparty.

    Kept separate from update_task so that function's existing positional
    signature (and the tests calling it) doesn't have to change.
    """
    db.execute(
        """UPDATE tasks SET stream=?, person_id=?, updated_at=CURRENT_TIMESTAMP,
           last_touched_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?""",
        stream, person_id, item_id, user_id
    )
    event_type = "delegated" if stream == "delegation" else "touched"
    log_event(user_id, item_id, event_type,
              payload={"stream": stream, "person_id": person_id}, person_id=person_id)


def carry_forward_item(item_id, user_id):
    """Mark an open item as explicitly carried into next week: a log-only
    reset (no status/due_date change) via a 'touched' event tagged with a
    carry_forward payload marker, since 'carry_forward' isn't one of
    events.event_type's fixed CHECK values."""
    db.execute(
        """UPDATE tasks SET last_touched_at = CURRENT_TIMESTAMP
           WHERE id = ? AND user_id = ?""",
        item_id, user_id
    )
    log_event(user_id, item_id, "touched", payload={"action": "carry_forward"})


def get_setting(user_id, key, default=None):
    rows = db.execute(
        "SELECT value FROM settings WHERE user_id = ? AND key = ?", user_id, key
    )
    return rows[0]["value"] if rows else default


def set_setting(user_id, key, value):
    db.execute(
        """INSERT INTO settings (user_id, key, value, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(user_id, key) DO UPDATE SET
               value = excluded.value, updated_at = excluded.updated_at""",
        user_id, key, value
    )
