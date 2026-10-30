"""Unit tests for the pure aggregation functions in observability.py (E1b.5).

Fixture rows are plain dicts shaped like tracing.fetch's output, and reports
like the JSON evals/runner.py writes. No DB, no Flask.
"""
from datetime import datetime

import pytest

import observability


def trace(**overrides):
    row = {
        "id": 1,
        "request_id": "r1",
        "created_at": "2026-10-30 09:00:00",
        "feature": "quick_add",
        "model": "qwen3-8b",
        "prompt_version": "quick_add.v1",
        "input_tokens": 370,
        "output_tokens": 100,
        "latency_ms": 5000.0,
        "tokens_per_second": 20.0,
        "success": 1,
        "error": None,
        "hosted": 0,
    }
    row.update(overrides)
    return row


BLOCKED = "HostedCallBlocked: Hosted LLM call blocked (quick_add, model claude-sonnet-5-5)"


def blocked_trace(**overrides):
    return trace(model="claude-sonnet-5-5", input_tokens=None, output_tokens=None,
                 latency_ms=0.1, tokens_per_second=None, success=0, error=BLOCKED,
                 hosted=1, **overrides)


# --- window_start ------------------------------------------------------------

def test_window_start_24h_and_7d():
    now = datetime(2026, 10, 30, 12, 0, 0)
    assert observability.window_start(now, "24h") == "2026-10-29 12:00:00"
    assert observability.window_start(now, "7d") == "2026-10-23 12:00:00"


def test_window_start_all_is_unbounded():
    assert observability.window_start(datetime(2026, 10, 30), "all") is None


def test_window_start_rejects_unknown_window():
    with pytest.raises(ValueError, match="30d"):
        observability.window_start(datetime(2026, 10, 30), "30d")


# --- is_blocked / percentile -----------------------------------------------

def test_is_blocked_reads_the_guard_error():
    assert observability.is_blocked(blocked_trace())
    assert not observability.is_blocked(trace(success=0, error="LLMUnavailable: no LM Studio"))
    assert not observability.is_blocked(trace())


def test_percentile_is_nearest_rank():
    values = [5.0, 1.0, 3.0, 2.0, 4.0]
    assert observability.percentile(values, 0.50) == 3.0
    assert observability.percentile(values, 0.95) == 5.0
    assert observability.percentile([7.0], 0.95) == 7.0
    assert observability.percentile([], 0.5) is None


# --- group_metrics -----------------------------------------------------------

def test_groups_by_feature_model_and_prompt_version_sorted():
    rows = [
        trace(feature="weekly_summary", prompt_version="weekly_summary.v1"),
        trace(model="qwen3-8b"),
        trace(model="qwen/qwen3-4b"),
        trace(model="qwen3-8b", prompt_version="quick_add.v2"),
        trace(model="qwen3-8b"),
    ]
    groups = observability.group_metrics(rows)
    keys = [(g["feature"], g["model"], g["prompt_version"]) for g in groups]
    assert keys == [
        ("quick_add", "qwen/qwen3-4b", "quick_add.v1"),
        ("quick_add", "qwen3-8b", "quick_add.v1"),
        ("quick_add", "qwen3-8b", "quick_add.v2"),
        ("weekly_summary", "qwen3-8b", "weekly_summary.v1"),
    ]
    assert groups[1]["calls"] == 2


def test_success_rate_and_failure_counts():
    rows = [trace(), trace(), trace(success=0, error="ValidationError: bad"), trace()]
    (g,) = observability.group_metrics(rows)
    assert g["calls"] == 4
    assert g["successes"] == 3
    assert g["success_rate"] == 0.75
    assert g["blocked"] == 0


def test_blocked_calls_count_but_stay_out_of_latency():
    rows = [trace(model="claude-sonnet-5-5", latency_ms=2000.0, hosted=1),
            blocked_trace(), blocked_trace()]
    (g,) = observability.group_metrics(rows)
    assert g["calls"] == 3
    assert g["blocked"] == 2
    assert g["p50_latency_ms"] == 2000.0
    assert g["p95_latency_ms"] == 2000.0


def test_hosted_local_and_unknown_split():
    rows = [trace(hosted=0), trace(hosted=0), trace(hosted=1), trace(hosted=None)]
    (g,) = observability.group_metrics(rows)
    assert (g["local"], g["hosted"], g["hosted_unknown"]) == (2, 1, 1)


def test_latency_percentiles_and_mean_tokens_per_second():
    rows = [trace(latency_ms=float(ms), tokens_per_second=tps)
            for ms, tps in [(1000, 10.0), (2000, 20.0), (3000, None), (4000, 30.0)]]
    (g,) = observability.group_metrics(rows)
    assert g["p50_latency_ms"] == 2000.0
    assert g["p95_latency_ms"] == 4000.0
    assert g["tokens_per_s"] == 20.0  # the missing rate is skipped, not zeroed


def test_tokens_skip_missing_values_and_compare_to_budget():
    rows = [trace(input_tokens=300, output_tokens=100),
            trace(input_tokens=500, output_tokens=60),
            trace(input_tokens=None, output_tokens=None, success=0, error="LLMUnavailable: x")]
    (g,) = observability.group_metrics(rows)
    assert g["input_tokens"] == 800
    assert g["output_tokens"] == 160
    assert g["mean_input_tokens"] == 400
    assert g["mean_output_tokens"] == 80
    assert g["max_input_tokens"] == 500
    assert g["max_output_tokens"] == 100
    assert (g["budget_input"], g["budget_output"]) == (1000, 256)
    assert g["input_budget_pct"] == 40.0
    assert g["output_budget_pct"] == 31.2


def test_feature_without_a_budget_has_no_budget_pct():
    (g,) = observability.group_metrics([trace(feature="something_new")])
    assert g["budget_input"] is None
    assert g["input_budget_pct"] is None
    assert g["mean_input_tokens"] == 370


def test_every_traced_feature_has_a_budget():
    assert set(observability.CONTEXT_BUDGETS) == {"quick_add", "weekly_summary"}


def test_group_with_no_token_data_has_no_means():
    (g,) = observability.group_metrics([blocked_trace()])
    assert g["mean_input_tokens"] is None
    assert g["max_input_tokens"] is None
    assert g["input_budget_pct"] is None
    assert g["p50_latency_ms"] is None
    assert g["tokens_per_s"] is None


def test_group_metrics_of_nothing_is_empty():
    assert observability.group_metrics([]) == []


# --- recent_failures ---------------------------------------------------------

def test_recent_failures_newest_first_with_blocked_flag():
    rows = [
        trace(id=1, request_id="ok", created_at="2026-10-30 09:00:00"),
        trace(id=2, request_id="old", created_at="2026-10-28 09:00:00", success=0,
              error="LLMUnavailable: Can't reach LLM"),
        blocked_trace(id=3, request_id="new", created_at="2026-10-30 10:00:00"),
    ]
    failures = observability.recent_failures(rows)
    assert [f["request_id"] for f in failures] == ["new", "old"]
    assert failures[0]["blocked"] is True
    assert failures[1]["blocked"] is False
    assert failures[1]["error"].startswith("LLMUnavailable")


def test_recent_failures_respects_limit():
    rows = [trace(id=i, request_id=f"r{i}", success=0, error="x") for i in range(15)]
    failures = observability.recent_failures(rows, limit=10)
    assert len(failures) == 10
    assert failures[0]["request_id"] == "r14"  # same timestamp: highest id first


# --- eval_comparison ---------------------------------------------------------

def report(model, mode="live", run_at="20261030T090000Z", repeat=3, mean=0.5, fields=None):
    names = fields or ["item_type", "priority"]
    return {
        "run_at": run_at,
        "mode": mode,
        "model": model,
        "prompt_version": "quick_add.v1",
        "repeat": repeat,
        "n_cases": 41,
        "summary": {n: {"mean": mean, "worst": mean - 0.1, "flip_rate": 0.0} for n in names},
        "per_run": [
            {"run": i, "input_tokens": 15000, "output_tokens": 3500 + i * 100,
             "p50_latency_ms": 6000.0 + i * 1000, "p95_latency_ms": 9000.0,
             "tokens_per_s": 12.0}
            for i in range(repeat)
        ],
        "cases": [],
    }


def test_one_column_per_model_ordered_by_run_time():
    reports = [
        report("meta-llama-3.1-8b-instruct", run_at="20261030T092800Z"),
        report("qwen3-8b", run_at="20261030T090224Z"),
        report("qwen/qwen3-4b", run_at="20261030T083952Z"),
    ]
    result = observability.eval_comparison(reports)
    assert [c["model"] for c in result["columns"]] == [
        "qwen/qwen3-4b", "qwen3-8b", "meta-llama-3.1-8b-instruct"]


def test_latest_live_run_wins_over_a_newer_replay():
    reports = [
        report("qwen3-8b", mode="replay", run_at="20261030T093235Z", repeat=1, mean=0.9),
        report("qwen3-8b", mode="live", run_at="20261030T090224Z", repeat=3, mean=0.7),
        report("qwen3-8b", mode="live", run_at="20261030T080000Z", repeat=3, mean=0.1),
    ]
    (column,) = observability.eval_comparison(reports)["columns"]
    assert (column["mode"], column["run_at"], column["repeat"]) == (
        "live", "20261030T090224Z", 3)
    assert column["fields"]["item_type"]["mean"] == 0.7


def test_model_with_only_replays_uses_its_latest_replay():
    reports = [
        report("qwen3-8b", mode="replay", run_at="20261030T080000Z", repeat=1, mean=0.2),
        report("qwen3-8b", mode="replay", run_at="20261030T090000Z", repeat=1, mean=0.4),
    ]
    (column,) = observability.eval_comparison(reports)["columns"]
    assert column["run_at"] == "20261030T090000Z"


def test_columns_carry_per_field_stats_and_efficiency():
    (column,) = observability.eval_comparison([report("qwen3-8b")])["columns"]
    assert column["fields"]["priority"] == {"mean": 0.5, "worst": 0.4, "flip_rate": 0.0}
    assert column["n_cases"] == 41
    assert column["prompt_version"] == "quick_add.v1"
    assert column["p50_latency_ms"] == 7000.0  # mean of 6000, 7000, 8000
    assert column["p95_latency_ms"] == 9000.0
    assert column["tokens_per_s"] == 12.0
    assert column["tokens_per_suite"] == 18600.0  # 15000 + mean(3500, 3600, 3700)


def test_fields_are_the_union_in_report_order():
    reports = [
        report("a", run_at="20261030T080000Z", fields=["item_type", "priority"]),
        report("b", run_at="20261030T090000Z", fields=["item_type", "due_date", "priority"]),
    ]
    assert observability.eval_comparison(reports)["fields"] == [
        "item_type", "priority", "due_date"]


def test_malformed_reports_are_skipped():
    bad = report("broken")
    del bad["summary"]
    reports = [bad, {"model": "x"}, [], None, "nope", report("qwen3-8b")]
    result = observability.eval_comparison(reports)
    assert [c["model"] for c in result["columns"]] == ["qwen3-8b"]


def test_no_reports_means_no_columns():
    assert observability.eval_comparison([]) == {"columns": [], "fields": []}
