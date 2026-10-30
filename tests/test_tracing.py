import json
import os
import sqlite3
from datetime import datetime

import pytest
from conftest import TRACES_DB_PATH, read_traces
from pydantic import BaseModel

import llm
import tracing

MESSAGES = [{"role": "user", "content": "hi"}]


class Answer(BaseModel):
    city: str


def test_successful_call_writes_one_fully_populated_row(fake_llm):
    fake_llm.text = '<think>x</think>{"city": "Leeds"}'

    result = llm.complete(
        MESSAGES, schema=Answer, feature="quick_add", prompt_version="quick_add.v1"
    )

    (row,) = read_traces()
    expected_columns = {
        "request_id", "created_at", "feature", "model", "model_settings",
        "prompt_version", "input", "output", "raw_request", "raw_response",
        "input_tokens", "output_tokens", "latency_ms", "tokens_per_second",
        "success", "hosted",
    }
    for column in expected_columns:
        assert row[column] is not None, column
    assert row["request_id"] == result.request_id
    assert row["feature"] == "quick_add"
    assert row["model"] == "fake-model"
    assert row["prompt_version"] == "quick_add.v1"
    assert json.loads(row["model_settings"]) == {
        "temperature": 0.0, "max_tokens": 1024, "thinking": False,
    }
    assert json.loads(row["input"]) == MESSAGES
    assert json.loads(row["output"]) == {"city": "Leeds"}
    assert json.loads(row["raw_request"])["messages"] == MESSAGES
    assert json.loads(row["raw_response"])["content"] == '<think>x</think>{"city": "Leeds"}'
    assert (row["input_tokens"], row["output_tokens"]) == (12, 8)
    assert row["success"] == 1
    assert row["hosted"] == 0
    assert row["error"] is None


def test_text_only_output_is_stored_as_text(fake_llm):
    fake_llm.text = "plain answer"

    llm.complete(MESSAGES, feature="f", prompt_version="v")

    (row,) = read_traces()
    assert row["output"] == "plain answer"


def test_tokens_per_second():
    assert tracing.tokens_per_second(output_tokens=50, latency_ms=2000) == pytest.approx(25.0)
    assert tracing.tokens_per_second(output_tokens=50, latency_ms=0) is None
    assert tracing.tokens_per_second(output_tokens=None, latency_ms=100) is None


def test_failed_call_is_traced_with_error(fake_llm):
    fake_llm.error = llm.LLMUnavailable("connection refused")

    with pytest.raises(llm.LLMUnavailable):
        llm.complete(MESSAGES, feature="quick_add", prompt_version="v1")

    (row,) = read_traces()
    assert row["success"] == 0
    assert "connection refused" in row["error"]
    assert row["feature"] == "quick_add"
    assert json.loads(row["input"]) == MESSAGES


def test_one_row_per_call(fake_llm):
    fake_llm.text = "x"
    for _ in range(3):
        llm.complete(MESSAGES, feature="f", prompt_version="v")

    rows = read_traces()
    assert len(rows) == 3
    assert len({r["request_id"] for r in rows}) == 3


def test_tracing_failure_does_not_break_the_call(fake_llm, monkeypatch, tmp_path):
    # A directory where the DB file should be: sqlite can't open it.
    bad_path = tmp_path / "is_a_dir"
    os.mkdir(bad_path)
    monkeypatch.setenv("TRACES_DB_PATH", str(bad_path))
    fake_llm.text = "still works"

    result = llm.complete(MESSAGES, feature="f", prompt_version="v")

    assert result.text == "still works"


# The traces table as it was before the hosted column (Phase 0).
PHASE_0_SCHEMA = """
CREATE TABLE traces (
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
    error TEXT
)
"""


def test_old_traces_db_gains_hosted_column(fake_llm, caplog):
    conn = sqlite3.connect(TRACES_DB_PATH)
    conn.execute(PHASE_0_SCHEMA)
    conn.execute(
        "INSERT INTO traces (request_id, created_at, feature, success) "
        "VALUES ('old', '2026-10-15 00:00:00', 'quick_add', 1)"
    )
    conn.commit()
    conn.close()
    fake_llm.text = "hello"

    llm.complete(MESSAGES, feature="quick_add", prompt_version="quick_add.v1")

    old, new = read_traces()
    assert old["hosted"] is None
    assert new["hosted"] == 0
    assert "Failed to write trace" not in caplog.text


def test_hosted_column_migration_is_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "traces.db")
    conn.execute(PHASE_0_SCHEMA)
    tracing._ensure_schema(conn)
    tracing._ensure_schema(conn)
    columns = [r[1] for r in conn.execute("PRAGMA table_info(traces)")]
    conn.close()
    assert columns.count("hosted") == 1



# --- read side (the /ai dashboard, E1b.5) ---------------------------------------

def _record(request_id="r1"):
    tracing.record(
        request_id=request_id, feature="quick_add", model="m", model_settings={},
        prompt_version="quick_add.v1", input="note", output="out", raw_request=None,
        raw_response=None, input_tokens=1, output_tokens=1, latency_ms=1.0,
        success=True, hosted=False,
    )


def test_fetch_without_a_file_returns_nothing_and_creates_nothing():
    assert tracing.fetch() == []
    assert tracing.fetch_one("anything") is None
    assert not tracing.exists()
    assert not os.path.exists(TRACES_DB_PATH)


def test_fetch_newest_first_and_since(monkeypatch):
    for request_id, at in [("old", datetime(2026, 10, 1, 9)), ("new", datetime(2026, 10, 30, 9))]:
        monkeypatch.setattr(tracing.clock, "utc_now", lambda at=at: at)
        _record(request_id)
    assert [r["request_id"] for r in tracing.fetch()] == ["new", "old"]
    assert [r["request_id"] for r in tracing.fetch("2026-10-15 00:00:00")] == ["new"]
    assert tracing.fetch_one("old")["created_at"] == "2026-10-01 09:00:00"


def test_read_connection_is_read_only():
    _record()
    conn = tracing._connect_ro()
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM traces")
    finally:
        conn.close()
    assert len(read_traces()) == 1
