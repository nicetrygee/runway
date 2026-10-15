import json
import os

import pytest
from conftest import read_traces
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
        "success",
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
