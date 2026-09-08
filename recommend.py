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

    See the module docstring for the contract. Implement in Slice B.
    """
    raise NotImplementedError("Slice B: implement recommend() to pass tests/test_recommend.py")


# --- signal helpers (implement alongside recommend) ------------------------
def priority_weight(priority: str) -> float:
    raise NotImplementedError


def urgency(due_date: Optional[date], now: datetime) -> float:
    """0 when no/na deadline; rises as due nears; max when overdue."""
    raise NotImplementedError


def blocking(is_blocking: int) -> float:
    raise NotImplementedError


def staleness(last_touched_at: Optional[datetime], now: datetime) -> float:
    raise NotImplementedError


def context_switch_penalty(item: Item, recent_item_type: Optional[str]) -> float:
    raise NotImplementedError


def effort_label(effort_minutes: Optional[int]) -> str:
    if effort_minutes is None:
        return "unestimated"
    return EFFORT_LABELS.get(effort_minutes, f"{effort_minutes}m")
