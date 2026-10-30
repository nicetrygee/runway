"""Live / replay eval runner (slices E0.3, E1b.4). No network: a scripted adapter
stands in for LM Studio, and every file lives in a temp dir."""
import json

import pytest
from conftest import read_traces

import classify
import llm
from evals import __main__ as cli
from evals import runner

CASES = [
    {"id": "qa-001", "today": "2026-10-15", "note": "Promised Dev the Kestrel review by Friday",
     "group": "normal", "source": "generated",
     "expected": {"title": "Review Kestrel", "item_type": "Technical", "priority": "Important",
                  "effort_minutes": 60, "person": "Dev", "stream": "commitment",
                  "mode": "reactive", "due_date": "2026-10-16"}},
    {"id": "qa-002", "today": "2026-10-15", "note": "Sketch Q1 roadmap themes next week",
     "group": "normal", "source": "generated",
     "expected": {"title": "Sketch Q1 themes", "item_type": "Strategy", "priority": "Important",
                  "effort_minutes": 120, "person": "", "stream": "task",
                  "mode": "proactive", "due_date": "2026-10-23"},
     "acceptable": {"priority": ["Important", "Normal"]}},
]


def _reply(expected, **overrides):
    out = dict(expected, blast_radius="", sprint="", notes="")
    out.update(overrides)
    return json.dumps(out)


class ScriptedAdapter:
    """Answers each note with a canned reply; counts calls; no network."""

    model = "fake-model"
    hosted = False

    def __init__(self, replies, error=None):
        self.replies = replies
        self.error = error
        self.calls = []

    def send(self, messages, schema, settings):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        note = messages[-1]["content"]
        return llm.AdapterResponse(
            raw_request={"messages": messages}, raw_response={"id": "x"},
            text=self.replies[note], input_tokens=100, output_tokens=40, model=self.model,
        )


@pytest.fixture
def paths(tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text("".join(json.dumps(c) + "\n" for c in CASES))
    return {
        "cases_path": cases,
        "recordings_path": tmp_path / "recordings.jsonl",
        "reports_dir": tmp_path / "reports",
    }


@pytest.fixture
def scripted(monkeypatch):
    replies = {
        CASES[0]["note"]: _reply(CASES[0]["expected"], person="Dev Patel"),  # person wrong
        CASES[1]["note"]: "<think>hmm</think>" + _reply(CASES[1]["expected"], priority="Normal"),
    }
    adapter = ScriptedAdapter(replies)
    monkeypatch.setattr(llm, "adapter", adapter)
    return adapter


def _recordings(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --- live ---

def test_live_runs_every_case_and_records_with_the_replay_key(paths, scripted):
    result = runner.run_live(**paths)
    assert len(scripted.calls) == 2

    recs = _recordings(paths["recordings_path"])
    assert [r["case_id"] for r in recs] == ["qa-001", "qa-002"]
    key = recs[1]["key"]
    assert key == {
        "model": "fake-model",
        "model_settings": {"temperature": 0.0, "max_tokens": 1024, "thinking": False},
        "prompt_version": classify.PROMPT_VERSION,
        "note": CASES[1]["note"],
        "today": "2026-10-15",
        "run": 0,
    }
    assert recs[1]["text"].startswith("<think>")  # raw text, re-parsed on replay
    assert recs[1]["input_tokens"] == 100 and recs[1]["output_tokens"] == 40
    assert recs[1]["latency_ms"] >= 0

    assert result.summary["person"] == 0.5
    assert result.summary["priority"] == 1.0  # Normal is acceptable for qa-002
    assert result.totals["input_tokens"] == 200
    assert result.totals["output_tokens"] == 80


def test_live_passes_the_case_today_to_the_prompt(paths, scripted):
    runner.run_live(**paths)
    assert "Today's date is 2026-10-15" in scripted.calls[0][0]["content"]


def test_live_traces_as_normal(paths, scripted):
    runner.run_live(**paths)
    rows = read_traces()
    assert len(rows) == 2
    assert {r["feature"] for r in rows} == {"quick_add"}


def test_live_writes_a_report(paths, scripted):
    result = runner.run_live(**paths)
    assert result.report_path.parent == paths["reports_dir"]
    assert result.report_path.name.endswith("-live.md")
    report = result.report_path.read_text()
    assert "| person | 50%" in report
    assert "qa-001" in report and "Dev Patel" in report  # the failure, with what we got
    assert "200" in report and "80" in report  # token totals


def test_live_rerun_replaces_matching_recordings_and_keeps_others(paths, scripted):
    other = {"case_id": "qa-001", "key": {"model": "other-model"}, "text": "{}"}
    paths["recordings_path"].write_text(json.dumps(other) + "\n")
    runner.run_live(**paths)
    runner.run_live(**paths)
    recs = _recordings(paths["recordings_path"])
    assert len(recs) == 3
    assert sum(1 for r in recs if r["key"]["model"] == "other-model") == 1


def test_live_unparseable_reply_is_recorded_and_scored_all_wrong(paths, scripted):
    scripted.replies[CASES[0]["note"]] = "not json at all"
    result = runner.run_live(**paths)
    failed = result.cases[0]
    assert failed.output is None
    assert failed.error
    assert not any(failed.scores.values())
    assert _recordings(paths["recordings_path"])[0]["text"] == "not json at all"


def test_live_aborts_when_lm_studio_is_unreachable(paths, monkeypatch):
    monkeypatch.setattr(llm, "adapter", ScriptedAdapter({}, error=llm.LLMUnavailable("down")))
    with pytest.raises(llm.LLMUnavailable):
        runner.run_live(**paths)


def test_live_restores_the_real_adapter(paths, scripted):
    runner.run_live(**paths)
    assert llm.adapter is scripted


# --- replay ---

def test_replay_reproduces_live_scores_without_calling_the_model(paths, scripted):
    live = runner.run_live(**paths)
    calls_after_live = len(scripted.calls)

    replay = runner.run_replay(**paths)

    assert len(scripted.calls) == calls_after_live
    assert replay.summary == live.summary
    assert [c.scores for c in replay.cases] == [c.scores for c in live.cases]
    assert replay.report_path.name.endswith("-replay.md")


def test_replay_reproduces_a_parse_failure(paths, scripted):
    scripted.replies[CASES[0]["note"]] = "not json at all"
    live = runner.run_live(**paths)
    replay = runner.run_replay(**paths)
    assert replay.cases[0].output is None
    assert replay.summary == live.summary


def test_replay_fails_loudly_naming_the_case_with_no_recording(paths, scripted):
    runner.run_live(**paths)
    recs = _recordings(paths["recordings_path"])
    paths["recordings_path"].write_text(json.dumps(recs[0]) + "\n")  # drop qa-002
    calls_after_live = len(scripted.calls)

    with pytest.raises(runner.MissingRecording) as exc:
        runner.run_replay(**paths)

    assert exc.value.case_ids == ["qa-002"]
    assert "qa-002" in str(exc.value)
    assert len(scripted.calls) == calls_after_live  # never falls back to live


def test_replay_with_no_recordings_file_names_every_case(paths, scripted):
    with pytest.raises(runner.MissingRecording) as exc:
        runner.run_replay(**paths)
    assert exc.value.case_ids == ["qa-001", "qa-002"]


@pytest.mark.parametrize("field,value", [
    ("prompt_version", "quick_add.v0"),
    ("model", "other-model"),
    ("today", "2026-10-14"),
    ("note", "a different note"),
])
def test_replay_treats_a_key_mismatch_as_missing(paths, scripted, field, value):
    runner.run_live(**paths)
    recs = _recordings(paths["recordings_path"])
    recs[0]["key"][field] = value
    paths["recordings_path"].write_text("".join(json.dumps(r) + "\n" for r in recs))
    with pytest.raises(runner.MissingRecording) as exc:
        runner.run_replay(**paths)
    assert exc.value.case_ids == ["qa-001"]


def test_replay_treats_changed_model_settings_as_missing(paths, scripted):
    runner.run_live(**paths)
    recs = _recordings(paths["recordings_path"])
    recs[1]["key"]["model_settings"]["temperature"] = 0.7
    paths["recordings_path"].write_text("".join(json.dumps(r) + "\n" for r in recs))
    with pytest.raises(runner.MissingRecording) as exc:
        runner.run_replay(**paths)
    assert exc.value.case_ids == ["qa-002"]


def test_replay_writes_no_traces(paths, scripted):
    runner.run_live(**paths)
    before = len(read_traces())
    runner.run_replay(**paths)
    assert len(read_traces()) == before


def test_replay_restores_the_real_adapter(paths, scripted):
    runner.run_live(**paths)
    runner.run_replay(**paths)
    assert llm.adapter is scripted


# --- CLI ---

@pytest.fixture
def cli_paths(paths, monkeypatch):
    monkeypatch.setattr(runner, "CASES_PATH", paths["cases_path"])
    monkeypatch.setattr(runner, "RECORDINGS_PATH", paths["recordings_path"])
    monkeypatch.setattr(runner, "REPORTS_DIR", paths["reports_dir"])
    return paths


def test_cli_live_then_replay_exit_zero(cli_paths, scripted, capsys):
    assert cli.main(["quick_add", "--live"]) == 0
    assert cli.main(["quick_add", "--replay"]) == 0
    out = capsys.readouterr().out
    assert "person" in out and "report:" in out.lower()


def test_cli_replay_missing_recording_exits_nonzero_naming_case(cli_paths, scripted, capsys):
    assert cli.main(["quick_add", "--replay"]) == 1
    err = capsys.readouterr().err
    assert "qa-001" in err and "qa-002" in err


def test_cli_live_unreachable_exits_nonzero(cli_paths, monkeypatch, capsys):
    monkeypatch.setattr(llm, "adapter", ScriptedAdapter({}, error=llm.LLMUnavailable("down")))
    assert cli.main(["quick_add", "--live"]) == 1
    assert "down" in capsys.readouterr().err


def test_cli_requires_exactly_one_mode(cli_paths):
    with pytest.raises(SystemExit) as exc:
        cli.main(["quick_add"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        cli.main(["quick_add", "--live", "--replay"])


def test_recording_adapter_forwards_hosted():
    inner = ScriptedAdapter({})
    assert runner.RecordingAdapter(inner).hosted is False
    inner.hosted = True
    assert runner.RecordingAdapter(inner).hosted is True


def test_replay_adapter_is_not_hosted():
    assert runner.ReplayAdapter("m").hosted is False


def test_cli_live_hosted_without_lab_flag_exits_1(cli_paths, scripted, capsys):
    scripted.hosted = True

    assert cli.main(["quick_add", "--live"]) == 1

    assert "RUNWAY_LAB" in capsys.readouterr().err
    assert scripted.calls == []
    assert not cli_paths["recordings_path"].exists()
    assert llm.adapter is scripted


def test_cli_live_hosted_with_lab_flag_runs(cli_paths, scripted, monkeypatch):
    monkeypatch.setenv("RUNWAY_LAB", "1")
    scripted.hosted = True

    assert cli.main(["quick_add", "--live"]) == 0
    assert len(scripted.calls) == 2


# --- repeat runs (E1b.4) ---

class AlternatingAdapter(ScriptedAdapter):
    """Cycles through a list of replies per note, so repeat runs can disagree."""

    def __init__(self, replies):
        super().__init__(replies)
        self.seen = {}

    def send(self, messages, schema, settings):
        self.calls.append(messages)
        note = messages[-1]["content"]
        n = self.seen.get(note, 0)
        self.seen[note] = n + 1
        options = self.replies[note]
        return llm.AdapterResponse(
            raw_request={"messages": messages}, raw_response={"id": "x"},
            text=options[n % len(options)], input_tokens=100, output_tokens=40,
            model=self.model,
        )


@pytest.fixture
def alternating(monkeypatch):
    first, second = CASES[0]["expected"], CASES[1]["expected"]
    adapter = AlternatingAdapter({
        # qa-001: run 1 says Normal, which isn't acceptable for qa-001
        CASES[0]["note"]: [_reply(first), _reply(first, priority="Normal"), _reply(first)],
        CASES[1]["note"]: [_reply(second)],
    })
    monkeypatch.setattr(llm, "adapter", adapter)
    return adapter


def _write(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def test_live_repeat_records_one_recording_per_case_and_run(paths, alternating):
    result = runner.run_live(**paths, repeat=3)

    assert len(alternating.calls) == 6
    recs = _recordings(paths["recordings_path"])
    assert [(r["case_id"], r["key"]["run"]) for r in recs] == [
        ("qa-001", 0), ("qa-001", 1), ("qa-001", 2),
        ("qa-002", 0), ("qa-002", 1), ("qa-002", 2),
    ]
    assert result.repeat == 3
    assert [(c.case_id, c.run) for c in result.cases] == [
        ("qa-001", 0), ("qa-002", 0), ("qa-001", 1), ("qa-002", 1),
        ("qa-001", 2), ("qa-002", 2),
    ]


def test_live_repeat_reports_flip_rate_worst_run_and_mean(paths, alternating):
    result = runner.run_live(**paths, repeat=3)

    priority = result.stats["priority"]
    assert priority["flip_rate"] == 0.5  # qa-001 flips, qa-002 doesn't
    assert priority["worst"] == 0.5  # run 1 gets qa-001 wrong
    assert priority["mean"] == pytest.approx(5 / 6)
    assert result.summary["priority"] == pytest.approx(5 / 6)
    for name in ("item_type", "person", "due_date"):
        assert result.stats[name] == {"mean": 1.0, "worst": 1.0, "flip_rate": 0.0}
    assert result.flips == {"qa-001": ["priority"]}


def test_flip_counts_a_changed_value_even_when_both_are_acceptable(paths, monkeypatch):
    second = CASES[1]["expected"]  # Normal is acceptable for qa-002
    monkeypatch.setattr(llm, "adapter", AlternatingAdapter({
        CASES[0]["note"]: [_reply(CASES[0]["expected"])],
        CASES[1]["note"]: [_reply(second), _reply(second, priority="Normal")],
    }))
    result = runner.run_live(**paths, repeat=2)
    assert result.stats["priority"]["mean"] == 1.0
    assert result.stats["priority"]["flip_rate"] == 0.5
    assert result.flips == {"qa-002": ["priority"]}


def test_flip_counts_an_unparseable_run_as_its_own_value(paths, monkeypatch):
    monkeypatch.setattr(llm, "adapter", AlternatingAdapter({
        CASES[0]["note"]: [_reply(CASES[0]["expected"]), "not json at all"],
        CASES[1]["note"]: [_reply(CASES[1]["expected"])],
    }))
    result = runner.run_live(**paths, repeat=2)
    assert all(s["flip_rate"] == 0.5 for s in result.stats.values())
    assert result.flips["qa-001"] == list(result.stats)


def test_single_run_has_no_flips(paths, scripted):
    result = runner.run_live(**paths)
    assert all(s["flip_rate"] == 0.0 for s in result.stats.values())
    assert result.flips == {}
    assert result.stats["person"]["worst"] == result.summary["person"] == 0.5


def test_live_rerun_replaces_old_recordings_with_no_run_field(paths, scripted):
    runner.run_live(**paths)
    old = _recordings(paths["recordings_path"])
    for r in old:
        del r["key"]["run"]
    _write(paths["recordings_path"], old)

    runner.run_live(**paths)

    recs = _recordings(paths["recordings_path"])
    assert [(r["case_id"], r["key"]["run"]) for r in recs] == [("qa-001", 0), ("qa-002", 0)]


def test_replay_treats_a_recording_with_no_run_field_as_run_0(paths, scripted):
    live = runner.run_live(**paths)
    old = _recordings(paths["recordings_path"])
    for r in old:
        del r["key"]["run"]
    _write(paths["recordings_path"], old)
    calls_after_live = len(scripted.calls)

    replay = runner.run_replay(**paths)

    assert replay.summary == live.summary
    assert [c.scores for c in replay.cases] == [c.scores for c in live.cases]
    assert len(scripted.calls) == calls_after_live


def test_replay_repeat_reproduces_the_live_run_exactly(paths, alternating):
    live = runner.run_live(**paths, repeat=3)
    calls_after_live = len(alternating.calls)

    replay = runner.run_replay(**paths, repeat=3)

    assert len(alternating.calls) == calls_after_live
    assert replay.stats == live.stats
    assert replay.flips == live.flips
    assert replay.per_run == live.per_run
    assert [(c.case_id, c.run, c.scores) for c in replay.cases] == [
        (c.case_id, c.run, c.scores) for c in live.cases]


def test_replay_repeat_with_a_missing_run_fails_naming_case_and_run(paths, alternating):
    runner.run_live(**paths, repeat=3)
    recs = [r for r in _recordings(paths["recordings_path"])
            if not (r["case_id"] == "qa-002" and r["key"]["run"] == 2)]
    _write(paths["recordings_path"], recs)
    calls_after_live = len(alternating.calls)

    with pytest.raises(runner.MissingRecording) as exc:
        runner.run_replay(**paths, repeat=3)

    assert exc.value.case_ids == ["qa-002"]
    assert exc.value.missing == [("qa-002", 2)]
    assert "qa-002 run 2" in str(exc.value)
    assert len(alternating.calls) == calls_after_live  # never falls back to live


def test_replay_repeat_over_single_run_recordings_names_every_missing_run(paths, scripted):
    runner.run_live(**paths)
    with pytest.raises(runner.MissingRecording) as exc:
        runner.run_replay(**paths, repeat=2)
    assert exc.value.missing == [("qa-001", 1), ("qa-002", 1)]
    assert exc.value.case_ids == ["qa-001", "qa-002"]


def test_repeat_must_be_at_least_1(paths, scripted):
    with pytest.raises(ValueError):
        runner.run_live(**paths, repeat=0)
    with pytest.raises(ValueError):
        runner.run_replay(**paths, repeat=0)


# --- efficiency ---

def test_efficiency_per_run_totals_percentiles_and_tokens_per_second():
    recs = [
        {"input_tokens": 100, "output_tokens": 50, "latency_ms": ms}
        for ms in (1000, 2000, 3000, 4000, 10000)
    ]
    eff = runner.efficiency(recs)
    assert eff["input_tokens"] == 500
    assert eff["output_tokens"] == 250
    assert eff["p50_latency_ms"] == 3000
    assert eff["p95_latency_ms"] == 10000
    assert eff["tokens_per_s"] == pytest.approx((50 + 25 + 50 / 3 + 12.5 + 5) / 5)


def test_efficiency_tolerates_missing_numbers():
    eff = runner.efficiency([
        {"input_tokens": None, "output_tokens": None, "latency_ms": None},
        {"input_tokens": 10, "output_tokens": 20, "latency_ms": 2000},
    ])
    assert eff["input_tokens"] == 10
    assert eff["p50_latency_ms"] == 2000
    assert eff["tokens_per_s"] == 10.0
    empty = runner.efficiency([{"latency_ms": None}])
    assert empty["p50_latency_ms"] is None and empty["tokens_per_s"] is None


def test_live_and_replay_report_efficiency_per_run(paths, alternating):
    live = runner.run_live(**paths, repeat=3)
    assert [r["run"] for r in live.per_run] == [0, 1, 2]
    assert all(r["input_tokens"] == 200 and r["output_tokens"] == 80 for r in live.per_run)
    assert live.totals["input_tokens"] == 600


# --- JSON report ---

def test_json_report_sits_next_to_the_markdown_and_round_trips(paths, alternating):
    result = runner.run_live(**paths, repeat=3)

    assert result.json_path is not None and result.report_path is not None
    assert result.json_path.with_suffix(".md") == result.report_path
    assert result.json_path.name.endswith("-live.json")
    loaded = json.loads(result.json_path.read_text())
    assert loaded == runner.report_dict(result, CASES)
    assert loaded["mode"] == "live" and loaded["repeat"] == 3
    assert loaded["model"] == "fake-model"
    assert loaded["prompt_version"] == classify.PROMPT_VERSION
    assert loaded["summary"]["priority"]["flip_rate"] == 0.5
    assert len(loaded["per_run"]) == 3
    first = loaded["cases"][0]
    assert first["id"] == "qa-001" and first["flipped"] == ["priority"]
    assert [r["run"] for r in first["runs"]] == [0, 1, 2]
    assert first["runs"][1]["scores"]["priority"] is False
    assert first["runs"][1]["output"]["priority"] == "Normal"


def test_markdown_report_shows_flip_rates_for_repeat_runs(paths, alternating):
    report = runner.run_live(**paths, repeat=3).report_path.read_text()
    assert "Flip rate" in report and "Worst run" in report
    assert "| priority | 83% | 50% | 50% |" in report
    assert "qa-001 — " in report and "(run 1)" in report


# --- CLI: --repeat / --model ---

def test_cli_repeat_live_then_replay(cli_paths, alternating, capsys):
    assert cli.main(["quick_add", "--live", "--repeat", "3"]) == 0
    assert cli.main(["quick_add", "--replay", "--repeat", "3"]) == 0
    out = capsys.readouterr().out
    assert "Flip rate" in out and ".json" in out


def test_cli_replay_repeat_missing_run_exits_1_naming_it(cli_paths, scripted, capsys):
    assert cli.main(["quick_add", "--live"]) == 0
    assert cli.main(["quick_add", "--replay", "--repeat", "2"]) == 1
    err = capsys.readouterr().err
    assert "qa-001 run 1" in err and "qa-002 run 1" in err


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_cli_rejects_a_bad_repeat(cli_paths, value):
    with pytest.raises(SystemExit) as exc:
        cli.main(["quick_add", "--replay", "--repeat", value])
    assert exc.value.code == 2


def test_cli_model_overrides_llm_model_for_this_run_only(cli_paths, monkeypatch, capsys):
    monkeypatch.setenv("LLM_MODEL", "model-a")
    lazy = llm._LazyAdapter()
    monkeypatch.setattr(llm, "adapter", lazy)
    recs = [{"case_id": c["id"], "key": runner.recording_key(c, "model-b"),
             "text": _reply(c["expected"]), "input_tokens": 1, "output_tokens": 1,
             "latency_ms": 1.0} for c in CASES]
    _write(cli_paths["recordings_path"], recs)

    assert cli.main(["quick_add", "--replay", "--model", "model-b"]) == 0

    assert "model-b" in capsys.readouterr().out
    assert llm.adapter is lazy
    assert lazy.model == "model-a"
    assert cli.main(["quick_add", "--replay"]) == 1  # model-a has no recordings
