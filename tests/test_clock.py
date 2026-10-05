"""One clock throughout: routes compare against UTC timestamps written by
SQLite's CURRENT_TIMESTAMP, so they must use clock.utc_now(), not local time.

The route tests run with the process in Sydney time and the clock pinned to
Sunday 20:00 UTC, which is already Monday 06:00 in Sydney. A route that used
local time would put that moment in the following week / on the following day.
"""
import time
from datetime import datetime

import pytest

import app as app_module
import clock
import db as db_module

SUNDAY_EVENING_UTC = datetime(2025, 6, 1, 20, 0, 0)  # Mon 06:00 in Sydney


@pytest.fixture
def sydney_time(monkeypatch):
    monkeypatch.setenv("TZ", "Australia/Sydney")
    time.tzset()
    monkeypatch.setattr(clock, "utc_now", lambda: SUNDAY_EVENING_UTC)
    yield
    monkeypatch.undo()
    time.tzset()


def _user_id(username="alice"):
    return db_module.get_user_by_username(username)[0]["id"]


def test_utc_now_matches_sqlite_current_timestamp(monkeypatch):
    monkeypatch.setenv("TZ", "Australia/Sydney")
    time.tzset()
    try:
        db_now = app_module.db.execute("SELECT CURRENT_TIMESTAMP AS now")[0]["now"]
        db_now = datetime.strptime(str(db_now), "%Y-%m-%d %H:%M:%S")
        assert abs((clock.utc_now() - db_now).total_seconds()) < 5
    finally:
        monkeypatch.undo()
        time.tzset()


def test_review_week_bounds_use_utc(logged_in_client, sydney_time):
    uid = _user_id()
    item_id = db_module.insert_task(uid, "Shipped on Sunday", "Technical")
    app_module.db.execute(
        "INSERT INTO events (user_id, item_id, event_type, created_at) VALUES (?, ?, ?, ?)",
        uid, item_id, "completed", "2025-06-01 19:00:00",
    )

    resp = logged_in_client.get("/review")
    assert b">1</span> Completed" in resp.data


def test_now_due_phrases_use_utc_date(logged_in_client, sydney_time):
    uid = _user_id()
    db_module.insert_task(uid, "Due Sunday", "Technical", due_date="2025-06-01")

    resp = logged_in_client.get("/now")
    assert b"due today" in resp.data
