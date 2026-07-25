from app import app as flask_app


def test_csrf_blocks_post_without_token(logged_in_client):
    # Other tests run with WTF_CSRF_ENABLED=False (see conftest.py) so they
    # can post form data without fetching a token first. This test flips
    # protection back on to confirm it actually rejects an unprotected POST.
    flask_app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = logged_in_client.post(
            "/add", data={"title": "X", "task_type": "rfc", "cognitive_load": "3"}
        )
        assert resp.status_code == 400
    finally:
        flask_app.config["WTF_CSRF_ENABLED"] = False
