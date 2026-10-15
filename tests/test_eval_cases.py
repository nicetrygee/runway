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
GROUPS = {"normal", "edge"}

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
    assert keys <= CASE_KEYS | OPTIONAL_CASE_KEYS, f"unknown keys: {keys - CASE_KEYS - OPTIONAL_CASE_KEYS}"

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
