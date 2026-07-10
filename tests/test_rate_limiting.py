def test_login_rate_limited_after_ten_attempts(client):
    for _ in range(10):
        resp = client.post("/login", data={"username": "nobody", "password": "wrong"})
        assert resp.status_code == 200
    resp = client.post("/login", data={"username": "nobody", "password": "wrong"})
    assert resp.status_code == 429


def test_register_rate_limited_after_five_attempts(client):
    for i in range(5):
        resp = client.post(
            "/register", data={"username": f"user{i}", "password": "password123"}
        )
        assert resp.status_code in (200, 302)
    resp = client.post(
        "/register", data={"username": "user5", "password": "password123"}
    )
    assert resp.status_code == 429
