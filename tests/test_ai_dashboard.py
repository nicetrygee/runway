"""Route tests for the /ai dashboard (E1b.5, D29).

Trace rows are written with tracing.record into conftest's temp
TRACES_DB_PATH (removed before every test); eval reports go in tmp_path via
EVAL_REPORTS_DIR. The aggregation itself is covered in test_observability.py.
"""
import html
import json
import os
import sqlite3
from datetime import datetime

import pytest
from conftest import TRACES_DB_PATH

import clock
import tracing


def record(at=None, **overrides):
    fields = {
        "request_id": "req-ok",
        "feature": "quick_add",
        "model": "qwen3-8b",
        "model_settings": {"temperature": 0.0, "max_tokens": 1024, "thinking": False},
        "prompt_version": "quick_add.v1",
        "input": {"messages": [{"role": "user", "content": "Prep 1:1 with Marta"}]},
        "output": {"title": "Prep 1:1 with Marta"},
        "raw_request": {"model": "qwen3-8b", "messages": []},
        "raw_response": {"id": "resp-1"},
        "input_tokens": 370,
        "output_tokens": 100,
        "latency_ms": 5000.0,
        "success": True,
        "hosted": False,
    }
    fields.update(overrides)
    if at is None:
        tracing.record(**fields)
        return
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(clock, "utc_now", lambda: at)
        tracing.record(**fields)


def record_failures():
    record(request_id="req-failed", success=False, output=None, raw_response=None,
           input_tokens=None, output_tokens=None,
           error="LLMUnavailable: Can't reach LLM at http://localhost:1234/v1/")
    record(request_id="req-blocked", model="claude-sonnet-5-5", hosted=True, success=False,
           output=None, raw_request=None, raw_response=None, input_tokens=None,
           output_tokens=None, latency_ms=0.1,
           error="HostedCallBlocked: Hosted LLM call blocked (quick_add, model "
                 "claude-sonnet-5-5): set RUNWAY_LAB=1 to allow; lab data must be synthetic")


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_REPORTS_DIR", str(tmp_path))
    return tmp_path


def write_report(directory, stamp, model, mode="live", item_type=0.5):
    report = {
        "run_at": stamp,
        "mode": mode,
        "model": model,
        "prompt_version": "quick_add.v1",
        "repeat": 3 if mode == "live" else 1,
        "n_cases": 41,
        "summary": {"item_type": {"mean": item_type, "worst": item_type, "flip_rate": 0.0}},
        "per_run": [{"run": 0, "input_tokens": 14983, "output_tokens": 3542,
                     "p50_latency_ms": 8616.4, "p95_latency_ms": 10766.0,
                     "tokens_per_s": 11.5}],
        "cases": [],
    }
    (directory / f"{stamp}-{mode}.json").write_text(json.dumps(report), encoding="utf-8")


# --- login -------------------------------------------------------------------

def test_ai_requires_login(client):
    response = client.get("/ai")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_trace_detail_requires_login(client):
    record()
    response = client.get("/ai/trace/req-ok")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# --- empty / missing traces.db ------------------------------------------------

def test_renders_empty_state_without_traces_db(logged_in_client, reports_dir):
    response = logged_in_client.get("/ai")
    assert response.status_code == 200
    assert b"No traces.db yet" in response.data
    assert b"No eval reports yet" in response.data
    # Read-only: rendering the page never creates the file.
    assert not os.path.exists(TRACES_DB_PATH)


def test_renders_with_traces_db_but_no_table(logged_in_client, reports_dir):
    sqlite3.connect(TRACES_DB_PATH).close()
    response = logged_in_client.get("/ai")
    assert response.status_code == 200
    assert b"No AI calls in this window" in response.data


def test_missing_reports_dir_is_an_empty_state(logged_in_client, monkeypatch, tmp_path):
    monkeypatch.setenv("EVAL_REPORTS_DIR", str(tmp_path / "nope"))
    response = logged_in_client.get("/ai")
    assert response.status_code == 200
    assert b"No eval reports yet" in response.data


# --- with rows -----------------------------------------------------------------

def test_renders_metrics_for_both_features(logged_in_client, reports_dir):
    record()
    record(request_id="req-ws", feature="weekly_summary", prompt_version="weekly_summary.v1",
           input_tokens=217, output_tokens=107, output="This week the team…")
    response = logged_in_client.get("/ai")
    page = response.data.decode()
    assert response.status_code == 200
    assert "quick_add.v1" in page
    assert "weekly_summary.v1" in page
    assert "370 / 1000" in page and "37% · max 370" in page
    assert "217 / 2000" in page and "11% · max 217" in page
    assert 'title="gen_ai.usage.input_tokens"' in page  # OTel names as tooltips


def test_failed_and_blocked_calls_appear_under_failures(logged_in_client, reports_dir):
    record()
    record_failures()
    page = logged_in_client.get("/ai").data.decode()
    assert "/ai/trace/req-failed" in page
    assert "/ai/trace/req-blocked" in page
    assert "LLMUnavailable" in page
    assert "badge-blocked" in page
    assert "/ai/trace/req-ok" not in page


def test_failure_trace_details_open(logged_in_client):
    record_failures()
    failed = logged_in_client.get("/ai/trace/req-failed")
    assert failed.status_code == 200
    assert b"LLMUnavailable" in failed.data
    blocked = logged_in_client.get("/ai/trace/req-blocked")
    assert blocked.status_code == 200
    assert b"HostedCallBlocked" in blocked.data
    assert b"blocked" in blocked.data


def test_trace_detail_pretty_prints_json(logged_in_client):
    record()
    page = html.unescape(logged_in_client.get("/ai/trace/req-ok").data.decode())
    assert '"title": "Prep 1:1 with Marta"' in page
    assert '\n  "max_tokens": 1024' in page  # indented, not one line
    assert "370" in page and "5000 ms" in page


def test_unknown_trace_redirects_with_flash(logged_in_client):
    record()
    response = logged_in_client.get("/ai/trace/nope", follow_redirects=True)
    assert response.status_code == 200
    assert b"Trace not found" in response.data


def test_window_filters_old_rows(logged_in_client, reports_dir):
    record(at=datetime(2020, 1, 1, 9, 0, 0), request_id="req-ancient",
           model="ancient-model")
    record()
    assert "ancient-model" not in logged_in_client.get("/ai?window=7d").data.decode()
    assert "ancient-model" in logged_in_client.get("/ai?window=all").data.decode()


def test_unknown_window_falls_back_to_default(logged_in_client, reports_dir):
    record()
    response = logged_in_client.get("/ai?window=forever")
    assert response.status_code == 200
    assert b"qwen3-8b" in response.data


def test_dashboard_never_writes_traces_db(logged_in_client, reports_dir):
    record()
    record_failures()
    before = os.stat(TRACES_DB_PATH).st_mtime_ns, os.path.getsize(TRACES_DB_PATH)
    for path in ("/ai", "/ai?window=all", "/ai/trace/req-ok", "/ai/trace/req-blocked"):
        assert logged_in_client.get(path).status_code == 200
    assert (os.stat(TRACES_DB_PATH).st_mtime_ns, os.path.getsize(TRACES_DB_PATH)) == before


# --- eval runs -------------------------------------------------------------------

def test_eval_runs_show_one_column_per_model(logged_in_client, reports_dir):
    write_report(reports_dir, "20261030T083952Z", "qwen/qwen3-4b", item_type=0.317)
    write_report(reports_dir, "20261030T090224Z", "qwen3-8b", item_type=0.463)
    write_report(reports_dir, "20261030T092800Z", "meta-llama-3.1-8b-instruct", item_type=0.561)
    write_report(reports_dir, "20261030T093235Z", "qwen3-8b", mode="replay", item_type=0.99)
    page = logged_in_client.get("/ai").data.decode()
    assert page.index("qwen/qwen3-4b") < page.index("meta-llama-3.1-8b-instruct")
    for cell in ("32% · 32% · 0%", "46% · 46% · 0%", "56% · 56% · 0%"):
        assert cell in page
    assert "99%" not in page  # the newer replay doesn't shadow qwen3-8b's live run


def test_unreadable_report_is_skipped(logged_in_client, reports_dir):
    write_report(reports_dir, "20261030T090224Z", "qwen3-8b")
    (reports_dir / "20261030T099999Z-live.json").write_text("{not json", encoding="utf-8")
    response = logged_in_client.get("/ai")
    assert response.status_code == 200
    assert b"qwen3-8b" in response.data


# --- nav link --------------------------------------------------------------------

def test_nav_link_hidden_without_traces_db(logged_in_client):
    assert b'href="/ai"' not in logged_in_client.get("/").data


def test_nav_link_shown_with_traces_db(logged_in_client):
    record()
    assert b'href="/ai"' in logged_in_client.get("/").data
