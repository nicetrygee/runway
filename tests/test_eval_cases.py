"""Schema validation for the Quick Add eval cases (slice E0.2).

Allowed values are copied from docs/slices/slice-e0-skeleton.md, not imported
from app.py, so the cases stay independent of the feature code.
"""

import datetime
import json
import re
from pathlib import Path

import pytest

CASES_PATH = Path(__file__).resolve().parent.parent / "evals" / "quick_add" / "cases.jsonl"

ITEM_TYPES = {"People", "Delivery", "Technical", "Stakeholder", "Strategy",
              "Hiring", "Operational", "Personal-admin"}
PRIORITIES = {"Critical", "Important", "Normal", "Delegate", "Ignore"}
EFFORTS = {5, 30, 60, 120, 240, 480}
STREAMS = {"task", "commitment", "delegation", "waiting"}
MODES = {"reactive", "proactive"}
SOURCES = {"handwritten", "generated"}
GROUPS = {"normal", "edge", "failure", "adversarial"}

CASE_KEYS = {"id", "today", "note", "group", "source", "expected"}
OPTIONAL_CASE_KEYS = {"acceptable"}
EXPECTED_KEYS = {"title", "item_type", "priority", "effort_minutes", "person",
                 "stream", "mode", "due_date"}
ACCEPTABLE_FIELDS = {"priority": PRIORITIES, "mode": MODES, "item_type": ITEM_TYPES}


def _load_cases():
    lines = CASES_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _is_iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def test_cases_file_exists_and_parses():
    assert CASES_PATH.exists()
    assert len(_load_cases()) >= 10


def test_case_ids_are_unique_and_well_formed():
    ids = [case["id"] for case in _load_cases()]
    assert len(ids) == len(set(ids))
    for case_id in ids:
        assert re.fullmatch(r"qa-\d{3}", case_id), case_id


@pytest.mark.parametrize("case", _load_cases() if CASES_PATH.exists() else [],
                         ids=lambda c: c.get("id", "?"))
def test_case_matches_schema(case):
    keys = set(case)
    assert CASE_KEYS <= keys, f"missing keys: {CASE_KEYS - keys}"
    unknown = keys - CASE_KEYS - OPTIONAL_CASE_KEYS
    assert not unknown, f"unknown keys: {unknown}"

    assert _is_iso_date(case["today"])
    assert isinstance(case["note"], str) and case["note"].strip()
    assert case["group"] in GROUPS
    assert case["source"] in SOURCES

    expected = case["expected"]
    assert set(expected) == EXPECTED_KEYS
    assert isinstance(expected["title"], str) and expected["title"].strip()
    assert expected["item_type"] in ITEM_TYPES
    assert expected["priority"] in PRIORITIES
    assert expected["effort_minutes"] in EFFORTS
    assert type(expected["effort_minutes"]) is int
    assert isinstance(expected["person"], str)
    assert expected["stream"] in STREAMS
    assert expected["mode"] in MODES
    assert expected["due_date"] == "" or _is_iso_date(expected["due_date"])

    for field, values in case.get("acceptable", {}).items():
        assert field in ACCEPTABLE_FIELDS, f"acceptable not allowed for {field}"
        assert isinstance(values, list) and values
        assert set(values) <= ACCEPTABLE_FIELDS[field]
        assert expected[field] in values, f"expected {field} missing from acceptable"


def test_cases_cover_the_required_mix():
    cases = _load_cases()
    expected = [case["expected"] for case in cases]
    assert sum(1 for e in expected if e["due_date"]) >= 3
    assert sum(1 for e in expected if e["person"] == "") >= 2
    assert sum(1 for e in expected if e["stream"] in {"commitment", "waiting"}) >= 2
    assert sum(1 for case in cases if case["group"] == "edge") >= 1


def _count(values, wanted):
    return {value: sum(1 for v in values if v == value) for value in wanted}


def test_cases_meet_the_e1b_coverage():
    """Minimum coverage from docs/slices/slice-e1b-eval-depth.md (E1b.3, D26, D27)."""
    cases = _load_cases()
    expected = [case["expected"] for case in cases]
    assert len(cases) >= 40

    groups = _count([case["group"] for case in cases], GROUPS)
    assert groups["normal"] + groups["edge"] >= 25, groups
    assert groups["failure"] >= 8, groups
    assert groups["adversarial"] >= 7, groups

    for field, values, minimum in [("item_type", ITEM_TYPES, 2), ("stream", STREAMS, 4),
                                   ("priority", PRIORITIES, 2)]:
        counts = _count([e[field] for e in expected], values)
        assert all(n >= minimum for n in counts.values()), (field, counts)

    people = [e["person"] for e in expected]
    teams = [p for p in people if p.endswith(" team")]
    full_names = [p for p in people if " " in p and p not in teams]
    assert sum(1 for p in people if p == "") >= 5
    assert len(full_names) >= 3, full_names
    assert len(teams) >= 2, teams


def test_cases_exercise_the_due_date_rules():
    """Each due-date rule the E1b.3 spec names has at least one case."""
    dated = [(datetime.date.fromisoformat(case["today"]),
              datetime.date.fromisoformat(case["expected"]["due_date"]),
              case["note"].lower())
             for case in _load_cases() if case["expected"]["due_date"]]
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    def found(check):
        return any(check(today, due, note) for today, due, note in dated)

    # DD1: a weekday said on that weekday is today.
    assert found(lambda t, d, n: d == t and weekdays[t.weekday()] in n)
    # DD4: end of month.
    assert found(lambda t, d, n: "end of month" in n)
    # DD3: next week.
    assert found(lambda t, d, n: "next week" in n)
    # DD5: ordinal day.
    assert found(lambda t, d, n: re.search(r"\bthe \d{1,2}(st|nd|rd|th)\b", n))
    # Crossing a month boundary, and a year boundary (DD8).
    assert found(lambda t, d, n: d.year == t.year and d.month != t.month)
    assert found(lambda t, d, n: d.year > t.year)
