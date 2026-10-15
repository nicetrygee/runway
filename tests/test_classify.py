from datetime import datetime

import pytest
from conftest import read_traces
from pydantic import ValidationError

import classify


def _extracted_task(**overrides):
    fields = dict(
        title="Review the Q4 hiring plan",
        blast_radius="",
        sprint="",
        due_date="",
        notes="",
        item_type="Hiring",
        priority="Important",
        effort_minutes=30,
        person="Sarah",
        stream="task",
        mode="reactive",
    )
    fields.update(overrides)
    return classify.ExtractedTask(**fields)


def test_extract_task_from_text_returns_new_em_fields(fake_llm):
    fake_llm.text = _extracted_task().model_dump_json()

    result = classify.extract_task_from_text("Sarah asked me to review the Q4 hiring plan")

    assert isinstance(result, classify.ExtractedTask)
    assert result.item_type == "Hiring"
    assert result.priority == "Important"
    assert result.effort_minutes == 30
    assert result.person == "Sarah"
    assert result.stream == "task"
    assert result.mode == "reactive"

    (call,) = fake_llm.calls
    assert call["schema"] is classify.ExtractedTask
    assert call["messages"][-1] == {
        "role": "user", "content": "Sarah asked me to review the Q4 hiring plan",
    }


def test_extract_task_from_text_no_person_is_empty_string(fake_llm):
    fake_llm.text = _extracted_task(person="", stream="waiting").model_dump_json()

    result = classify.extract_task_from_text("Blocked on infra approval")

    assert result.person == ""
    assert result.stream == "waiting"


def test_extract_task_from_text_now_fixes_today_in_prompt(fake_llm):
    fake_llm.text = _extracted_task().model_dump_json()

    classify.extract_task_from_text("do it Friday", now=datetime(2026, 10, 16, 9, 30))

    system = fake_llm.calls[0]["messages"][0]
    assert system["role"] == "system"
    assert "Today's date is 2026-10-16." in system["content"]


def test_extract_task_from_text_defaults_now_to_clock(fake_llm, monkeypatch):
    monkeypatch.setattr(classify.clock, "utc_now", lambda: datetime(2027, 1, 2, 3, 4))
    fake_llm.text = _extracted_task().model_dump_json()

    classify.extract_task_from_text("do it Friday")

    assert "Today's date is 2027-01-02." in fake_llm.calls[0]["messages"][0]["content"]


def test_extract_task_from_text_disables_thinking(fake_llm):
    fake_llm.text = _extracted_task().model_dump_json()

    classify.extract_task_from_text("note")

    assert fake_llm.calls[0]["messages"][0]["content"].startswith("/no_think")
    assert fake_llm.calls[0]["settings"].thinking is False


def test_extract_task_from_text_is_traced_with_prompt_version(fake_llm):
    fake_llm.text = _extracted_task().model_dump_json()

    classify.extract_task_from_text("note")

    (row,) = read_traces()
    assert row["feature"] == "quick_add"
    assert row["prompt_version"] == classify.PROMPT_VERSION
    assert row["success"] == 1


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("item_type", "NotARealType"),
        ("priority", "Whenever"),
        ("effort_minutes", 45),
        ("stream", "someday"),
        ("mode", "chill"),
    ],
)
def test_extracted_task_rejects_invalid_enum_values(field, bad_value):
    with pytest.raises(ValidationError):
        _extracted_task(**{field: bad_value})
