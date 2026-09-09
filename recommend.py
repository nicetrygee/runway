"""Runway recommendation engine — "What should I do now?"

PURE MODULE. No DB, no Flask, no network, no LLM. Inputs are plain data,
output is a ranked list. This is what makes it unit-testable and the ideal
test-first target: implement against tests/test_recommend.py (Slice B).

Design contract (see docs/technical-design.md):
  - Candidates are items in MY court: stream in {'task','commitment'},
    status != 'done', priority != 'Ignore'.
  - Time-fit is a HARD filter, not a weight: an item whose effort_minutes
    exceeds available_minutes is not eligible for the "now" list. If nothing
    fits, fall back to the smallest items with fits=False and an explanatory
    reason (never return an empty screen when work exists).
  - Score = weighted sum of normalised signals: priority, due urgency,
    blocking (bottleneck), staleness (neglect), minus a context-switch penalty.
  - Weights are tunable constants below; keep them here, in one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

# --- tunable weights (start by hand; a later slice can learn these) --------
W_PRIORITY = 4.0
W_DUE = 3.0
W_BLOCK = 3.0
W_NEGLECT = 1.0
W_SWITCH = 0.5

PRIORITY_WEIGHT = {"Critical": 3.0, "Important": 2.0, "Normal": 1.0, "Delegate": 0.5}
# 'Ignore' is excluded upstream and intentionally absent here.

CANDIDATE_STREAMS = ("task", "commitment")

# canonical effort minutes <-> UI labels
EFFORT_LABELS = {5: "5m", 30: "30m", 60: "1h", 120: "2h", 240: "half-day", 480: "multi-day"}


@dataclass
class Item:
    id: int
    title: str
    stream: str                     # task | commitment | delegation | waiting
    priority: str                   # Critical | Important | Normal | Delegate | Ignore
    status: str                     # backlog | in_progress | blocked | done
    effort_minutes: Optional[int] = None
    due_date: Optional[date] = None
    is_blocking: int = 0
    mode: str = "reactive"          # reactive | proactive
    item_type: Optional[str] = None
    last_touched_at: Optional[datetime] = None


@dataclass
class Recommendation:
    item: Item
    score: float
    reason: str                     # human-readable, e.g. "10 min · blocking 3 people"
    fits: bool                      # False only in the nothing-fits fallback


def recommend(
    items: list[Item],
    available_minutes: int,
    now: datetime,
    limit: int = 5,
    recent_item_type: Optional[str] = None,
) -> list[Recommendation]:
    """Return up to `limit` recommended next actions, best first.

    See the module docstring for the contract.
    """
    candidates = [i for i in items if _is_candidate(i)]
    fitting = [i for i in candidates if _fits(i, available_minutes)]

    if fitting:
        recs = [
            Recommendation(
                item=i,
                score=_score(i, now, recent_item_type),
                reason=_reason(i, now, fits=True),
                fits=True,
            )
            for i in fitting
        ]
        recs.sort(key=lambda r: r.score, reverse=True)
        return recs[:limit]

    if not candidates:
        return []

    # nothing fits: fall back to the smallest items rather than an empty screen
    smallest = sorted(
        candidates,
        key=lambda i: i.effort_minutes if i.effort_minutes is not None else float("inf"),
    )
    return [
        Recommendation(
            item=i,
            score=_score(i, now, recent_item_type),
            reason=_reason(i, now, fits=False),
            fits=False,
        )
        for i in smallest[:limit]
    ]


def _is_candidate(item: Item) -> bool:
    return (
        item.stream in CANDIDATE_STREAMS
        and item.status != "done"
        and item.priority != "Ignore"
    )


def _fits(item: Item, available_minutes: int) -> bool:
    return item.effort_minutes is None or item.effort_minutes <= available_minutes


def _score(item: Item, now: datetime, recent_item_type: Optional[str]) -> float:
    return (
        W_PRIORITY * priority_weight(item.priority)
        + W_DUE * urgency(item.due_date, now)
        + W_BLOCK * blocking(item.is_blocking)
        + W_NEGLECT * staleness(item.last_touched_at, now)
        - W_SWITCH * context_switch_penalty(item, recent_item_type)
    )


def _due_phrase(due_date: Optional[date], now: datetime) -> Optional[str]:
    if due_date is None:
        return None
    days = (due_date - now.date()).days
    if days < 0:
        return f"overdue by {-days}d"
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    return f"due in {days}d"


def _reason(item: Item, now: datetime, fits: bool) -> str:
    parts = [effort_label(item.effort_minutes)]
    if item.is_blocking:
        who = "person" if item.is_blocking == 1 else "people"
        parts.append(f"blocking {item.is_blocking} {who}")
    due_phrase = _due_phrase(item.due_date, now)
    if due_phrase:
        parts.append(due_phrase)
    if not fits:
        parts.append("doesn't fit your window")
    return " · ".join(parts)


# --- signal helpers ----------------------------------------------------
def priority_weight(priority: str) -> float:
    return PRIORITY_WEIGHT.get(priority, 0.0)


def urgency(due_date: Optional[date], now: datetime) -> float:
    """0 when no/na deadline; rises as due nears; max when overdue."""
    if due_date is None:
        return 0.0
    days_until = (due_date - now.date()).days
    if days_until <= 0:
        return 1.0
    return max(0.0, 1.0 - days_until / 7.0)


def blocking(is_blocking: int) -> float:
    if not is_blocking:
        return 0.0
    return min(is_blocking / 5.0, 1.0)


def staleness(last_touched_at: Optional[datetime], now: datetime) -> float:
    if last_touched_at is None:
        return 1.0
    days_since = (now - last_touched_at).total_seconds() / 86400.0
    return max(0.0, min(days_since / 14.0, 1.0))


def context_switch_penalty(item: Item, recent_item_type: Optional[str]) -> float:
    if recent_item_type is None or item.item_type is None:
        return 0.0
    return 1.0 if item.item_type != recent_item_type else 0.0


def effort_label(effort_minutes: Optional[int]) -> str:
    if effort_minutes is None:
        return "unestimated"
    return EFFORT_LABELS.get(effort_minutes, f"{effort_minutes}m")
