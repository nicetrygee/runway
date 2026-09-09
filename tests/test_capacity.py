"""Unit tests for the pure aggregation functions in capacity.py.

No DB, no Flask — plain dict rows in, computed aggregates out. Mirrors the
style of tests/test_recommend.py.
"""
from datetime import datetime

import capacity


def test_week_bounds_returns_monday_through_next_monday():
    # Wednesday 2026-09-09 -> week starts Monday 2026-09-07, ends the
    # following Monday 2026-09-14.
    now = datetime(2026, 9, 9, 15, 30, 0)
    start, end = capacity.week_bounds(now)
    assert start == "2026-09-07 00:00:00"
    assert end == "2026-09-14 00:00:00"


def test_week_bounds_on_a_monday_starts_that_day():
    now = datetime(2026, 9, 7, 8, 0, 0)
    start, end = capacity.week_bounds(now)
    assert start == "2026-09-07 00:00:00"
    assert end == "2026-09-14 00:00:00"


# --- weekly_counts ----------------------------------------------------------

def test_weekly_counts_tallies_completed_and_delegated_from_event_history():
    events = [
        {"event_type": "completed", "payload": None},
        {"event_type": "completed", "payload": None},
        {"event_type": "delegated", "payload": None},
        {"event_type": "created", "payload": None},
    ]
    counts = capacity.weekly_counts(events, waiting_items=[])
    assert counts["completed"] == 2
    assert counts["delegated"] == 1
    assert counts["carried_forward"] == 0


def test_weekly_counts_recognizes_carry_forward_touched_events():
    events = [
        {"event_type": "touched", "payload": '{"action": "carry_forward"}'},
        {"event_type": "touched", "payload": None},  # ordinary edit, not counted
        {"event_type": "touched", "payload": {"action": "carry_forward"}},  # dict form
    ]
    counts = capacity.weekly_counts(events, waiting_items=[])
    assert counts["carried_forward"] == 2


def test_weekly_counts_waiting_is_a_snapshot_of_waiting_items():
    waiting_items = [{"id": 1}, {"id": 2}]
    counts = capacity.weekly_counts(events=[], waiting_items=waiting_items)
    assert counts["waiting"] == 2


# --- time_breakdown / strategic_time_pct ------------------------------------

def test_time_breakdown_groups_by_item_type_and_mode():
    items = [
        {"effort_minutes": 60, "item_type": "Delivery", "mode": "reactive"},
        {"effort_minutes": 30, "item_type": "Delivery", "mode": "proactive"},
        {"effort_minutes": 120, "item_type": "Strategy", "mode": "proactive"},
    ]
    breakdown = capacity.time_breakdown(items)
    assert breakdown["by_item_type"] == {"Delivery": 90, "Strategy": 120}
    assert breakdown["by_mode"] == {"reactive": 60, "proactive": 150}


def test_time_breakdown_buckets_missing_item_type_as_other():
    items = [{"effort_minutes": 30, "item_type": None, "mode": "reactive"}]
    breakdown = capacity.time_breakdown(items)
    assert breakdown["by_item_type"] == {"Other": 30}


def test_time_breakdown_of_no_items_is_empty():
    breakdown = capacity.time_breakdown([])
    assert breakdown == {"by_item_type": {}, "by_mode": {}}


def test_strategic_time_pct_is_proactive_over_total():
    assert capacity.strategic_time_pct({"reactive": 60, "proactive": 60}) == 50.0
    assert capacity.strategic_time_pct({"reactive": 90, "proactive": 30}) == 25.0


def test_strategic_time_pct_with_no_effort_is_zero():
    assert capacity.strategic_time_pct({}) == 0.0


# --- capacity_read ------------------------------------------------------------

def test_capacity_read_under_100_percent():
    open_items = [
        {"stream": "task", "effort_minutes": 600},
        {"stream": "commitment", "effort_minutes": 600},
    ]
    result = capacity.capacity_read(open_items, available_hours=38)
    assert result["committed_hours"] == 20.0
    assert result["pct_committed"] == 53
    assert "committed" in result["summary"]


def test_capacity_read_over_100_percent():
    open_items = [{"stream": "task", "effort_minutes": 60 * 40}]
    result = capacity.capacity_read(open_items, available_hours=38)
    assert result["pct_committed"] > 100


def test_capacity_read_ignores_delegation_and_waiting_streams():
    open_items = [
        {"stream": "task", "effort_minutes": 60},
        {"stream": "delegation", "effort_minutes": 600},
        {"stream": "waiting", "effort_minutes": 600},
    ]
    result = capacity.capacity_read(open_items, available_hours=38)
    assert result["committed_hours"] == 1.0


def test_capacity_read_zero_available_hours_does_not_divide_by_zero():
    result = capacity.capacity_read([{"stream": "task", "effort_minutes": 60}], available_hours=0)
    assert result["pct_committed"] == 0


# --- delegation_suggestions ---------------------------------------------------

def test_delegation_suggestions_includes_explicit_delegate_priority():
    items = [{"priority": "Delegate", "effort_minutes": 5}]
    assert capacity.delegation_suggestions(items) == items


def test_delegation_suggestions_includes_high_effort_normal_priority():
    items = [{"priority": "Normal", "effort_minutes": 120}]
    assert capacity.delegation_suggestions(items) == items


def test_delegation_suggestions_excludes_low_effort_normal_priority():
    items = [{"priority": "Normal", "effort_minutes": 30}]
    assert capacity.delegation_suggestions(items) == []


def test_delegation_suggestions_excludes_high_effort_critical_priority():
    items = [{"priority": "Critical", "effort_minutes": 480}]
    assert capacity.delegation_suggestions(items) == []
