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
            "blast_radius, sprint, and notes as empty strings if not mentioned."
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
