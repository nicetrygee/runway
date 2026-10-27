from types import SimpleNamespace

import app as app_module
import llm


def test_quick_add_requires_login(client):
    resp = client.post("/quick-add", data={"text": "follow up with Sarah"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_quick_add_empty_text(logged_in_client):
    resp = logged_in_client.post("/quick-add", data={"text": ""}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Type something" in resp.data


def test_quick_add_model_unreachable(logged_in_client, monkeypatch):
    def fake_extract(text):
        raise llm.LLMUnavailable("connection refused")

    monkeypatch.setattr(app_module, "extract_task_from_text", fake_extract)
    resp = logged_in_client.post("/quick-add", data={"text": "draft the zephyr roadmap"})
    assert resp.status_code == 200
    assert b"reach the local model" in resp.data
    # Blank form: nothing from the note is pre-filled.
    assert b"zephyr" not in resp.data


def test_quick_add_success_prefills_form(logged_in_client, monkeypatch):
    def fake_extract(text):
        return SimpleNamespace(
            title="Follow up with Sarah on the RFC",
            blast_radius="",
            sprint="",
            due_date="2026-08-01",
            notes="",
            item_type="Technical",
            priority="Important",
            effort_minutes=30,
            stream="task",
            mode="reactive",
            person="Sarah",
        )

    monkeypatch.setattr(app_module, "extract_task_from_text", fake_extract)
    resp = logged_in_client.post(
        "/quick-add", data={"text": "follow up with Sarah on the RFC by Aug 1"}
    )
    assert resp.status_code == 200
    assert b"Follow up with Sarah on the RFC" in resp.data
    assert b'<option value="Technical" selected>' in resp.data


def test_quick_add_extraction_failure(logged_in_client, monkeypatch):
    def fake_extract(text):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "extract_task_from_text", fake_extract)
    resp = logged_in_client.post(
        "/quick-add", data={"text": "follow up with Sarah on the RFC"}
    )
    assert resp.status_code == 200
    assert b"Couldn&#39;t parse" in resp.data or b"Couldn't parse" in resp.data


def test_quick_add_never_saves_a_task(logged_in_client, monkeypatch):
    # AGENTS.md AI rule 6: AI output is a proposal; nothing is written until
    # the user reviews the pre-filled form and saves it themselves.
    def fake_extract(text):
        return SimpleNamespace(
            title="Chase Juno for the Halcyon numbers", blast_radius="", sprint="",
            due_date="", notes="", item_type="Stakeholder", priority="Normal",
            effort_minutes=5, stream="waiting", mode="reactive", person="Juno",
        )

    monkeypatch.setattr(app_module, "extract_task_from_text", fake_extract)
    resp = logged_in_client.post("/quick-add", data={"text": "chase Juno for numbers"})

    assert resp.status_code == 200
    assert app_module.db.execute("SELECT id FROM tasks") == []
    assert app_module.db.execute("SELECT id FROM events") == []
    assert app_module.db.execute("SELECT id FROM people") == []
