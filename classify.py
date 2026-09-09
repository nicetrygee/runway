"""AI extraction — freeform text into structured task fields.

Quick-add (AI) is optional — the app runs fine without an API key, the
feature just flashes an error if used unconfigured (see app.py's quick_add).
"""
import os
from datetime import datetime
from typing import Literal

import anthropic
from pydantic import BaseModel

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None


class ExtractedTask(BaseModel):
    title: str
    # Mirrors VALID_TASK_TYPES (app.py) — Literal members can't be built from a runtime list.
    task_type: Literal["incident", "rfc", "1on1", "hiring", "delivery", "other"]
    blast_radius: str
    sprint: str
    cognitive_load: int
    due_date: str
    notes: str
    # Slice A additions — mirror VALID_ITEM_TYPES / VALID_PRIORITIES / VALID_STREAMS /
    # VALID_MODES (app.py). Same Literal-mirrors-a-runtime-list constraint as task_type above.
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


class WeeklySummary(BaseModel):
    summary: str


def extract_task_from_text(text):
    """Turn a freeform note into structured task fields via Claude."""
    today = datetime.now().strftime("%Y-%m-%d")
    response = ai_client.messages.parse(
        model="claude-sonnet-5",
        max_tokens=1024,
        system=(
            "Extract a task from the user's freeform note for an engineering "
            f"manager's task tracker. Today's date is {today}. Resolve relative "
            "dates (e.g. 'Friday', 'next week') to YYYY-MM-DD; leave due_date as "
            "an empty string if no date is mentioned. cognitive_load is 1-5, how "
            "much headspace the task consumes — default to 2 if unclear. Leave "
            "blast_radius, sprint, and notes as empty strings if not mentioned.\n\n"
            "Also classify it against the EM's broader taxonomy:\n"
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
        ),
        messages=[{"role": "user", "content": text}],
        output_format=ExtractedTask,
    )
    return response.parsed_output


def generate_weekly_summary(tasks):
    """Turn a week's worth of completed tasks into a short prose recap via Claude."""
    task_lines = "\n".join(
        f"- [{t['task_type']}] {t['title']}"
        + (f" — {t['blast_radius']}" if t["blast_radius"] else "")
        for t in tasks
    )
    response = ai_client.messages.parse(
        model="claude-sonnet-5",
        max_tokens=512,
        system=(
            "Write a short, upbeat 2-4 sentence recap of the engineering work "
            "an engineering manager's team completed this week, suitable to "
            "skim or forward to their own manager. Group related items where "
            "it makes sense. Don't invent details beyond what's given."
        ),
        messages=[{"role": "user", "content": task_lines}],
        output_format=WeeklySummary,
    )
    return response.parsed_output.summary
