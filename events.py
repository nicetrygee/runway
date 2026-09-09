"""Read-side helpers over the append-only events log.

log_event (the write side) lives in db.py so a task write and its event
append happen atomically in one function call, on the same db handle,
without a circular import between db.py and this module. This module only
reads — nothing here ever INSERTs, UPDATEs, or DELETEs.
"""
from db import db


def timeline_for_item(item_id, user_id):
    """All events for one item, newest first."""
    return db.execute(
        "SELECT * FROM events WHERE item_id = ? AND user_id = ? ORDER BY created_at DESC",
        item_id, user_id
    )


def events_between(user_id, start, end):
    """Events for a user within [start, end) (ISO datetime strings)."""
    return db.execute(
        """SELECT * FROM events WHERE user_id = ? AND created_at >= ? AND created_at < ?
           ORDER BY created_at ASC""",
        user_id, start, end
    )


def event_counts(user_id, start, end):
    """Counts of events in [start, end), grouped by event_type."""
    return db.execute(
        """SELECT event_type, COUNT(*) AS count FROM events
           WHERE user_id = ? AND created_at >= ? AND created_at < ?
           GROUP BY event_type""",
        user_id, start, end
    )


def last_followup_for_item(item_id, user_id):
    """Most recent followed_up event for an item, or None. Distinct from
    last_touched_at (which any write bumps) — this is specifically "when
    did I last chase this"."""
    rows = db.execute(
        """SELECT * FROM events WHERE item_id = ? AND user_id = ?
           AND event_type = 'followed_up' ORDER BY created_at DESC LIMIT 1""",
        item_id, user_id
    )
    return rows[0] if rows else None
