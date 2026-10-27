import json
from datetime import datetime

from pydantic import BaseModel

import classify
import prompts


class Small(BaseModel):
    summary: str


class SmallPlusField(BaseModel):
    summary: str
    tone: str


def _registry(template="Say hi.", schema=Small, version="demo.v1", has_evals=True):
    return {"demo": prompts.Prompt(version, template, schema, has_evals=has_evals)}


# --- The gate: the committed lock matches the prompts in code ---

def test_lock_matches_current_prompts():
    problems = prompts.check(prompts.load_lock())
    assert problems == [], "\n".join(problems)


def test_locked_versions_have_distinct_hashes():
    hashes = list(prompts.load_lock().values())
    assert len(hashes) == len(set(hashes))


def test_registry_versions_are_prefixed_by_feature():
    for feature, prompt in prompts.REGISTRY.items():
        assert prompt.version.startswith(f"{feature}."), (feature, prompt.version)


def test_features_send_their_registered_template(fake_llm):
    fake_llm.text = json.dumps({
        "title": "t", "blast_radius": "", "sprint": "", "due_date": "", "notes": "",
        "item_type": "Technical", "priority": "Normal", "effort_minutes": 30,
        "person": "", "stream": "task", "mode": "reactive",
    })
    classify.extract_task_from_text("note", now=datetime(2026, 10, 15))
    fake_llm.text = classify.WeeklySummary(summary="ok").model_dump_json()
    classify.generate_weekly_summary(
        [{"title": "Ship it", "item_type": "Delivery", "blast_radius": ""}]
    )

    quick_add_system = fake_llm.calls[0]["messages"][0]["content"]
    summary_system = fake_llm.calls[1]["messages"][0]["content"]
    assert quick_add_system == classify.QUICK_ADD_SYSTEM.format(today="2026-10-15")
    assert summary_system == classify.WEEKLY_SUMMARY_SYSTEM


# --- prompt_hash ---

def test_hash_is_stable():
    assert prompts.prompt_hash("Say hi.", Small) == prompts.prompt_hash("Say hi.", Small)


def test_hash_changes_with_one_template_character():
    assert prompts.prompt_hash("Say hi.", Small) != prompts.prompt_hash("Say hi!", Small)


def test_hash_changes_with_schema_field():
    assert prompts.prompt_hash("Say hi.", Small) != prompts.prompt_hash("Say hi.", SmallPlusField)


# --- check ---

def test_check_reports_drift_with_instructions():
    lock = {"demo.v1": prompts.prompt_hash("Say hi.", Small)}

    (message,) = prompts.check(lock, _registry(template="Say hello."))

    assert "demo" in message and "demo.v1" in message
    assert "python -m prompts lock" in message
    assert "python -m evals demo --live" in message


def test_check_drift_without_evals_skips_rerecord_step():
    lock = {"demo.v1": prompts.prompt_hash("Say hi.", Small)}

    (message,) = prompts.check(lock, _registry(template="Say hello.", has_evals=False))

    assert "python -m prompts lock" in message
    assert "evals" not in message


def test_check_reports_missing_version():
    (message,) = prompts.check({}, _registry())

    assert "demo.v1" in message
    assert "python -m prompts lock" in message


def test_check_passes_when_lock_matches():
    lock = {"demo.v1": prompts.prompt_hash("Say hi.", Small)}

    assert prompts.check(lock, _registry()) == []


# --- lock ---

def test_lock_writes_current_entries(tmp_path):
    path = tmp_path / "prompts.lock.json"

    assert prompts.lock(path, _registry()) == 0

    assert json.loads(path.read_text()) == {"demo.v1": prompts.prompt_hash("Say hi.", Small)}
    assert path.read_text().endswith("\n")


def test_lock_keeps_old_versions(tmp_path):
    path = tmp_path / "prompts.lock.json"
    prompts.lock(path, _registry())

    prompts.lock(path, _registry(template="Say hello.", version="demo.v2"))

    assert set(json.loads(path.read_text())) == {"demo.v1", "demo.v2"}


def test_lock_is_idempotent(tmp_path):
    path = tmp_path / "prompts.lock.json"
    prompts.lock(path, _registry())
    first = path.read_text()

    prompts.lock(path, _registry())

    assert path.read_text() == first


def test_lock_refuses_to_rebless_an_edited_prompt(tmp_path, capsys):
    path = tmp_path / "prompts.lock.json"
    prompts.lock(path, _registry())
    before = path.read_text()

    assert prompts.lock(path, _registry(template="Say hello.")) == 1

    assert path.read_text() == before
    assert "demo.v1" in capsys.readouterr().err


def test_cli_lock_uses_lock_path(tmp_path, monkeypatch):
    path = tmp_path / "prompts.lock.json"
    monkeypatch.setattr(prompts, "LOCK_PATH", path)
    monkeypatch.setattr(prompts, "REGISTRY", _registry())

    assert prompts.main(["lock"]) == 0
    assert "demo.v1" in json.loads(path.read_text())
