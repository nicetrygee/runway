import json


def test_add_task_success(logged_in_client):
    resp = logged_in_client.post(
        "/add",
        data={"title": "Ship the RFC", "item_type": "Technical"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Ship the RFC" in resp.data


def test_add_task_missing_title(logged_in_client):
    resp = logged_in_client.post(
        "/add", data={"title": "", "item_type": "Technical"}
    )
    assert resp.status_code == 200
    assert b"Title is required" in resp.data


def test_add_task_missing_item_type(logged_in_client):
    resp = logged_in_client.post("/add", data={"title": "X"})
    assert resp.status_code == 200
    assert b"Invalid item type" in resp.data


def test_add_task_invalid_priority(logged_in_client):
    resp = logged_in_client.post(
        "/add", data={"title": "X", "item_type": "Technical", "priority": "Urgent!!"}
    )
    assert resp.status_code == 200
    assert b"Invalid priority" in resp.data


def test_add_task_non_numeric_effort(logged_in_client):
    resp = logged_in_client.post(
        "/add", data={"title": "X", "item_type": "Technical", "effort_minutes": "abc"}
    )
    assert resp.status_code == 200
    assert b"Invalid effort" in resp.data


def test_add_task_requires_login(client):
    resp = client.post(
        "/add", data={"title": "X", "item_type": "Technical"}
    )
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def _add_task(client, **overrides):
    data = {"title": "Task", "item_type": "Technical"}
    data.update(overrides)
    client.post("/add", data=data)
    from app import db

    return db.execute("SELECT id FROM tasks ORDER BY id DESC LIMIT 1")[0]["id"]


def test_edit_task_success(logged_in_client):
    task_id = _add_task(logged_in_client)
    resp = logged_in_client.post(
        f"/edit/{task_id}",
        data={
            "title": "Renamed",
            "item_type": "Operational",
            "status": "in_progress",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Renamed" in resp.data


def test_edit_task_changes_em_fields(logged_in_client):
    task_id = _add_task(logged_in_client, priority="Normal", effort_minutes="30", mode="reactive")
    logged_in_client.post(
        f"/edit/{task_id}",
        data={
            "title": "Task",
            "item_type": "Strategy",
            "status": "backlog",
            "priority": "Critical",
            "effort_minutes": "240",
            "mode": "proactive",
        },
    )

    from app import db

    row = db.execute(
        "SELECT item_type, priority, effort_minutes, mode FROM tasks WHERE id = ?", task_id
    )[0]
    assert row == {"item_type": "Strategy", "priority": "Critical",
                   "effort_minutes": 240, "mode": "proactive"}


def test_edit_task_can_clear_effort_estimate(logged_in_client):
    task_id = _add_task(logged_in_client, effort_minutes="30")
    logged_in_client.post(
        f"/edit/{task_id}",
        data={"title": "Task", "item_type": "Technical", "status": "backlog",
              "effort_minutes": ""},
    )

    from app import db

    assert db.execute("SELECT effort_minutes FROM tasks WHERE id = ?",
                      task_id)[0]["effort_minutes"] is None


def test_edit_form_shows_em_fields(logged_in_client):
    task_id = _add_task(logged_in_client, priority="Important", effort_minutes="120")
    resp = logged_in_client.get(f"/edit/{task_id}")
    assert b'name="item_type"' in resp.data
    assert b'<option value="Important" selected>' in resp.data
    assert b'<option value="120" selected>' in resp.data
    assert b"task_type" not in resp.data
    assert b"cognitive_load" not in resp.data


def test_edit_task_invalid_status_rejected(logged_in_client):
    task_id = _add_task(logged_in_client)
    resp = logged_in_client.post(
        f"/edit/{task_id}",
        data={
            "title": "Task",
            "item_type": "Technical",
            "status": "not_a_real_status",
        },
    )
    assert resp.status_code == 200
    assert b"Invalid status" in resp.data

    from app import db

    row = db.execute("SELECT status FROM tasks WHERE id = ?", task_id)[0]
    assert row["status"] == "backlog"


def test_edit_task_not_found(logged_in_client):
    resp = logged_in_client.get("/edit/999999", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Task not found" in resp.data


def test_cannot_edit_other_users_task(client):
    client.post("/register", data={"username": "alice", "password": "password123"})
    client.post("/login", data={"username": "alice", "password": "password123"})
    task_id = _add_task(client)
    client.get("/logout")

    client.post("/register", data={"username": "bob", "password": "password123"})
    client.post("/login", data={"username": "bob", "password": "password123"})
    resp = client.get(f"/edit/{task_id}", follow_redirects=True)
    assert b"Task not found" in resp.data


def test_delete_task(logged_in_client):
    task_id = _add_task(logged_in_client)
    resp = logged_in_client.post(f"/delete/{task_id}", follow_redirects=True)
    assert resp.status_code == 200

    from app import db

    assert db.execute("SELECT * FROM tasks WHERE id = ?", task_id) == []


def test_status_endpoint_valid(logged_in_client):
    task_id = _add_task(logged_in_client)
    resp = logged_in_client.post(
        f"/status/{task_id}",
        data=json.dumps({"status": "done"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


def test_status_endpoint_invalid(logged_in_client):
    task_id = _add_task(logged_in_client)
    resp = logged_in_client.post(
        f"/status/{task_id}",
        data=json.dumps({"status": "not_real"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid status"}


def test_status_endpoint_rejects_other_users_task(client):
    client.post("/register", data={"username": "alice", "password": "password123"})
    client.post("/login", data={"username": "alice", "password": "password123"})
    task_id = _add_task(client)
    client.get("/logout")

    client.post("/register", data={"username": "bob", "password": "password123"})
    client.post("/login", data={"username": "bob", "password": "password123"})
    resp = client.post(
        f"/status/{task_id}",
        data=json.dumps({"status": "done"}),
        content_type="application/json",
    )
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Task not found"}

    from app import db

    assert db.execute("SELECT status FROM tasks WHERE id = ?", task_id)[0]["status"] == "backlog"
    events = db.execute(
        "SELECT * FROM events WHERE item_id = ? AND event_type IN ('status_changed', 'completed')",
        task_id,
    )
    assert events == []
