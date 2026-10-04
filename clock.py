"""The app's one source of "now".

SQLite's CURRENT_TIMESTAMP writes naive UTC ('YYYY-MM-DD HH:MM:SS'), so every
timestamp in the DB is UTC. Anything compared against those columns — week
bounds, staleness, due-date urgency — must use the same clock, or results
shift by the server's UTC offset (~10h in Sydney). Call utc_now() instead of
datetime.now().
"""
from datetime import datetime, timezone


def utc_now() -> datetime:
    """Current time as a naive UTC datetime, matching CURRENT_TIMESTAMP."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
