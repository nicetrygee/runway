"""AI extraction — freeform text into structured task fields.

Quick Add and the weekly summary both go through `llm.complete` (local model
via LM Studio, traced to traces.db); if the model is unreachable the routes
fall back to the blank form / plain list (see app.py).
"""
from typing import Literal

from pydantic import BaseModel

import clock
import llm


class ExtractedTask(BaseModel):
    title: str
    blast_radius: str
    sprint: str
    due_date: str
    notes: str
    # Mirror VALID_ITEM_TYPES / VALID_PRIORITIES / VALID_STREAMS / VALID_MODES
    # (app.py) — Literal members can't be built from a runtime list.
    item_type: Literal[
        "People", "Delivery", "Technical", "Stakeholder", "Strategy",
        "Hiring", "Operational", "Personal-admin",
    ]
    priority: Literal["Critical", "Important", "Normal", "Delegate", "Ignore"]
    # Canonical minutes directly (5m/30m/1h/2h/half-day/multi-day -> 5/30/60/120/240/480)
    # so the Literal itself enforces a valid bucket.
    effort_minutes: Literal[5, 30, 60, 120, 240, 480]
    person: str  # empty string if no one is named
    stream: Literal["task", "commitment", "delegation", "waiting"]
    mode: Literal["reactive", "proactive"]


# Bump whenever the Quick Add system prompt changes: replay recordings are
# keyed on it, so a new version needs a fresh live eval run.
PROMPT_VERSION = "quick_add.v1"
WEEKLY_SUMMARY_PROMPT_VERSION = "weekly_summary.v1"


class WeeklySummary(BaseModel):
    summary: str


def extract_task_from_text(text, now=None):
    """Turn a freeform note into structured task fields via the local model.

    `now` fixes "today" for resolving relative dates (evals pass a fixed one);
    it defaults to clock.utc_now().
    """
    today = (now or clock.utc_now()).strftime("%Y-%m-%d")
    system = (
        "/no_think\n"
        "Extract a task from the user's freeform note for an engineering "
        f"manager's task tracker. Today's date is {today}. Resolve relative "
        "dates (e.g. 'Friday', 'next week') to YYYY-MM-DD; leave due_date as "
        "an empty string if no date is mentioned. Leave blast_radius, sprint, "
        "and notes as empty strings if not mentioned.\n\n"
        "Also classify it:\n"
        "- item_type: the single best fit among People, Delivery, Technical, "
        "Stakeholder, Strategy, Hiring, Operational, Personal-admin.\n"
        "- priority: Critical, Important, Normal, Delegate, or Ignore — how "
        "urgently this deserves attention.\n"
        "- effort_minutes: how long this will take, mapped to exactly one of "
        "5 (5m), 30 (30m), 60 (1h), 120 (2h), 240 (half-day), 480 (multi-day) "
        "— pick the closest bucket.\n"
        "- person: the name of anyone the note is about or addressed to "
        "(e.g. who asked, who it's owed to, who it's delegated to), or an "
        "empty string if no one is named.\n"
        "- stream: 'commitment' if the note is a promise the user made to "
        "someone else, 'delegation' if the user is handing this off to "
        "someone else to do, 'waiting' if the user is blocked pending "
        "someone or something else, otherwise 'task' (the user's own work).\n"
        "- mode: 'proactive' for strategic, planned, non-urgent work; "
        "'reactive' for everything else (the common case)."
    )
    result = llm.complete(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        schema=ExtractedTask,
        feature="quick_add",
        prompt_version=PROMPT_VERSION,
    )
    return result.parsed


def generate_weekly_summary(tasks):
    """Turn a week's worth of completed tasks into a short prose recap via the local model."""
    task_lines = "\n".join(
        f"- [{t['item_type'] or 'Untyped'}] {t['title']}"
        + (f" — {t['blast_radius']}" if t["blast_radius"] else "")
        for t in tasks
    )
    system = (
        "/no_think\n"
        "Write a short, upbeat 2-4 sentence recap of the engineering work "
        "an engineering manager's team completed this week, suitable to "
        "skim or forward to their own manager. Group related items where "
        "it makes sense. Don't invent details beyond what's given."
    )
    result = llm.complete(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": task_lines},
        ],
        schema=WeeklySummary,
        feature="weekly_summary",
        prompt_version=WEEKLY_SUMMARY_PROMPT_VERSION,
    )
    return result.parsed.summary
