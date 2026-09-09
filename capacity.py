"""Weekly review + capacity aggregations (Slice D).

PURE MODULE, same discipline as recommend.py: no DB, no Flask, no network.
Every function here takes plain rows (dicts, as returned by cs50.SQL) or
plain values and returns computed aggregates. All queries live in db.py /
events.py; app.py fetches the rows and calls these functions to turn them
into the numbers the /review page renders. This keeps the aggregation
logic densely unit-testable without a database, exactly like recommend.py.

See docs/technical-design.md, "Capacity + weekly review (feature 5)", and
docs/slices/slice-d-review.md for the acceptance criteria.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

# Items in MY court — same definition recommend.py uses for "my next actions".
MY_STREAMS = ("task", "commitment")

# "high-effort" threshold (minutes) for the low-priority delegation suggestion.
DEFAULT_EFFORT_THRESHOLD = 120

DEFAULT_AVAILABLE_HOURS = 38


def week_bounds(now: datetime) -> tuple[str, str]:
    """The [start, end) of now's calendar week — Monday 00:00 through the
    following Monday 00:00 — as SQLite-comparable timestamp strings.
    Matches the 'YYYY-MM-DD HH:MM:SS' format CURRENT_TIMESTAMP writes, so
    the bounds compare correctly against created_at/updated_at columns.
    """
    start_date = now.date() - timedelta(days=now.weekday())
    start = datetime.combine(start_date, datetime.min.time())
    end = start + timedelta(days=7)
    fmt = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), end.strftime(fmt)


def weekly_counts(events: list[dict], waiting_items: list[dict]) -> dict:
    """completed / carried_forward / delegated / waiting counts for the week.

    completed and delegated are read from event history (event_type
    'completed' / 'delegated'); carried_forward is the 'touched' event
    carry_forward_item() writes, disambiguated by payload. waiting has no
    matching event type in the fixed events.event_type enum (no "entered
    waiting" event exists to log), so it's a snapshot of currently-waiting
    items instead — pass the current stream='waiting' rows for that.
    """
    completed = 0
    carried_forward = 0
    delegated = 0
    for event in events:
        event_type = event["event_type"]
        if event_type == "completed":
            completed += 1
        elif event_type == "delegated":
            delegated += 1
        elif event_type == "touched" and is_carry_forward_event(event):
            carried_forward += 1

    return {
        "completed": completed,
        "carried_forward": carried_forward,
        "delegated": delegated,
        "waiting": len(waiting_items),
    }


def is_carry_forward_event(event: dict) -> bool:
    payload = event.get("payload")
    if not payload:
        return False
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return False
    return isinstance(payload, dict) and payload.get("action") == "carry_forward"


def time_breakdown(completed_items: list[dict]) -> dict:
    """Sum effort_minutes for the week's completed items, grouped by
    item_type and by mode. NULL/missing item_type buckets as 'Other'.
    """
    by_item_type: dict[str, int] = {}
    by_mode: dict[str, int] = {}
    for item in completed_items:
        minutes = item.get("effort_minutes") or 0
        item_type = item.get("item_type") or "Other"
        mode = item.get("mode") or "reactive"
        by_item_type[item_type] = by_item_type.get(item_type, 0) + minutes
        by_mode[mode] = by_mode.get(mode, 0) + minutes
    return {"by_item_type": by_item_type, "by_mode": by_mode}


def strategic_time_pct(by_mode: dict) -> float:
    """Proactive effort / total effort, as a percentage. 0 if no effort logged."""
    total = sum(by_mode.values())
    if total == 0:
        return 0.0
    proactive = by_mode.get("proactive", 0)
    return round(proactive / total * 100, 1)


def capacity_read(open_items: list[dict], available_hours: float) -> dict:
    """committed effort (mine, still open) vs available_hours -> a capacity read."""
    committed_minutes = sum(
        item.get("effort_minutes") or 0
        for item in open_items
        if item.get("stream") in MY_STREAMS
    )
    committed_hours = round(committed_minutes / 60, 1)
    available_hours = round(available_hours, 1)
    pct_committed = (
        round(committed_hours / available_hours * 100) if available_hours > 0 else 0
    )
    return {
        "committed_hours": committed_hours,
        "available_hours": available_hours,
        "pct_committed": pct_committed,
        "summary": f"{pct_committed}% committed — {committed_hours}h committed / {available_hours}h available",
    }


def delegation_suggestions(open_items: list[dict], effort_threshold: int = DEFAULT_EFFORT_THRESHOLD) -> list[dict]:
    """Items worth delegating: explicitly flagged Delegate, or high-effort
    Normal-priority items that don't need the EM's own hands.
    """
    suggestions = []
    for item in open_items:
        priority = item.get("priority")
        effort = item.get("effort_minutes") or 0
        if priority == "Delegate":
            suggestions.append(item)
        elif priority == "Normal" and effort >= effort_threshold:
            suggestions.append(item)
    return suggestions
