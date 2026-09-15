"""Tests for the EM dashboard assembly (composition of Slices A-D onto `/`).

Unit tests for the two new pure capacity.py functions, then route-level
tests seeding real rows via raw SQL after an ordinary insert_task — same
pattern tests/test_review.py uses, since forms don't expose
stream/priority/is_blocking yet.
"""
import capacity
import db as db_module

# --- capacity.py: neglected_items / bottleneck_items -----------------------

def test_neglected_items_filters_to_high_stakes_priority():
    items = [
        {"priority": "Normal", "last_touched_at": "2020-01-01 00:00:00"},
        {"priority": "Critical", "last_touched_at": "2020-01-01 00:00:00"},
        {"priority": "Important", "last_touched_at": "2020-01-01 00:00:00"},
    ]
    result = capacity.neglected_items(items)
    assert len(result) == 2
    assert all(i["priority"] in ("Critical", "Important") for i in result)


def test_neglected_items_sorts_most_stale_first():
    items = [
        {"priority": "Critical", "last_touched_at": "2026-09-01 00:00:00", "title": "newer"},
        {"priority": "Critical", "last_touched_at": "2020-01-01 00:00:00", "title": "oldest"},
        {"priority": "Critical", "last_touched_at": "2025-01-01 00:00:00", "title": "middle"},
    ]
    result = capacity.neglected_items(items)
    assert [i["title"] for i in result] == ["oldest", "middle", "newer"]


def test_neglected_items_respects_limit():
    items = [{"priority": "Critical", "last_touched_at": "2020-01-01 00:00:00"} for _ in range(10)]
    assert len(capacity.neglected_items(items, limit=3)) == 3


def test_bottleneck_items_filters_to_positive_is_blocking():
    items = [
        {"is_blocking": 0, "title": "not blocking"},
        {"is_blocking": 2, "title": "blocking"},
    ]
    result = capacity.bottleneck_items(items)
    assert [i["title"] for i in result] == ["blocking"]


def test_bottleneck_items_sorts_most_blocking_first():
    items = [
        {"is_blocking": 1, "title": "one"},
        {"is_blocking": 5, "title": "five"},
        {"is_blocking": 3, "title": "three"},
    ]
    result = capacity.bottleneck_items(items)
    assert [i["title"] for i in result] == ["five", "three", "one"]


# --- route-level: `/` dashboard widgets -------------------------------------

def seed_item(user_id, title="Task", task_type="rfc", **overrides):
    item_id = db_module.insert_task(user_id, title, task_type, "", "", 2, None, "")
    if overrides:
        columns = ", ".join(f"{col} = ?" for col in overrides)
        db_module.db.execute(
            f"UPDATE tasks SET {columns} WHERE id = ?", *overrides.values(), item_id
        )
    return item_id


def get_user_id(username):
    return db_module.get_user_by_username(username)[0]["id"]


def test_dashboard_empty_state_for_all_widgets(logged_in_client):
    resp = logged_in_client.get("/")
    assert resp.status_code == 200
    assert b"Nothing in your court right now." in resp.data
    assert b"Not waiting on anyone right now." in resp.data
    assert b"Nothing high-stakes has gone quiet." in resp.data
    assert (
        b"Nothing&#39;s waiting on you right now." in resp.data
        or b"Nothing's waiting on you right now." in resp.data
    )


def test_dashboard_now_widget_shows_task_stream_item(logged_in_client):
    seed_item(get_user_id("alice"), "Ship the RFC", stream="task", effort_minutes=30)
    resp = logged_in_client.get("/")
    assert b"Ship the RFC" in resp.data


def test_dashboard_waiting_widget_shows_escalation(logged_in_client):
    uid = get_user_id("alice")
    seed_item(uid, "Confirm scope", stream="waiting", due_date="2020-01-01")
    resp = logged_in_client.get("/")
    assert b"Confirm scope" in resp.data
    assert b"follow up?" in resp.data


def test_dashboard_neglected_widget_shows_stale_high_priority_item(logged_in_client):
    uid = get_user_id("alice")
    seed_item(uid, "Stale critical item", priority="Critical",
              last_touched_at="2020-01-01 00:00:00")
    resp = logged_in_client.get("/")
    assert b"Stale critical item" in resp.data


def test_dashboard_neglected_widget_excludes_normal_priority(logged_in_client):
    # Normal priority still shows up elsewhere on the page (kanban list,
    # possibly the Now widget) — this only checks the neglect filter itself
    # via the empty-state message; the precise filtering is unit-tested
    # above (test_neglected_items_filters_to_high_stakes_priority).
    uid = get_user_id("alice")
    seed_item(uid, "Normal old item", priority="Normal",
              last_touched_at="2020-01-01 00:00:00")
    resp = logged_in_client.get("/")
    assert b"Nothing high-stakes has gone quiet." in resp.data


def test_dashboard_bottleneck_widget_shows_blocking_item(logged_in_client):
    uid = get_user_id("alice")
    seed_item(uid, "Blocking three people", is_blocking=3)
    resp = logged_in_client.get("/")
    assert b"Blocking three people" in resp.data
    assert b"Blocking 3 people" in resp.data


def test_dashboard_capacity_snapshot_renders(logged_in_client):
    uid = get_user_id("alice")
    seed_item(uid, "Open work", stream="task", effort_minutes=60)
    resp = logged_in_client.get("/")
    assert b"committed" in resp.data


def test_dashboard_requires_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_dashboard_kanban_still_renders_below_new_sections(logged_in_client):
    seed_item(get_user_id("alice"), "Legacy kanban task")
    resp = logged_in_client.get("/")
    assert b"Legacy kanban task" in resp.data
    assert b"Backlog" in resp.data
