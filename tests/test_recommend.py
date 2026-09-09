"""Behavioural spec for the recommendation engine (Slice B target).

These tests are written FIRST and are expected to fail until recommend() is
implemented. They assert observable behaviour, not exact weights, so they
stay stable while the scoring is tuned.

Run: pytest tests/test_recommend.py
"""
from datetime import date, datetime, timedelta

from recommend import Item, recommend

NOW = datetime(2026, 9, 8, 9, 0, 0)   # a fixed "now" so tests are deterministic


def make(id, title, **kw):
    defaults = dict(stream="task", priority="Normal", status="backlog",
                    effort_minutes=30, due_date=None, is_blocking=0,
                    mode="reactive", item_type="Delivery",
                    last_touched_at=NOW - timedelta(days=1))
    defaults.update(kw)
    return Item(id=id, title=title, **defaults)


def titles(recs):
    return [r.item.title for r in recs]


def test_time_fit_is_a_hard_filter():
    # 25 minutes available: a 120-min item must never appear in the now-list.
    items = [
        make(1, "Quick follow-up", effort_minutes=10),
        make(2, "Two-hour strategy doc", effort_minutes=120, priority="Critical"),
    ]
    recs = recommend(items, available_minutes=25, now=NOW)
    assert "Two-hour strategy doc" not in titles(recs)
    assert "Quick follow-up" in titles(recs)


def test_worked_example_ordering_with_25_minutes():
    items = [
        make(1, "Follow up with Product on migration scope",
             effort_minutes=10, is_blocking=3, priority="Important"),
        make(2, "Review hiring feedback", effort_minutes=20, priority="Important"),
        make(3, "Prepare exec update", effort_minutes=45, priority="Important",
             due_date=(NOW + timedelta(days=1)).date()),
    ]
    recs = recommend(items, available_minutes=25, now=NOW)
    names = titles(recs)
    # the 45-min item doesn't fit 25 minutes
    assert "Prepare exec update" not in names
    # the blocking item outranks the plain one
    assert names.index("Follow up with Product on migration scope") < \
        names.index("Review hiring feedback")


def test_overdue_critical_beats_distant_normal():
    items = [
        make(1, "Overdue critical", priority="Critical",
             due_date=(NOW - timedelta(days=2)).date()),
        make(2, "Someday normal", priority="Normal", due_date=None),
    ]
    recs = recommend(items, available_minutes=60, now=NOW)
    assert titles(recs)[0] == "Overdue critical"


def test_done_and_ignore_are_excluded():
    items = [
        make(1, "Finished", status="done"),
        make(2, "Noise", priority="Ignore"),
        make(3, "Real work"),
    ]
    recs = recommend(items, available_minutes=60, now=NOW)
    assert titles(recs) == ["Real work"]


def test_other_peoples_court_is_excluded():
    # delegation and waiting are not MY next actions
    items = [
        make(1, "I delegated this", stream="delegation"),
        make(2, "I'm waiting on this", stream="waiting"),
        make(3, "My task"),
        make(4, "My promise", stream="commitment"),
    ]
    recs = recommend(items, available_minutes=60, now=NOW)
    names = titles(recs)
    assert "I delegated this" not in names
    assert "I'm waiting on this" not in names
    assert "My task" in names and "My promise" in names


def test_blocking_item_is_surfaced_as_bottleneck():
    items = [
        make(1, "Unblock the team", is_blocking=3, effort_minutes=10),
        make(2, "Solo cleanup", is_blocking=0, effort_minutes=10),
    ]
    recs = recommend(items, available_minutes=60, now=NOW)
    assert titles(recs)[0] == "Unblock the team"


def test_nothing_fits_fallback_is_not_empty():
    # everything is bigger than the window: return smallest, marked fits=False
    items = [
        make(1, "Big A", effort_minutes=120),
        make(2, "Big B", effort_minutes=240),
    ]
    recs = recommend(items, available_minutes=15, now=NOW)
    assert len(recs) >= 1
    assert all(r.fits is False for r in recs)
    assert titles(recs)[0] == "Big A"   # smallest first


def test_respects_limit_and_is_sorted_descending():
    items = [make(i, f"Task {i}", priority="Normal") for i in range(10)]
    recs = recommend(items, available_minutes=60, now=NOW, limit=3)
    assert len(recs) == 3
    scores = [r.score for r in recs]
    assert scores == sorted(scores, reverse=True)


def test_every_recommendation_has_a_reason():
    items = [make(1, "With reason", effort_minutes=10, is_blocking=2)]
    recs = recommend(items, available_minutes=60, now=NOW)
    assert recs and all(r.reason and isinstance(r.reason, str) for r in recs)
