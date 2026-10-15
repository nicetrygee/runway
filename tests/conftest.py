import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["SECRET_KEY"] = "test-secret-key"

TEST_DB_FD, TEST_DB_PATH = tempfile.mkstemp(suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

# Never write the real traces.db from tests; each test starts with no file.
TRACES_DIR = tempfile.mkdtemp()
TRACES_DB_PATH = os.path.join(TRACES_DIR, "traces.db")
os.environ["TRACES_DB_PATH"] = TRACES_DB_PATH

import app as app_module  # noqa: E402  (must import after env vars are set)
import llm  # noqa: E402

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
with open(SCHEMA_PATH) as f:
    SCHEMA_SQL = f.read()

app_module.app.config["TESTING"] = True
# Tests post form data directly without fetching a CSRF token first; disable
# CSRF checks here so tests don't need to thread one through.
# test_csrf.py re-enables it for a dedicated check that it's actually wired up.
app_module.app.config["WTF_CSRF_ENABLED"] = False


@pytest.fixture(autouse=True)
def reset_db():
    conn = sqlite3.connect(TEST_DB_PATH)
    conn.executescript(
        "DROP TABLE IF EXISTS settings; DROP TABLE IF EXISTS events; "
        "DROP TABLE IF EXISTS tasks; DROP TABLE IF EXISTS people; "
        "DROP TABLE IF EXISTS users;"
    )
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    # Limiter storage is a module-level singleton shared across the whole
    # test session — reset it so one test's requests don't count against
    # another's rate limit budget.
    app_module.limiter.reset()
    if os.path.exists(TRACES_DB_PATH):
        os.remove(TRACES_DB_PATH)
    yield


class FakeAdapter:
    """Stands in for the LM Studio adapter: returns canned text, records calls,
    or raises `error` if set. No network."""

    model = "fake-model"

    def __init__(self, text="", input_tokens=12, output_tokens=8, error=None):
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.error = error
        self.calls = []

    def send(self, messages, schema, settings):
        self.calls.append({"messages": messages, "schema": schema, "settings": settings})
        if self.error is not None:
            raise self.error
        return llm.AdapterResponse(
            raw_request={"model": self.model, "messages": messages},
            raw_response={"id": "fake-1", "content": self.text},
            text=self.text,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            model=self.model,
        )


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(llm, "adapter", fake)
    return fake


def read_traces():
    conn = sqlite3.connect(TRACES_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM traces ORDER BY id")]
    conn.close()
    return rows


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
