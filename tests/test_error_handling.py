import app as app_module


def test_unhandled_exception_returns_generic_error_page(logged_in_client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(app_module.db, "execute", boom)

    resp = logged_in_client.get("/")

    assert resp.status_code == 500
    assert b"Something went wrong" in resp.data
    assert b"RuntimeError" not in resp.data
    assert b"Traceback" not in resp.data


def test_unhandled_exception_on_json_route_returns_json(logged_in_client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(app_module.db, "execute", boom)

    resp = logged_in_client.post(
        "/status/1",
        data='{"status": "done"}',
        content_type="application/json",
    )

    assert resp.status_code == 500
    assert resp.get_json() == {"error": "Something went wrong."}
