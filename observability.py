"""AI dashboard aggregations (slice E1b.5, D29).

PURE MODULE, same discipline as capacity.py: no DB, no Flask, no network, no
clock, no file I/O. Every function takes plain trace rows (dicts, as returned
by tracing.fetch) or parsed eval reports (the JSON evals/runner.py writes) and
returns the numbers /ai renders. app.py does the reading.

See docs/eval-observability.md (Principles 8 and 9, D29) and
docs/slices/slice-e1b-eval-depth.md, "E1b.5 — Dashboard v1".
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

Row = Mapping[str, Any]

# Context budget per feature, (input tokens, output tokens) per call
# (Principle 9). Quick Add per D29 (measured: 370 / 100 at most). The weekly
# summary was measured for E1b.5 on qwen3-8b with synthetic world items:
# 217 / 107 for an 11-item week, 919 / 124 for a 60-item stress week.
CONTEXT_BUDGETS: dict[str, tuple[int, int]] = {
    "quick_add": (1000, 256),
    "weekly_summary": (2000, 256),
}

# OpenTelemetry GenAI semantic-convention names, shown as column tooltips
# (Principle 8). The SQLite column names stay as they are.
OTEL_NAMES: dict[str, str] = {
    "feature": "gen_ai.operation.name (Runway feature)",
    "model": "gen_ai.request.model / gen_ai.response.model",
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "latency_ms": "gen_ai.client.operation.duration",
    "error": "error.type",
    "hosted": "server.address (loopback = local)",
    "prompt_version": "gen_ai.prompt.version (Runway-specific)",
}

WINDOWS = ("24h", "7d", "all")
DEFAULT_WINDOW = "7d"
_WINDOW_DELTAS = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}

BLOCKED_PREFIX = "HostedCallBlocked:"


def window_start(now: datetime, window: str) -> str | None:
    """The earliest created_at inside `window`, as a SQLite-comparable
    'YYYY-MM-DD HH:MM:SS' string (the format tracing.record writes), or None
    for "all". `now` is naive UTC, from clock.utc_now()."""
    if window == "all":
        return None
    if window not in _WINDOW_DELTAS:
        raise ValueError(f"unknown window {window!r}; expected one of {WINDOWS}")
    return (now - _WINDOW_DELTAS[window]).strftime("%Y-%m-%d %H:%M:%S")


def is_blocked(row: Row) -> bool:
    """A hosted call the guard stopped before sending (llm.complete traces it
    with error 'HostedCallBlocked: …')."""
    return (row.get("error") or "").startswith(BLOCKED_PREFIX)


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile: always one of the measured values. Same
    definition as the eval runner's, so dashboard and reports agree."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(p * len(ordered)) - 1, 0)]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _numbers(rows: Iterable[Row], key: str) -> list[float]:
    """The non-missing values of `key`. Missing numbers are skipped, not zeroed."""
    return [row[key] for row in rows if row.get(key) is not None]


def _budget_pct(mean: float | None, budget: int | None) -> float | None:
    if mean is None or not budget:
        return None
    return round(mean / budget * 100, 1)


def group_metrics(rows: Iterable[Row]) -> list[dict[str, Any]]:
    """Per feature × model × prompt version: call counts, latency, throughput
    and token use against the feature's context budget. Sorted by those keys.

    Blocked calls count as calls (and failures) but are left out of latency
    and tokens/s, since nothing was sent. `hosted` NULL (rows from before the
    guard) counts as unknown.
    """
    groups: dict[tuple[str, str, str], list[Row]] = {}
    for row in rows:
        key = (row.get("feature") or "", row.get("model") or "", row.get("prompt_version") or "")
        groups.setdefault(key, []).append(row)

    result = []
    for (feature, model, prompt_version), group in sorted(groups.items()):
        calls = len(group)
        successes = sum(1 for r in group if r.get("success"))
        sent = [r for r in group if not is_blocked(r)]
        latencies = _numbers(sent, "latency_ms")
        input_tokens = _numbers(group, "input_tokens")
        output_tokens = _numbers(group, "output_tokens")
        mean_in = _mean(input_tokens)
        mean_out = _mean(output_tokens)
        budget_in, budget_out = CONTEXT_BUDGETS.get(feature, (None, None))
        result.append({
            "feature": feature,
            "model": model,
            "prompt_version": prompt_version,
            "calls": calls,
            "successes": successes,
            "success_rate": successes / calls,
            "blocked": sum(1 for r in group if is_blocked(r)),
            "hosted": sum(1 for r in group if r.get("hosted") == 1),
            "local": sum(1 for r in group if r.get("hosted") == 0),
            "hosted_unknown": sum(1 for r in group if r.get("hosted") is None),
            "p50_latency_ms": percentile(latencies, 0.50),
            "p95_latency_ms": percentile(latencies, 0.95),
            "tokens_per_s": _mean(_numbers(sent, "tokens_per_second")),
            "input_tokens": sum(input_tokens),
            "output_tokens": sum(output_tokens),
            "mean_input_tokens": mean_in,
            "mean_output_tokens": mean_out,
            "max_input_tokens": max(input_tokens, default=None),
            "max_output_tokens": max(output_tokens, default=None),
            "budget_input": budget_in,
            "budget_output": budget_out,
            "input_budget_pct": _budget_pct(mean_in, budget_in),
            "output_budget_pct": _budget_pct(mean_out, budget_out),
        })
    return result


def recent_failures(rows: Iterable[Row], limit: int = 10) -> list[dict[str, Any]]:
    """Failed calls (blocked ones included), newest first."""
    failed = [r for r in rows if not r.get("success")]
    failed.sort(key=lambda r: (r.get("created_at") or "", r.get("id") or 0), reverse=True)
    return [
        {
            "created_at": r.get("created_at"),
            "feature": r.get("feature"),
            "model": r.get("model"),
            "error": r.get("error"),
            "request_id": r.get("request_id"),
            "blocked": is_blocked(r),
        }
        for r in failed[:limit]
    ]


_REPORT_KEYS = ("run_at", "mode", "model", "summary", "per_run")


def _is_report(report: Any) -> bool:
    return (isinstance(report, Mapping) and all(k in report for k in _REPORT_KEYS)
            and isinstance(report["summary"], Mapping) and isinstance(report["per_run"], list))


def _report_column(report: Mapping[str, Any]) -> dict[str, Any]:
    per_run = report["per_run"]
    return {
        "model": report["model"],
        "mode": report["mode"],
        "run_at": report["run_at"],
        "repeat": report.get("repeat", 1),
        "n_cases": report.get("n_cases"),
        "prompt_version": report.get("prompt_version"),
        "fields": dict(report["summary"]),
        # One suite run's cost and speed, averaged over the repeats.
        "p50_latency_ms": _mean(_numbers(per_run, "p50_latency_ms")),
        "p95_latency_ms": _mean(_numbers(per_run, "p95_latency_ms")),
        "tokens_per_s": _mean(_numbers(per_run, "tokens_per_s")),
        "tokens_per_suite": _mean([(r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
                                   for r in per_run]),
    }


def eval_comparison(reports: Iterable[Any]) -> dict[str, Any]:
    """One column per model from the eval runner's JSON reports.

    Each model's column is its latest live run; a model with no live run falls
    back to its latest replay. (A replay re-scores the same recordings with
    --repeat 1 by default, so preferring the newest report regardless of mode
    would hide a 3-repeat live run's worst run and flip rate.) Columns are
    ordered oldest first; `fields` lists every scored field in report order.
    Malformed reports are skipped.
    """
    chosen: dict[str, Mapping[str, Any]] = {}
    for report in reports:
        if not _is_report(report):
            continue
        current = chosen.get(report["model"])
        rank = (report["mode"] == "live", report["run_at"])
        if current is None or rank > (current["mode"] == "live", current["run_at"]):
            chosen[report["model"]] = report

    columns = sorted((_report_column(r) for r in chosen.values()), key=lambda c: c["run_at"])
    fields: list[str] = []
    for column in columns:
        fields += [name for name in column["fields"] if name not in fields]
    return {"columns": columns, "fields": fields}
