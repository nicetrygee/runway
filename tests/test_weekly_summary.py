import app as app_module


def add_done_task(client, title="Ship the RFC"):
    client.post(
        "/add",
        data={
            "title": title,
            "task_type": "rfc",
            "blast_radius": "",
            "sprint": "",
            "cognitive_load": "2",
            "due_date": "",
            "notes": "",
        },
    )
    task = app_module.db.execute(
        "SELECT id FROM tasks WHERE title = ? ORDER BY id DESC LIMIT 1", title
    )[0]
    client.post(f"/status/{task['id']}", json={"status": "done"})
    return task["id"]


def test_summary_requires_login(client):
    resp = client.get("/summary")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_summary_disabled_by_default_shows_plain_list(logged_in_client, monkeypatch):
    monkeypatch.setattr(app_module, "ai_client", object())
    add_done_task(logged_in_client)
    resp = logged_in_client.get("/summary")
    assert resp.status_code == 200
    assert b"Ship the RFC" in resp.data
    assert b"AI Recap" not in resp.data


def test_summary_enabled_but_not_configured(logged_in_client, monkeypatch):
    monkeypatch.setattr(app_module, "WEEKLY_SUMMARY_AI_ENABLED", True)
    monkeypatch.setattr(app_module, "ai_client", None)
    add_done_task(logged_in_client)
    resp = logged_in_client.get("/summary")
    assert resp.status_code == 200
    assert b"isn&#39;t configured" in resp.data or b"isn't configured" in resp.data
    assert b"Ship the RFC" in resp.data


def test_summary_enabled_success_shows_ai_recap(logged_in_client, monkeypatch):
    monkeypatch.setattr(app_module, "WEEKLY_SUMMARY_AI_ENABLED", True)
    monkeypatch.setattr(app_module, "ai_client", object())
    monkeypatch.setattr(
        app_module, "generate_weekly_summary", lambda tasks: "You shipped the RFC this week."
    )
    add_done_task(logged_in_client)
    resp = logged_in_client.get("/summary")
    assert resp.status_code == 200
    assert b"You shipped the RFC this week." in resp.data
    assert b"Ship the RFC" in resp.data


def test_summary_enabled_failure_falls_back_to_list(logged_in_client, monkeypatch):
    monkeypatch.setattr(app_module, "WEEKLY_SUMMARY_AI_ENABLED", True)
    monkeypatch.setattr(app_module, "ai_client", object())

    def fake_generate(tasks):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "generate_weekly_summary", fake_generate)
    add_done_task(logged_in_client)
    resp = logged_in_client.get("/summary")
    assert resp.status_code == 200
    assert b"Couldn&#39;t generate" in resp.data or b"Couldn't generate" in resp.data
    assert b"Ship the RFC" in resp.data


def test_summary_no_tasks_skips_ai_call_entirely(logged_in_client, monkeypatch):
    monkeypatch.setattr(app_module, "WEEKLY_SUMMARY_AI_ENABLED", True)
    monkeypatch.setattr(app_module, "ai_client", object())

    def fail_if_called(tasks):
        raise AssertionError("should not be called with no completed tasks")

    monkeypatch.setattr(app_module, "generate_weekly_summary", fail_if_called)
    resp = logged_in_client.get("/summary")
    assert resp.status_code == 200
    assert b"Nothing marked done" in resp.data
