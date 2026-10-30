"""Seed the synthetic world into a separate lab DB (slice E1b.2, D28).

`world.json` is a fixed, fictional EM's world: the cast, projects, items across
all four streams, and their event history. Every timestamp in it is an offset
in days from an anchor date, so the same `--today` always builds the same DB.

The seeder writes with plain sqlite3 (not db.py, whose handle is bound to
DATABASE_URL at import) through the same schema.sql as runway.db, into a
different file. It refuses to touch any file named runway.db, or any DB that
already holds a user or an item, so it can't seed over real data (D6, D8).
"""
import json
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from werkzeug.security import generate_password_hash

WORLD_PATH = Path(__file__).resolve().parent / "world.json"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema.sql"

SQLITE_PREFIX = "sqlite:///"
REAL_DB_NAME = "runway.db"
STREAMS = ("task", "commitment", "delegation", "waiting")
# Everything happens at 09:00 UTC on its day; only the day offset varies.
EVENT_TIME = time(9, 0)
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class SeedRefused(Exception):
    """The target DB is the real one, or already has data in it."""


def load_world(path: Path = WORLD_PATH) -> dict[str, Any]:
    world: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return world


def db_path_from_url(db_url: str) -> Path:
    if not db_url.startswith(SQLITE_PREFIX):
        raise SeedRefused(f"only {SQLITE_PREFIX}<file> URLs are supported, got {db_url!r}")
    path = Path(db_url[len(SQLITE_PREFIX):])
    if path.name == REAL_DB_NAME:
        raise SeedRefused(f"refusing to seed {path}: {REAL_DB_NAME} is the real DB")
    return path


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if not exists:
        return 0
    count: int = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return count


def _timestamp(anchor: date, offset_days: int) -> str:
    moment = datetime.combine(anchor + timedelta(days=offset_days), EVENT_TIME)
    return moment.strftime(TIMESTAMP_FORMAT)


def _due_date(anchor: date, offset_days: int | None) -> str | None:
    if offset_days is None:
        return None
    return (anchor + timedelta(days=offset_days)).isoformat()


def stream_counts(world: dict[str, Any]) -> dict[str, int]:
    counts = dict.fromkeys(STREAMS, 0)
    for item in world["items"]:
        counts[item["stream"]] += 1
    return counts


def seed(db_url: str, today: date, world: dict[str, Any] | None = None) -> dict[str, int]:
    """Build the world into db_url, anchored on today. Returns counts per stream.

    Raises SeedRefused before writing anything if the target is runway.db or
    already has users or items.
    """
    world = world if world is not None else load_world()
    path = db_path_from_url(db_url)

    conn = sqlite3.connect(path)
    try:
        for table in ("users", "tasks"):
            if _row_count(conn, table):
                raise SeedRefused(f"refusing to seed {path}: it already has rows in {table}")
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        with conn:
            _insert_world(conn, world, today)
    finally:
        conn.close()
    return stream_counts(world)


def _insert_world(conn: sqlite3.Connection, world: dict[str, Any], anchor: date) -> None:
    user = world["user"]
    user_id = conn.execute(
        "INSERT INTO users (username, hash) VALUES (?, ?)",
        (user["username"], generate_password_hash(user["password"])),
    ).lastrowid
    earliest = min(item["created_offset_days"] for item in world["items"])

    person_ids: dict[str, int] = {}
    for person in world["people"]:
        cursor = conn.execute(
            "INSERT INTO people (user_id, name, role, notes, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, person["name"], person["role"],
             f"{person['relationship']} · {person['team']}", _timestamp(anchor, earliest)),
        )
        assert cursor.lastrowid is not None
        person_ids[person["name"]] = cursor.lastrowid

    for item in world["items"]:
        person_id = person_ids[item["person"]] if item["person"] else None
        created = _timestamp(anchor, item["created_offset_days"])
        touched = _timestamp(anchor, item["last_touched_offset_days"])
        item_id = conn.execute(
            """INSERT INTO tasks (user_id, title, status, due_date, notes, stream,
               item_type, priority, effort_minutes, mode, person_id, is_blocking,
               last_touched_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, item["title"], item["status"],
             _due_date(anchor, item["due_offset_days"]), item["notes"], item["stream"],
             item["item_type"], item["priority"], item["effort_minutes"], item["mode"],
             person_id, item["is_blocking"], touched, created, touched),
        ).lastrowid
        _insert_event(conn, user_id, item_id, person_id, "created", None, created)
        for event in item["events"]:
            payload = event.get("payload")
            if event["type"] == "delegated":
                payload = {"stream": "delegation", "person_id": person_id}
            _insert_event(conn, user_id, item_id, person_id, event["type"], payload,
                          _timestamp(anchor, event["offset_days"]))

    for interaction in world["interactions"]:
        _insert_event(conn, user_id, None, person_ids[interaction["person"]], "interaction",
                      {"note": interaction["note"]},
                      _timestamp(anchor, interaction["offset_days"]))


def _insert_event(conn: sqlite3.Connection, user_id: int | None, item_id: int | None,
                  person_id: int | None, event_type: str, payload: dict[str, Any] | None,
                  created_at: str) -> None:
    conn.execute(
        """INSERT INTO events (user_id, item_id, person_id, event_type, payload, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, item_id, person_id, event_type,
         json.dumps(payload) if payload is not None else None, created_at),
    )
