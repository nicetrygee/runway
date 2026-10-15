"""Per-field scorers for the Quick Add eval (slice E0.3)."""
import pytest

from evals import scoring

EXPECTED = {
    "title": "Review Kestrel API design doc",
    "item_type": "Technical",
    "priority": "Important",
    "effort_minutes": 60,
    "person": "Dev",
    "stream": "commitment",
    "mode": "reactive",
    "due_date": "2026-10-16",
}


def _case(acceptable=None):
    case = {"id": "qa-x", "expected": dict(EXPECTED)}
    if acceptable is not None:
        case["acceptable"] = acceptable
    return case


def _output(**overrides):
    out = dict(EXPECTED, blast_radius="", sprint="", notes="")
    out.update(overrides)
    return out


# --- exact match: person, due_date, stream ---

def test_exact_match_passes_on_identical_value():
    assert scoring.score_exact("Dev", "Dev")


def test_exact_match_is_case_sensitive_and_strict():
    assert not scoring.score_exact("dev", "Dev")
    assert not scoring.score_exact("Dev Patel", "Dev")


def test_exact_match_empty_string_means_none():
    assert scoring.score_exact("", "")
    assert not scoring.score_exact("Dev", "")
    assert not scoring.score_exact("", "2026-10-16")


# --- effort: within one step ---

@pytest.mark.parametrize("actual,expected", [
    (60, 60), (30, 60), (120, 60), (5, 30), (480, 240), (5, 5), (480, 480),
])
def test_effort_within_one_step_passes(actual, expected):
    assert scoring.score_effort(actual, expected)


@pytest.mark.parametrize("actual,expected", [
    (5, 60), (240, 60), (5, 480), (30, 120), (7, 5),
])
def test_effort_two_or_more_steps_or_off_scale_fails(actual, expected):
    assert not scoring.score_effort(actual, expected)


# --- judgement: exact or in acceptable ---

def test_judgement_exact_match_passes_without_acceptable():
    assert scoring.score_judgement("Important", "Important", None)


def test_judgement_mismatch_fails_without_acceptable():
    assert not scoring.score_judgement("Normal", "Important", None)


def test_judgement_value_in_acceptable_passes():
    assert scoring.score_judgement("Normal", "Important", ["Important", "Normal"])


def test_judgement_value_outside_acceptable_fails():
    assert not scoring.score_judgement("Critical", "Important", ["Important", "Normal"])


# --- whole case ---

def test_score_case_scores_exactly_the_seven_fields():
    scores = scoring.score_case(_case(), _output())
    assert set(scores) == set(scoring.SCORED_FIELDS)
    assert set(scoring.SCORED_FIELDS) == {
        "item_type", "priority", "effort_minutes", "person", "stream", "mode", "due_date",
    }
    assert all(scores.values())


def test_free_text_fields_are_not_scored():
    scores = scoring.score_case(_case(), _output(title="Something else", notes="x"))
    assert "title" not in scores and "notes" not in scores
    assert all(scores.values())


def test_score_case_uses_acceptable_per_field():
    case = _case(acceptable={"priority": ["Important", "Normal"]})
    scores = scoring.score_case(case, _output(priority="Normal", mode="proactive"))
    assert scores["priority"] is True
    assert scores["mode"] is False


def test_score_case_applies_the_right_rule_to_each_field():
    scores = scoring.score_case(
        _case(), _output(effort_minutes=120, person="dev", due_date="2026-10-17"),
    )
    assert scores["effort_minutes"] is True
    assert scores["person"] is False
    assert scores["due_date"] is False


def test_score_case_with_no_output_fails_every_field():
    scores = scoring.score_case(_case(), None)
    assert set(scores) == set(scoring.SCORED_FIELDS)
    assert not any(scores.values())


# --- summary ---

def test_summarise_gives_per_field_accuracy():
    results = [
        {"item_type": True, "priority": True},
        {"item_type": False, "priority": True},
        {"item_type": False, "priority": True},
        {"item_type": True, "priority": False},
    ]
    assert scoring.summarise(results) == {"item_type": 0.5, "priority": 0.75}


def test_summarise_of_no_results_is_empty():
    assert scoring.summarise([]) == {}
