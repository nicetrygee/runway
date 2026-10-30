"""Quick Add eval runner: live and replay (slices E0.3, E1b.4; D4/D9/D17).

Both modes call the real feature, `classify.extract_task_from_text`, so the
parsing and scoring code under test is the same either way. The only
difference is what sits behind `llm.adapter`:

- live: the configured adapter (LM Studio), wrapped to capture each raw
  response, which is written to recordings.jsonl (committed). Traced as normal.
- replay: a stand-in that serves the recorded response. It never calls a
  model; a case with no matching recording fails the whole run, by name.

Recordings are keyed on (model, model settings, prompt version, note, today,
run). Replay proves code and scorer changes only — a new prompt, model or
settings needs a fresh live run.

`repeat` (D4) runs every case N times, one whole pass after another; each run
of a case gets its own recording, with `run` 0..N-1 in its key. A recording
with no `run` predates repeats and counts as run 0. Replay with `repeat` N
needs all N recordings of every case.

Each run writes a Markdown report and the same content as JSON, side by side.
"""
import json
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

import classify
import clock
import llm
from evals import scoring

EVALS_DIR = Path(__file__).resolve().parent
CASES_PATH = EVALS_DIR / "quick_add" / "cases.jsonl"
RECORDINGS_PATH = EVALS_DIR / "quick_add" / "recordings.jsonl"
REPORTS_DIR = EVALS_DIR / "reports"

PathArg = str | Path | None


class MissingRecording(Exception):
    """Replay found no recording for one or more (case, run) pairs."""

    def __init__(
        self, missing: list[tuple[str, int]], model: str, prompt_version: str, repeat: int = 1
    ) -> None:
        self.missing = missing
        self.case_ids = list(dict.fromkeys(cid for cid, _ in missing))
        names = self.case_ids if repeat == 1 else [f"{cid} run {r}" for cid, r in missing]
        super().__init__(
            f"no recording for {', '.join(names)} "
            f"(model={model}, prompt={prompt_version}) — run --live first"
        )


@dataclass
class CaseResult:
    case_id: str
    scores: dict
    output: dict | None
    error: str | None = None
    run: int = 0


@dataclass
class RunResult:
    """`cases` holds every (case, run) result, one whole run after another.
    `summary` is per-field mean accuracy across runs; `stats` adds the worst
    run and the flip rate; `flips` names the fields each case flipped on."""

    mode: str
    model: str
    prompt_version: str
    cases: list[CaseResult]
    summary: dict
    totals: dict = field(default_factory=dict)
    report_path: Path | None = None
    repeat: int = 1
    stats: dict = field(default_factory=dict)
    flips: dict = field(default_factory=dict)
    per_run: list[dict] = field(default_factory=list)
    json_path: Path | None = None
    stamp: str = ""


class RecordingAdapter:
    """Wraps the live adapter and keeps the last raw response it returned."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.model = inner.model
        self.hosted = inner.hosted  # stays behind the guard during --live
        self.last: llm.AdapterResponse | None = None
        self.last_settings: llm.ModelSettings | None = None
        self.last_latency_ms: float | None = None

    def send(
        self,
        messages: llm.Messages,
        schema: type[BaseModel] | None,
        settings: llm.ModelSettings,
    ) -> llm.AdapterResponse:
        start = time.perf_counter()
        response: llm.AdapterResponse = self.inner.send(messages, schema, settings)
        self.last_latency_ms = (time.perf_counter() - start) * 1000
        self.last, self.last_settings = response, settings
        return response


class ReplayAdapter:
    """Serves one recorded response at a time. No network."""

    hosted = False

    def __init__(self, model: str) -> None:
        self.model = model
        self.recording: dict[str, Any] | None = None

    def send(
        self,
        messages: llm.Messages,
        schema: type[BaseModel] | None,
        settings: llm.ModelSettings,
    ) -> llm.AdapterResponse:
        rec = self.recording
        assert rec is not None, "ReplayAdapter called with no recording loaded"
        return llm.AdapterResponse(
            raw_request={"replay": rec["case_id"]},
            raw_response={"replay": rec["case_id"]},
            text=rec["text"],
            input_tokens=rec.get("input_tokens"),
            output_tokens=rec.get("output_tokens"),
            model=rec.get("response_model") or self.model,
        )


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def load_cases(path: str | Path | None = None) -> list[dict[str, Any]]:
    return _read_jsonl(path or CASES_PATH)


def recording_key(case: dict[str, Any], model: str, run: int = 0) -> dict[str, Any]:
    return {
        "model": model,
        "model_settings": asdict(llm.ModelSettings()),
        "prompt_version": classify.PROMPT_VERSION,
        "note": case["note"],
        "today": case["today"],
        "run": run,
    }


def _key_of(rec: dict[str, Any]) -> dict[str, Any] | None:
    """A stored recording's key, with a missing `run` read as run 0 (pre-E1b.4)."""
    key = rec.get("key")
    if not isinstance(key, dict):
        return None
    return {**key, "run": key.get("run", 0)}


def _check_repeat(repeat: int) -> None:
    if repeat < 1:
        raise ValueError(f"repeat must be at least 1, got {repeat}")


def _percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile: always one of the measured values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(p * len(ordered)) - 1, 0)]


def efficiency(recs: list[dict[str, Any]]) -> dict[str, Any]:
    """Token totals, p50/p95 latency and mean output tokens/s over one run's
    recordings. Missing numbers (None or absent) are skipped, not zeroed."""
    latencies = [r["latency_ms"] for r in recs if r.get("latency_ms") is not None]
    rates = [r["output_tokens"] / (r["latency_ms"] / 1000) for r in recs
             if r.get("output_tokens") is not None and r.get("latency_ms")]
    return {
        "input_tokens": sum(r.get("input_tokens") or 0 for r in recs),
        "output_tokens": sum(r.get("output_tokens") or 0 for r in recs),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "tokens_per_s": sum(rates) / len(rates) if rates else None,
    }


_UNPARSEABLE = object()  # an unparseable reply counts as its own answer for flips


def _finish(
    mode: str, model: str, results: list[CaseResult], recs: list[dict[str, Any]],
    cases: list[dict[str, Any]], repeat: int, reports_dir: PathArg,
) -> RunResult:
    """Aggregate across runs and write both reports. `results` and `recs` are
    parallel, run-major: run 0's cases, then run 1's, …"""
    n = len(cases)
    per_run_scores = [[r.scores for r in results[i * n:(i + 1) * n]] for i in range(repeat)]
    per_run_acc = [scoring.summarise(s) for s in per_run_scores]
    summary = scoring.summarise([r.scores for r in results])

    flips: dict[str, list[str]] = {}
    for i, case in enumerate(cases):
        runs = results[i::n]
        flipped = [name for name in summary
                   if len({repr(_UNPARSEABLE if r.output is None else r.output[name])
                           for r in runs}) > 1]
        if flipped:
            flips[case["id"]] = flipped
    stats = {
        name: {
            "mean": acc,
            "worst": min(a[name] for a in per_run_acc),
            "flip_rate": sum(1 for f in flips.values() if name in f) / n,
        }
        for name, acc in summary.items()
    }
    per_run = [{"run": i, **efficiency(recs[i * n:(i + 1) * n])} for i in range(repeat)]
    totals = {
        "input_tokens": sum(r["input_tokens"] for r in per_run),
        "output_tokens": sum(r["output_tokens"] for r in per_run),
        "latency_ms": sum(r.get("latency_ms") or 0 for r in recs),
    }
    run = RunResult(mode, model, classify.PROMPT_VERSION, results, summary, totals,
                    repeat=repeat, stats=stats, flips=flips, per_run=per_run)
    write_report(run, cases, reports_dir)
    return run


def _run_case(case: dict[str, Any], run: int = 0) -> CaseResult:
    """Run one case through the real feature. A reply that doesn't parse is a
    scored failure, not a crash; anything else (e.g. LLMUnavailable) propagates."""
    now = datetime.strptime(case["today"], "%Y-%m-%d")
    try:
        task = classify.extract_task_from_text(case["note"], now=now)
    except ValidationError as e:
        return CaseResult(case["id"], scoring.score_case(case, None), None,
                          f"unparseable reply: {e.error_count()} validation error(s)", run)
    output = task.model_dump()
    return CaseResult(case["id"], scoring.score_case(case, output), output, run=run)


def run_live(
    cases_path: PathArg = None, recordings_path: PathArg = None, reports_dir: PathArg = None,
    repeat: int = 1,
) -> RunResult:
    _check_repeat(repeat)
    cases = load_cases(cases_path)
    recordings_path = Path(recordings_path or RECORDINGS_PATH)
    real = llm.adapter
    recorder = RecordingAdapter(real)
    model = recorder.model
    expected_settings = asdict(llm.ModelSettings())
    results: list[CaseResult] = []
    new_recs: list[dict[str, Any]] = []
    llm.adapter = recorder
    try:
        # One case at a time, one whole run after another: one model in
        # memory, 16GB budget. Never in parallel.
        for run_no in range(repeat):
            for case in cases:
                recorder.last = None
                result = _run_case(case, run_no)
                resp = recorder.last
                assert resp is not None and recorder.last_settings is not None
                if asdict(recorder.last_settings) != expected_settings:
                    raise RuntimeError(
                        "classify sent non-default model settings; the recording key "
                        "assumes llm.ModelSettings() — update recording_key first")
                results.append(result)
                new_recs.append({
                    "case_id": case["id"],
                    "key": recording_key(case, model, run_no),
                    "text": resp.text,
                    "input_tokens": resp.input_tokens,
                    "output_tokens": resp.output_tokens,
                    "latency_ms": recorder.last_latency_ms,
                    "response_model": resp.model,
                    "recorded_at": clock.utc_now().strftime("%Y-%m-%d %H:%M:%S"),
                })
    finally:
        llm.adapter = real

    new_keys = [r["key"] for r in new_recs]
    kept = [r for r in _read_jsonl(recordings_path) if _key_of(r) not in new_keys]
    merged = sorted(kept + new_recs,
                    key=lambda r: (r["case_id"], (_key_of(r) or {}).get("run", 0)))
    recordings_path.parent.mkdir(parents=True, exist_ok=True)
    recordings_path.write_text("".join(json.dumps(r) + "\n" for r in merged), encoding="utf-8")

    return _finish("live", model, results, new_recs, cases, repeat, reports_dir)


def run_replay(
    cases_path: PathArg = None, recordings_path: PathArg = None, reports_dir: PathArg = None,
    repeat: int = 1,
) -> RunResult:
    _check_repeat(repeat)
    cases = load_cases(cases_path)
    model = llm.adapter.model
    recordings = _read_jsonl(recordings_path or RECORDINGS_PATH)
    # Look everything up before touching the adapter: a missing recording
    # fails the whole run by name and never falls back to a live call.
    needed = [(run_no, case) for run_no in range(repeat) for case in cases]
    found: list[dict[str, Any]] = []
    missing: list[tuple[str, int]] = []
    for run_no, case in needed:
        key = recording_key(case, model, run_no)
        rec = next((r for r in recordings if _key_of(r) == key), None)
        if rec is None:
            missing.append((case["id"], run_no))
        else:
            found.append(rec)
    if missing:
        missing.sort()
        raise MissingRecording(missing, model, classify.PROMPT_VERSION, repeat)

    real = llm.adapter
    replayer = ReplayAdapter(model)
    old_traces = os.environ.get("TRACES_DB_PATH")
    results = []
    # Replay still goes through llm.complete, which traces; send those rows
    # to a throwaway file so traces.db only ever holds real model calls.
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRACES_DB_PATH"] = os.path.join(tmp, "replay-traces.db")
        llm.adapter = replayer
        try:
            for (run_no, case), rec in zip(needed, found, strict=True):
                replayer.recording = rec
                results.append(_run_case(case, run_no))
        finally:
            llm.adapter = real
            if old_traces is None:
                os.environ.pop("TRACES_DB_PATH", None)
            else:
                os.environ["TRACES_DB_PATH"] = old_traces

    return _finish("replay", model, results, found, cases, repeat, reports_dir)


def format_summary(run: RunResult) -> str:
    if run.repeat > 1:
        lines = ["| Field | Mean | Worst run | Flip rate |", "|---|---|---|---|"]
        for name, s in run.stats.items():
            lines.append(
                f"| {name} | {s['mean']:.0%} | {s['worst']:.0%} | {s['flip_rate']:.0%} |")
        return "\n".join(lines)
    n = len(run.cases)
    lines = ["| Field | Accuracy | Correct |", "|---|---|---|"]
    for name, acc in run.summary.items():
        lines.append(f"| {name} | {acc:.0%} | {round(acc * n)}/{n} |")
    return "\n".join(lines)


def report_dict(run: RunResult, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """The JSON report: the Markdown report's content, machine-readable, for
    the dashboard (E1b.5) and model comparisons (E1b.6, E1b.8)."""
    return {
        "run_at": run.stamp,
        "mode": run.mode,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "repeat": run.repeat,
        "n_cases": len(cases),
        "summary": run.stats,
        "per_run": run.per_run,
        "cases": [
            {
                "id": case["id"],
                "note": case["note"],
                "group": case.get("group"),
                "flipped": run.flips.get(case["id"], []),
                "runs": [{"run": r.run, "scores": r.scores, "output": r.output,
                          "error": r.error}
                         for r in run.cases if r.case_id == case["id"]],
            }
            for case in cases
        ],
    }


def _ms(value: float | None) -> str:
    return "—" if value is None else f"{value / 1000:.1f}s"


def write_report(
    run: RunResult, cases: list[dict[str, Any]], reports_dir: PathArg = None
) -> Path:
    """Write `<stamp>-<mode>.md` and `<stamp>-<mode>.json`; set both paths on `run`."""
    reports_dir = Path(reports_dir or REPORTS_DIR)
    reports_dir.mkdir(parents=True, exist_ok=True)
    run.stamp = clock.utc_now().strftime("%Y%m%dT%H%M%SZ")
    path = reports_dir / f"{run.stamp}-{run.mode}.md"
    by_id = {c["id"]: c for c in cases}

    lines = [
        f"# Quick Add eval — {run.mode}",
        "",
        f"- Run at: {run.stamp}",
        f"- Model: {run.model}",
        f"- Prompt version: {run.prompt_version}",
        f"- Cases: {len(cases)}",
    ]
    if run.repeat > 1:
        lines.append(f"- Runs per case: {run.repeat}")
    lines += [
        "",
        "## Per-field accuracy",
        "",
        format_summary(run),
        "",
    ]
    if run.totals:
        n = len(run.cases) or 1
        lines += [
            "## Usage",
            "",
            f"- Input tokens: {run.totals['input_tokens']}",
            f"- Output tokens: {run.totals['output_tokens']}",
            f"- Latency: {run.totals['latency_ms'] / 1000:.1f}s total, "
            f"{run.totals['latency_ms'] / n / 1000:.1f}s mean per case",
        ]
        for eff in run.per_run:
            tps = "—" if eff["tokens_per_s"] is None else f"{eff['tokens_per_s']:.1f}"
            lines.append(
                f"- Run {eff['run']}: {eff['input_tokens']} in / {eff['output_tokens']} out, "
                f"p50 {_ms(eff['p50_latency_ms'])}, p95 {_ms(eff['p95_latency_ms'])}, "
                f"{tps} tokens/s")
        lines.append("")
    if run.repeat > 1:
        lines += ["## Flips", ""]
        if not run.flips:
            lines.append("None: every run agreed on every field.")
        for cid, names in run.flips.items():
            lines.append(f"- {cid}: {', '.join(names)}")
        lines.append("")
    lines += ["## Failures", ""]
    failures = [r for r in run.cases if not all(r.scores.values())]
    if not failures:
        lines.append("None.")
    for r in failures:
        case = by_id[r.case_id]
        suffix = f" (run {r.run})" if run.repeat > 1 else ""
        lines.append(f"### {r.case_id} — {case['note']}{suffix}")
        lines.append("")
        if r.error:
            lines.append(f"- **{r.error}**")
        for name, ok in r.scores.items():
            if ok:
                continue
            want = repr(case["expected"][name])
            if name in case.get("acceptable", {}):
                want += f" (acceptable: {case['acceptable'][name]})"
            got = "—" if r.output is None else repr(r.output[name])
            lines.append(f"- {name}: expected {want}, got {got}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    run.report_path = path
    run.json_path = path.with_suffix(".json")
    run.json_path.write_text(json.dumps(report_dict(run, cases), indent=2) + "\n",
                             encoding="utf-8")
    return path
