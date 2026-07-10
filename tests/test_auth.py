def test_register_success(client):
    resp = client.post(
        "/register",
        data={"username": "alice", "password": "password123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Account created" in resp.data


def test_register_duplicate_username(client):
    client.post("/register", data={"username": "alice", "password": "password123"})
    resp = client.post(
        "/register",
        data={"username": "alice", "password": "password123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"already taken" in resp.data


def test_register_missing_fields(client):
    resp = client.post("/register", data={"username": "", "password": ""})
    assert resp.status_code == 200
    assert b"required" in resp.data


def test_login_success(client):
    client.post("/register", data={"username": "alice", "password": "password123"})
    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invalid credentials" not in resp.data


def test_login_wrong_password(client):
    client.post("/register", data={"username": "alice", "password": "password123"})
    resp = client.post(
        "/login",
        data={"username": "alice", "password": "wrongpassword"},
    )
    assert resp.status_code == 200
    assert b"Invalid credentials" in resp.data


def test_login_nonexistent_user(client):
    resp = client.post("/login", data={"username": "nobody", "password": "whatever"})
    assert resp.status_code == 200
    assert b"Invalid credentials" in resp.data


def test_logout_clears_session(logged_in_client):
    resp = logged_in_client.get("/logout", follow_redirects=True)
    assert resp.status_code == 200
    # after logout, protected routes should redirect to login
    resp = logged_in_client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_index_requires_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
