"""Slice C: commitments / delegated / waiting surfaces.

Covers stream-filtered queries in db.py, the pure aging/escalation helpers
in app.py (tested directly with an explicit `now`, same determinism trick
as test_recommend.py's fixed NOW), the follow-up action's event/timestamp
side effects, and person grouping.
"""
from datetime import datetime

import pytest

import db as db_module
from app import escalation_for_item, item_age_days


def _add_item(client, **overrides):
    data = {"title": "Item", "task_type": "rfc", "cognitive_load": "3"}
    data.update(overrides)
    client.post("/add", data=data)
    return db_module.db.execute("SELECT id FROM tasks ORDER BY id DESC LIMIT 1")[0]["id"]


def _user_id(username="alice"):
    return db_module.get_user_by_username(username)[0]["id"]


def _touch_at(item_id, timestamp):
    db_module.db.execute("UPDATE tasks SET last_touched_at = ? WHERE id = ?", timestamp, item_id)


# --- db.py: stream-filtered queries ----------------------------------------

def test_items_by_stream_filters_by_stream_and_excludes_others():
    uid = db_module.create_user("carol", "hash")
    task_item = db_module.insert_task(uid, "Plain task", "rfc", "", "", 2, None, "")
    waiting_item = db_module.insert_task(uid, "Waiting item", "rfc", "", "", 2, None, "")
    db_module.set_relationship(waiting_item, uid, "waiting", None)

    waiting = db_module.items_by_stream(uid, "waiting")
    assert [i["id"] for i in waiting] == [waiting_item]

    tasks = db_module.items_by_stream(uid, "task")
    assert [i["id"] for i in tasks] == [task_item]


def test_items_by_stream_excludes_other_users():
    alice_id = db_module.create_user("alice_iso", "hash")
    bob_id = db_module.create_user("bob_iso", "hash")
    alice_item = db_module.insert_task(alice_id, "Alice item", "rfc", "", "", 2, None, "")
    bob_item = db_module.insert_task(bob_id, "Bob item", "rfc", "", "", 2, None, "")
    db_module.set_relationship(alice_item, alice_id, "waiting", None)
    db_module.set_relationship(bob_item, bob_id, "waiting", None)

    alice_waiting = db_module.items_by_stream(alice_id, "waiting")
    assert [i["id"] for i in alice_waiting] == [alice_item]


def test_set_relationship_updates_stream_and_person_and_logs_delegated_event():
    uid = db_module.create_user("dave", "hash")
    person_id = db_module.create_person(uid, "Sarah", "PM", "")
    item = db_module.insert_task(uid, "Hiring analysis", "rfc", "", "", 2, None, "")

    db_module.set_relationship(item, uid, "delegation", person_id)

    row = db_module.get_task(item, uid)[0]
    assert row["stream"] == "delegation"
    assert row["person_id"] == person_id

    events = db_module.db.execute(
        "SELECT * FROM events WHERE item_id = ? AND event_type = 'delegated'", item
    )
    assert len(events) == 1


def test_open_items_for_person_excludes_done_items():
    uid = db_module.create_user("erin2", "hash")
    person_id = db_module.create_person(uid, "James", "Eng", "")
    open_item = db_module.insert_task(uid, "Open item", "rfc", "", "", 2, None, "")
    done_item = db_module.insert_task(uid, "Done item", "rfc", "", "", 2, None, "")
    db_module.set_relationship(open_item, uid, "delegation", person_id)
    db_module.set_relationship(done_item, uid, "delegation", person_id)
    db_module.set_status(done_item, uid, "done")

    open_items = db_module.open_items_for_person(person_id, uid)
    assert [i["id"] for i in open_items] == [open_item]


def test_follow_up_logs_event_and_bumps_last_touched_at():
    uid = db_module.create_user("frank2", "hash")
    item = db_module.insert_task(uid, "Chase Product", "rfc", "", "", 2, None, "")
    db_module.set_relationship(item, uid, "waiting", None)
    _touch_at(item, "2020-01-01 00:00:00")

    db_module.follow_up(item, uid)

    events = db_module.db.execute(
        "SELECT * FROM events WHERE item_id = ? AND event_type = 'followed_up'", item
    )
    assert len(events) == 1
    row = db_module.get_task(item, uid)[0]
    assert row["last_touched_at"] != "2020-01-01 00:00:00"


# --- app.py: pure aging/escalation helpers ----------------------------------

def test_item_age_days_computed_from_last_touched_at():
    now = datetime(2026, 9, 9, 12, 0, 0)
    item = {"last_touched_at": "2026-09-05 12:00:00"}
    assert item_age_days(item, now) == 4


def test_escalation_none_for_fresh_waiting_item():
    now = datetime(2026, 9, 9, 12, 0, 0)
    item = {"stream": "waiting", "due_date": None, "last_touched_at": "2026-09-08 12:00:00"}
    assert escalation_for_item(item, now) is None


def test_escalation_for_stale_untouched_waiting_item():
    now = datetime(2026, 9, 9, 12, 0, 0)
    item = {"stream": "waiting", "due_date": None, "last_touched_at": "2026-09-05 12:00:00"}
    msg = escalation_for_item(item, now)
    assert msg is not None
    assert "4 days" in msg


def test_escalation_for_overdue_delegation_item_takes_precedence_over_staleness():
    now = datetime(2026, 9, 9, 12, 0, 0)
    item = {
        "stream": "delegation", "due_date": "2026-09-07",
        "last_touched_at": "2026-09-08 12:00:00",
    }
    msg = escalation_for_item(item, now)
    assert msg is not None
    assert "was due 2026-09-07" in msg


def test_escalation_none_for_commitment_even_if_overdue():
    now = datetime(2026, 9, 9, 12, 0, 0)
    item = {
        "stream": "commitment", "due_date": "2026-09-01",
        "last_touched_at": "2026-09-01 12:00:00",
    }
    assert escalation_for_item(item, now) is None


def test_escalation_none_for_task_stream():
    now = datetime(2026, 9, 9, 12, 0, 0)
    item = {"stream": "task", "due_date": "2026-09-01", "last_touched_at": "2026-09-01 12:00:00"}
    assert escalation_for_item(item, now) is None


# --- routes ------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/commitments", "/waiting", "/delegated", "/people"])
def test_relationship_views_require_login(client, path):
    resp = client.get(path)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_commitments_view_shows_only_commitment_stream_grouped_by_person(logged_in_client):
    uid = _user_id()
    james_id = db_module.create_person(uid, "James", "Eng", "")
    _add_item(logged_in_client, title="Plain Task")
    commitment = _add_item(logged_in_client, title="Send proposal")
    db_module.set_relationship(commitment, uid, "commitment", james_id)

    resp = logged_in_client.get("/commitments")
    assert resp.status_code == 200
    assert b"James" in resp.data
    assert b"Send proposal" in resp.data
    assert b"Plain Task" not in resp.data


def test_waiting_view_shows_escalation_for_overdue_item(logged_in_client):
    uid = _user_id()
    item = _add_item(logged_in_client, title="Confirm scope", due_date="2020-01-01")
    db_module.set_relationship(item, uid, "waiting", None)

    resp = logged_in_client.get("/waiting")
    assert resp.status_code == 200
    assert b"Confirm scope" in resp.data
    assert b"follow up?" in resp.data


def test_delegated_view_only_shows_delegation_stream(logged_in_client):
    uid = _user_id()
    _add_item(logged_in_client, title="Not delegated")
    item = _add_item(logged_in_client, title="Hiring analysis")
    db_module.set_relationship(item, uid, "delegation", None)

    resp = logged_in_client.get("/delegated")
    assert b"Hiring analysis" in resp.data
    assert b"Not delegated" not in resp.data


def test_follow_up_route_logs_event_and_redirects(logged_in_client):
    uid = _user_id()
    item = _add_item(logged_in_client, title="Chase Product")
    db_module.set_relationship(item, uid, "waiting", None)
    _touch_at(item, "2020-01-01 00:00:00")

    resp = logged_in_client.post(f"/follow-up/{item}", follow_redirects=True)
    assert resp.status_code == 200

    events = db_module.db.execute(
        "SELECT * FROM events WHERE item_id = ? AND event_type = 'followed_up'", item
    )
    assert len(events) == 1
    row = db_module.get_task(item, uid)[0]
    assert row["last_touched_at"] != "2020-01-01 00:00:00"


def test_follow_up_rejects_other_users_item(client):
    client.post("/register", data={"username": "alice", "password": "password123"})
    client.post("/login", data={"username": "alice", "password": "password123"})
    item = _add_item(client, title="Alice item")
    client.get("/logout")

    client.post("/register", data={"username": "bob", "password": "password123"})
    client.post("/login", data={"username": "bob", "password": "password123"})
    resp = client.post(f"/follow-up/{item}", follow_redirects=True)
    assert b"Item not found" in resp.data

    events = db_module.db.execute(
        "SELECT * FROM events WHERE item_id = ? AND event_type = 'followed_up'", item
    )
    assert len(events) == 0


def test_set_item_relationship_route_updates_stream_and_person(logged_in_client):
    uid = _user_id()
    person_id = db_module.create_person(uid, "Sarah", "PM", "")
    item = _add_item(logged_in_client, title="Hiring analysis")

    resp = logged_in_client.post(
        f"/items/{item}/relationship",
        data={"stream": "delegation", "person_id": str(person_id)},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    row = db_module.get_task(item, uid)[0]
    assert row["stream"] == "delegation"
    assert row["person_id"] == person_id


def test_set_item_relationship_rejects_invalid_stream(logged_in_client):
    item = _add_item(logged_in_client, title="X")
    resp = logged_in_client.post(
        f"/items/{item}/relationship",
        data={"stream": "bogus", "person_id": ""},
        follow_redirects=True,
    )
    assert b"Invalid stream" in resp.data


def test_add_person(logged_in_client):
    resp = logged_in_client.post(
        "/people/add", data={"name": "James", "role": "Eng", "notes": ""}, follow_redirects=True
    )
    assert resp.status_code == 200
    assert b"James" in resp.data


def test_add_person_requires_name(logged_in_client):
    resp = logged_in_client.post(
        "/people/add", data={"name": "", "role": "", "notes": ""}, follow_redirects=True
    )
    assert b"Name is required" in resp.data


def test_person_detail_shows_open_items_and_count(logged_in_client):
    uid = _user_id()
    person_id = db_module.create_person(uid, "James", "Eng", "")
    item = _add_item(logged_in_client, title="Investigate outage")
    db_module.set_relationship(item, uid, "delegation", person_id)

    resp = logged_in_client.get(f"/people/{person_id}")
    assert resp.status_code == 200
    assert b"Investigate outage" in resp.data
    assert b"1 open item" in resp.data


def test_person_detail_not_found(logged_in_client):
    resp = logged_in_client.get("/people/999999", follow_redirects=True)
    assert b"Person not found" in resp.data
