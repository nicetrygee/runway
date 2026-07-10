import json


def test_add_task_success(logged_in_client):
    resp = logged_in_client.post(
        "/add",
        data={"title": "Ship the RFC", "task_type": "rfc", "cognitive_load": "3"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Ship the RFC" in resp.data


def test_add_task_missing_title(logged_in_client):
    resp = logged_in_client.post(
        "/add", data={"title": "", "task_type": "rfc", "cognitive_load": "3"}
    )
    assert resp.status_code == 200
    assert b"Title is required" in resp.data


def test_add_task_invalid_task_type(logged_in_client):
    resp = logged_in_client.post(
        "/add", data={"title": "X", "task_type": "bogus", "cognitive_load": "3"}
    )
    assert resp.status_code == 200
    assert b"Invalid task type" in resp.data


def test_add_task_cognitive_load_out_of_range(logged_in_client):
    resp = logged_in_client.post(
        "/add", data={"title": "X", "task_type": "rfc", "cognitive_load": "99"}
    )
    assert resp.status_code == 200
    assert b"between 1 and 5" in resp.data


def test_add_task_cognitive_load_non_numeric(logged_in_client):
    resp = logged_in_client.post(
        "/add", data={"title": "X", "task_type": "rfc", "cognitive_load": "abc"}
    )
    assert resp.status_code == 200
    assert b"must be a number" in resp.data


def test_add_task_requires_login(client):
    resp = client.post(
        "/add", data={"title": "X", "task_type": "rfc", "cognitive_load": "3"}
    )
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def _add_task(client, **overrides):
    data = {"title": "Task", "task_type": "rfc", "cognitive_load": "3"}
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
            "task_type": "incident",
            "status": "in_progress",
            "cognitive_load": "5",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Renamed" in resp.data


def test_edit_task_invalid_status_rejected(logged_in_client):
    task_id = _add_task(logged_in_client)
    resp = logged_in_client.post(
        f"/edit/{task_id}",
        data={
            "title": "Task",
            "task_type": "rfc",
            "status": "not_a_real_status",
            "cognitive_load": "3",
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
