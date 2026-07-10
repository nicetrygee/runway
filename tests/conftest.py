import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["SECRET_KEY"] = "test-secret-key"

TEST_DB_FD, TEST_DB_PATH = tempfile.mkstemp(suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

import app as app_module  # noqa: E402  (must import after env vars are set)

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
with open(SCHEMA_PATH) as f:
    SCHEMA_SQL = f.read()

app_module.app.config["TESTING"] = True


@pytest.fixture(autouse=True)
def reset_db():
    conn = sqlite3.connect(TEST_DB_PATH)
    conn.executescript("DROP TABLE IF EXISTS tasks; DROP TABLE IF EXISTS users;")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    # Limiter storage is a module-level singleton shared across the whole
    # test session — reset it so one test's requests don't count against
    # another's rate limit budget.
    app_module.limiter.reset()
    yield


@pytest.fixture
def client():
    with app_module.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def logged_in_client(client):
    client.post("/register", data={"username": "alice", "password": "password123"})
    client.post("/login", data={"username": "alice", "password": "password123"})
    return client


def pytest_sessionfinish(session, exitstatus):
    os.close(TEST_DB_FD)
    os.remove(TEST_DB_PATH)
