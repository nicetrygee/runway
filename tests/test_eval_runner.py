"""Live / replay eval runner (slice E0.3). No network: a scripted adapter
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
