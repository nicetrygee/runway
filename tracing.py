"""AI call tracing — one row per LLM call in a separate SQLite file.

traces.db is local and gitignored, kept apart from runway.db so traces never
mix with user data and the file can be deleted at any time. Path comes from
TRACES_DB_PATH (read per call, so tests can point it at a temp file).

Tracing must never break a user request: any failure here is logged and
swallowed.
"""
import json
import logging
import os
import sqlite3

import clock

logger = logging.getLogger(__name__)

DEFAULT_TRACES_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "traces.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    feature TEXT NOT NULL,
    model TEXT,
    model_settings TEXT,
    prompt_version TEXT,
    input TEXT,
    output TEXT,
    raw_request TEXT,
    raw_response TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms REAL,
    tokens_per_second REAL,
    success INTEGER NOT NULL,
    error TEXT,
    hosted INTEGER
)
"""


def _ensure_schema(conn):
    """Create the table, and add columns that older traces.db files lack.
    `hosted` stays NULL ("unknown") on rows written before the guard."""
    conn.execute(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(traces)")}
    if "hosted" not in columns:
        try:
            conn.execute("ALTER TABLE traces ADD COLUMN hosted INTEGER")
        except sqlite3.OperationalError as e:  # another process got there first
            if "duplicate column" not in str(e):
                raise


def traces_db_path():
    return os.environ.get("TRACES_DB_PATH", DEFAULT_TRACES_DB_PATH)


def tokens_per_second(output_tokens, latency_ms):
    if not output_tokens or not latency_ms:
        return None
    return output_tokens / (latency_ms / 1000)


def _json(value):
    return None if value is None else json.dumps(value, default=str)


def record(
    *,
    request_id,
    feature,
    model,
    model_settings,
    prompt_version,
    input,
    output,
    raw_request,
    raw_response,
    input_tokens,
    output_tokens,
    latency_ms,
    success,
    hosted,
    error=None,
):
    """Write one trace row. `input`, `model_settings`, `raw_request` and
    `raw_response` are JSON-encoded; `output` is stored as-is if it's already
    a string (text output), else JSON-encoded (parsed structured output)."""
    row = (
        request_id,
        clock.utc_now().strftime("%Y-%m-%d %H:%M:%S"),
        feature,
        model,
        _json(model_settings),
        prompt_version,
        _json(input),
        output if output is None or isinstance(output, str) else _json(output),
        _json(raw_request),
        _json(raw_response),
        input_tokens,
        output_tokens,
        latency_ms,
        tokens_per_second(output_tokens, latency_ms),
        1 if success else 0,
        error,
        1 if hosted else 0,
    )
    try:
        conn = sqlite3.connect(traces_db_path())
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO traces (request_id, created_at, feature, model, "
                "model_settings, prompt_version, input, output, raw_request, "
                "raw_response, input_tokens, output_tokens, latency_ms, "
                "tokens_per_second, success, error, hosted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to write trace %s", request_id)
