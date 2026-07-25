from types import SimpleNamespace

import app as app_module


def test_quick_add_requires_login(client):
    resp = client.post("/quick-add", data={"text": "follow up with Sarah"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_quick_add_empty_text(logged_in_client):
    resp = logged_in_client.post("/quick-add", data={"text": ""}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Type something" in resp.data


def test_quick_add_not_configured(logged_in_client, monkeypatch):
    monkeypatch.setattr(app_module, "ai_client", None)
    resp = logged_in_client.post(
        "/quick-add", data={"text": "follow up with Sarah on the RFC"}
    )
    assert resp.status_code == 200
    assert b"isn&#39;t configured" in resp.data or b"isn't configured" in resp.data


def test_quick_add_success_prefills_form(logged_in_client, monkeypatch):
    monkeypatch.setattr(app_module, "ai_client", object())  # just needs to be non-None

    def fake_extract(text):
        return SimpleNamespace(
            title="Follow up with Sarah on the RFC",
            task_type="rfc",
            blast_radius="",
            sprint="",
            cognitive_load=9,  # deliberately out of range, to check clamping
            due_date="2026-08-01",
            notes="",
        )

    monkeypatch.setattr(app_module, "extract_task_from_text", fake_extract)
    resp = logged_in_client.post(
        "/quick-add", data={"text": "follow up with Sarah on the RFC by Aug 1"}
    )
    assert resp.status_code == 200
    assert b"Follow up with Sarah on the RFC" in resp.data
    assert b'value="5"' in resp.data  # clamped from 9 to 5


def test_quick_add_extraction_failure(logged_in_client, monkeypatch):
    monkeypatch.setattr(app_module, "ai_client", object())

    def fake_extract(text):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "extract_task_from_text", fake_extract)
    resp = logged_in_client.post(
        "/quick-add", data={"text": "follow up with Sarah on the RFC"}
    )
    assert resp.status_code == 200
    assert b"Couldn&#39;t parse" in resp.data or b"Couldn't parse" in resp.data
