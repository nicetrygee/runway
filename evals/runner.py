"""Quick Add eval runner: live and replay (slice E0.3, D9/D17).

Both modes call the real feature, `classify.extract_task_from_text`, so the
parsing and scoring code under test is the same either way. The only
difference is what sits behind `llm.adapter`:

- live: the configured adapter (LM Studio), wrapped to capture each raw
  response, which is written to recordings.jsonl (committed). Traced as normal.
- replay: a stand-in that serves the recorded response. It never calls a
  model; a case with no matching recording fails the whole run, by name.

Recordings are keyed on (model, model settings, prompt version, note, today).
Replay proves code and scorer changes only — a new prompt, model or settings
needs a fresh live run.
"""
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

import classify
import clock
import llm
from evals import scoring

EVALS_DIR = Path(__file__).resolve().parent
CASES_PATH = EVALS_DIR / "quick_add" / "cases.jsonl"
RECORDINGS_PATH = EVALS_DIR / "quick_add" / "recordings.jsonl"
REPORTS_DIR = EVALS_DIR / "reports"


class MissingRecording(Exception):
    """Replay found no recording for one or more cases."""

    def __init__(self, case_ids, model, prompt_version):
        self.case_ids = case_ids
        super().__init__(
            f"no recording for {', '.join(case_ids)} "
            f"(model={model}, prompt={prompt_version}) — run --live first"
        )


@dataclass
class CaseResult:
    case_id: str
    scores: dict
    output: dict | None
    error: str | None = None


@dataclass
class RunResult:
    mode: str
    model: str
    prompt_version: str
    cases: list[CaseResult]
    summary: dict
    totals: dict = field(default_factory=dict)
    report_path: Path | None = None


class RecordingAdapter:
    """Wraps the live adapter and keeps the last raw response it returned."""

    def __init__(self, inner):
        self.inner = inner
        self.model = inner.model
        self.last: llm.AdapterResponse | None = None
        self.last_settings: llm.ModelSettings | None = None
        self.last_latency_ms: float | None = None

    def send(self, messages, schema, settings):
        start = time.perf_counter()
        response = self.inner.send(messages, schema, settings)
        self.last_latency_ms = (time.perf_counter() - start) * 1000
        self.last, self.last_settings = response, settings
        return response


class ReplayAdapter:
    """Serves one recorded response at a time. No network."""

    def __init__(self, model):
        self.model = model
        self.recording: dict[str, Any] | None = None

    def send(self, messages, schema, settings):
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


def _read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def load_cases(path=None):
    return _read_jsonl(path or CASES_PATH)


def recording_key(case, model):
    return {
        "model": model,
        "model_settings": asdict(llm.ModelSettings()),
        "prompt_version": classify.PROMPT_VERSION,
        "note": case["note"],
        "today": case["today"],
    }


def _run_case(case):
    """Run one case through the real feature. A reply that doesn't parse is a
    scored failure, not a crash; anything else (e.g. LLMUnavailable) propagates."""
    now = datetime.strptime(case["today"], "%Y-%m-%d")
    try:
        task = classify.extract_task_from_text(case["note"], now=now)
    except ValidationError as e:
        return CaseResult(case["id"], scoring.score_case(case, None), None,
                          f"unparseable reply: {e.error_count()} validation error(s)")
    output = task.model_dump()
    return CaseResult(case["id"], scoring.score_case(case, output), output)


def run_live(cases_path=None, recordings_path=None, reports_dir=None):
    cases = load_cases(cases_path)
    recordings_path = Path(recordings_path or RECORDINGS_PATH)
    real = llm.adapter
    recorder = RecordingAdapter(real)
    model = recorder.model
    expected_settings = asdict(llm.ModelSettings())
    results, new_recs = [], []
    llm.adapter = recorder
    try:
        for case in cases:  # one at a time: one model in memory, 16GB budget
            recorder.last = None
            result = _run_case(case)
            resp = recorder.last
            assert resp is not None and recorder.last_settings is not None
            if asdict(recorder.last_settings) != expected_settings:
                raise RuntimeError(
                    "classify sent non-default model settings; the recording key "
                    "assumes llm.ModelSettings() — update recording_key first")
            results.append(result)
            new_recs.append({
                "case_id": case["id"],
                "key": recording_key(case, model),
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
    kept = [r for r in _read_jsonl(recordings_path) if r.get("key") not in new_keys]
    merged = sorted(kept + new_recs, key=lambda r: r["case_id"])
    recordings_path.parent.mkdir(parents=True, exist_ok=True)
    recordings_path.write_text("".join(json.dumps(r) + "\n" for r in merged), encoding="utf-8")

    totals = {
        "input_tokens": sum(r["input_tokens"] or 0 for r in new_recs),
        "output_tokens": sum(r["output_tokens"] or 0 for r in new_recs),
        "latency_ms": sum(r["latency_ms"] or 0 for r in new_recs),
    }
    run = RunResult("live", model, classify.PROMPT_VERSION, results,
                    scoring.summarise([r.scores for r in results]), totals)
    run.report_path = write_report(run, cases, reports_dir)
    return run


def run_replay(cases_path=None, recordings_path=None, reports_dir=None):
    cases = load_cases(cases_path)
    model = llm.adapter.model
    recordings = _read_jsonl(recordings_path or RECORDINGS_PATH)
    by_case = {}
    for case in cases:
        key = recording_key(case, model)
        by_case[case["id"]] = next((r for r in recordings if r.get("key") == key), None)
    missing = [cid for cid, rec in by_case.items() if rec is None]
    if missing:
        raise MissingRecording(missing, model, classify.PROMPT_VERSION)

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
            for case in cases:
                replayer.recording = by_case[case["id"]]
                results.append(_run_case(case))
        finally:
            llm.adapter = real
            if old_traces is None:
                os.environ.pop("TRACES_DB_PATH", None)
            else:
                os.environ["TRACES_DB_PATH"] = old_traces

    run = RunResult("replay", model, classify.PROMPT_VERSION, results,
                    scoring.summarise([r.scores for r in results]))
    run.report_path = write_report(run, cases, reports_dir)
    return run


def format_summary(run):
    n = len(run.cases)
    lines = ["| Field | Accuracy | Correct |", "|---|---|---|"]
    for name, acc in run.summary.items():
        lines.append(f"| {name} | {acc:.0%} | {round(acc * n)}/{n} |")
    return "\n".join(lines)


def write_report(run, cases, reports_dir=None):
    reports_dir = Path(reports_dir or REPORTS_DIR)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = clock.utc_now().strftime("%Y%m%dT%H%M%SZ")
    path = reports_dir / f"{stamp}-{run.mode}.md"
    by_id = {c["id"]: c for c in cases}

    lines = [
        f"# Quick Add eval — {run.mode}",
        "",
        f"- Run at: {stamp}",
        f"- Model: {run.model}",
        f"- Prompt version: {run.prompt_version}",
        f"- Cases: {len(run.cases)}",
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
            "",
        ]
    lines += ["## Failures", ""]
    failures = [r for r in run.cases if not all(r.scores.values())]
    if not failures:
        lines.append("None.")
    for r in failures:
        case = by_id[r.case_id]
        lines.append(f"### {r.case_id} — {case['note']}")
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
    return path
