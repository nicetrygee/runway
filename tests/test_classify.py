from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

import classify


def _extracted_task(**overrides):
    fields = dict(
        title="Review the Q4 hiring plan",
        task_type="hiring",
        blast_radius="",
        sprint="",
        cognitive_load=2,
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


def test_extract_task_from_text_returns_new_em_fields(monkeypatch):
    fake_parsed = _extracted_task()
    fake_client = MagicMock()
    fake_client.messages.parse.return_value = SimpleNamespace(parsed_output=fake_parsed)
    monkeypatch.setattr(classify, "ai_client", fake_client)

    result = classify.extract_task_from_text("Sarah asked me to review the Q4 hiring plan")

    assert result.item_type == "Hiring"
    assert result.priority == "Important"
    assert result.effort_minutes == 30
    assert result.person == "Sarah"
    assert result.stream == "task"
    assert result.mode == "reactive"

    fake_client.messages.parse.assert_called_once()
    _, kwargs = fake_client.messages.parse.call_args
    assert kwargs["model"] == "claude-sonnet-5"
    assert kwargs["output_format"] is classify.ExtractedTask


def test_extract_task_from_text_no_person_is_empty_string(monkeypatch):
    fake_parsed = _extracted_task(person="", stream="waiting")
    fake_client = MagicMock()
    fake_client.messages.parse.return_value = SimpleNamespace(parsed_output=fake_parsed)
    monkeypatch.setattr(classify, "ai_client", fake_client)

    result = classify.extract_task_from_text("Blocked on infra approval")

    assert result.person == ""
    assert result.stream == "waiting"


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
