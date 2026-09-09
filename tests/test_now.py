def _add_task(client, **overrides):
    data = {"title": "Task", "task_type": "rfc", "cognitive_load": "3"}
    data.update(overrides)
    client.post("/add", data=data)
    from app import db

    return db.execute("SELECT id FROM tasks ORDER BY id DESC LIMIT 1")[0]["id"]


def test_now_requires_login(client):
    resp = client.get("/now")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_now_renders_ranked_list_with_reason(logged_in_client):
    _add_task(logged_in_client, title="Follow up with Product")
    resp = logged_in_client.get("/now")
    assert resp.status_code == 200
    assert b"Follow up with Product" in resp.data
    assert b"unestimated" in resp.data  # effort_minutes defaults to NULL, unset by /add


def test_now_time_fit_drops_large_item_and_shows_fallback_note(logged_in_client):
    task_id = _add_task(logged_in_client, title="Two-hour strategy doc")
    from app import db

    db.execute("UPDATE tasks SET effort_minutes = 120 WHERE id = ?", task_id)

    resp = logged_in_client.get("/now?minutes=15")
    assert resp.status_code == 200
    assert b"Two-hour strategy doc" in resp.data
    assert b"doesn&#39;t fit your window" in resp.data or b"doesn't fit your window" in resp.data
