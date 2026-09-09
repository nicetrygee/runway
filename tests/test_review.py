"""Route-level tests for /review (Slice D).

Forms (add/edit) don't expose stream/item_type/priority/effort_minutes/mode
yet (Slice A/B/C/D wire those in as they build their own surfaces — see
AGENTS.md), so tests seed those columns directly via raw SQL after an
ordinary insert_task, the same way tests/test_migration.py seeds rows
directly rather than going through a form.
"""
import json

import app as app_module
import db as db_module


def seed_item(user_id, title="Task", task_type="rfc", **overrides):
    item_id = db_module.insert_task(user_id, title, task_type, "", "", 2, None, "")
    if overrides:
        columns = ", ".join(f"{col} = ?" for col in overrides)
        app_module.db.execute(
            f"UPDATE tasks SET {columns} WHERE id = ?", *overrides.values(), item_id
        )
    return item_id


def mark_done(client, item_id):
    client.post(f"/status/{item_id}", json={"status": "done"})


def get_user_id(username):
    return db_module.get_user_by_username(username)[0]["id"]


def test_review_requires_login(client):
    resp = client.get("/review")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_review_empty_state(logged_in_client):
    resp = logged_in_client.get("/review")
    assert resp.status_code == 200
    assert b"Nothing completed this week yet" in resp.data
    assert b"clean slate" in resp.data


def test_review_shows_completed_count_and_time_breakdown(logged_in_client):
    uid = get_user_id("alice")
    item_id = seed_item(uid, "Ship the RFC", effort_minutes=60,
                         item_type="Delivery", mode="proactive")
    mark_done(logged_in_client, item_id)

    resp = logged_in_client.get("/review")
    assert resp.status_code == 200
    assert b">1</span> Completed" in resp.data
    assert b"Delivery" in resp.data
    assert b"1.0h" in resp.data
    assert b"100.0%" in resp.data  # all proactive


def test_review_capacity_read_reflects_open_items(logged_in_client):
    uid = get_user_id("alice")
    seed_item(uid, "Open task", effort_minutes=60, stream="task")

    resp = logged_in_client.get("/review")
    assert resp.status_code == 200
    assert b"1.0h committed" in resp.data


def test_review_capacity_read_ignores_delegated_and_waiting_streams(logged_in_client):
    uid = get_user_id("alice")
    seed_item(uid, "Delegated elsewhere", effort_minutes=600, stream="delegation")
    seed_item(uid, "Waiting on someone", effort_minutes=600, stream="waiting")

    resp = logged_in_client.get("/review")
    assert resp.status_code == 200
    assert b"0.0h committed" in resp.data
    assert b">1</span> Waiting On" in resp.data


def test_review_delegation_suggestions_render(logged_in_client):
    uid = get_user_id("alice")
    seed_item(uid, "Please delegate me", priority="Delegate", effort_minutes=30)

    resp = logged_in_client.get("/review")
    assert resp.status_code == 200
    assert b"Please delegate me" in resp.data
    assert b"Nothing stands out to delegate" not in resp.data


def test_carry_forward_action_writes_touched_event_with_payload(logged_in_client):
    uid = get_user_id("alice")
    item_id = seed_item(uid, "Keep going next week")

    resp = logged_in_client.post(
        "/review", data={"action": "carry_forward", "item_id": [str(item_id)]},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    events = app_module.db.execute(
        "SELECT * FROM events WHERE item_id = ? AND event_type = 'touched'", item_id
    )
    carry_events = [e for e in events if json.loads(e["payload"] or "{}").get("action") == "carry_forward"]
    assert len(carry_events) == 1


def test_carried_forward_item_drops_off_the_carry_forward_list(logged_in_client):
    uid = get_user_id("alice")
    item_id = seed_item(uid, "Keep going next week")

    logged_in_client.post(
        "/review", data={"action": "carry_forward", "item_id": [str(item_id)]}
    )
    resp = logged_in_client.get("/review")
    assert b"Keep going next week" not in resp.data
    assert b">1</span> Carried Forward" in resp.data


def test_settings_round_trip(logged_in_client):
    resp = logged_in_client.post(
        "/review",
        data={"action": "update_settings", "available_hours": "20", "meeting_hours_this_week": "5"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    resp = logged_in_client.get("/review")
    assert b'value="20.0"' in resp.data
    assert b"Meeting hours this week: 5.0h" in resp.data


def test_settings_update_rejects_non_numeric_input(logged_in_client):
    resp = logged_in_client.post(
        "/review",
        data={"action": "update_settings", "available_hours": "not-a-number", "meeting_hours_this_week": ""},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"must be numbers" in resp.data
