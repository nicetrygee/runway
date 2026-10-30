"""The synthetic world and its lab-DB seeder (slice E1b.2, D28)."""
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from evals.world import seed
from evals.world.__main__ import main

CASES_PATH = Path(__file__).resolve().parent.parent / "evals" / "quick_add" / "cases.jsonl"
TODAY = date(2026, 10, 15)


def _rows(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


@pytest.fixture
def world():
    return seed.load_world()


@pytest.fixture
def cast(world):
    return {person["name"] for person in world["people"]}


def test_seed_builds_a_fresh_db_matching_world_json(tmp_path, world):
    path = tmp_path / "lab.db"
    counts = seed.seed(f"sqlite:///{path}", TODAY)

    expected = seed.stream_counts(world)
    assert counts == expected
    assert sum(expected.values()) == len(world["items"])
    assert dict(_rows(path, "SELECT stream, COUNT(*) FROM tasks GROUP BY stream")) == expected
    assert _rows(path, "SELECT COUNT(*) FROM people") == [(len(world["people"]),)]
    assert _rows(path, "SELECT COUNT(*) FROM users") == [(1,)]
    created = _rows(path, "SELECT COUNT(DISTINCT item_id) FROM events WHERE event_type='created'")
    assert created == [(len(world["items"]),)]


def test_every_timestamp_is_the_anchor_plus_an_offset(tmp_path, world):
    path = tmp_path / "lab.db"
    seed.seed(f"sqlite:///{path}", TODAY)

    first = world["items"][0]
    row = _rows(path, "SELECT created_at, last_touched_at, due_date FROM tasks ORDER BY id")[0]
    assert row == (
        seed._timestamp(TODAY, first["created_offset_days"]),
        seed._timestamp(TODAY, first["last_touched_offset_days"]),
        seed._due_date(TODAY, first["due_offset_days"]),
    )


def test_same_anchor_builds_the_same_world(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    seed.seed(f"sqlite:///{a}", TODAY)
    seed.seed(f"sqlite:///{b}", TODAY)

    # The password hash is salted, so compare everything but users.hash.
    for sql in ("SELECT * FROM tasks ORDER BY id", "SELECT * FROM people ORDER BY id",
                "SELECT * FROM events ORDER BY id", "SELECT id, username FROM users"):
        assert _rows(a, sql) == _rows(b, sql)


@pytest.mark.parametrize("table_sql", [
    "INSERT INTO users (username, hash) VALUES ('someone', 'x')",
    "INSERT INTO tasks (user_id, title) VALUES (1, 'a real item')",
])
def test_refuses_a_db_that_already_has_rows(tmp_path, table_sql):
    path = tmp_path / "lab.db"
    conn = sqlite3.connect(path)
    conn.executescript(seed.SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute(table_sql)
    conn.commit()
    conn.close()
    before = _rows(path, "SELECT COUNT(*) FROM users UNION ALL SELECT COUNT(*) FROM tasks")

    with pytest.raises(seed.SeedRefused, match="already has rows"):
        seed.seed(f"sqlite:///{path}", TODAY)

    after = _rows(path, "SELECT COUNT(*) FROM users UNION ALL SELECT COUNT(*) FROM tasks")
    assert after == before
    assert _rows(path, "SELECT COUNT(*) FROM people") == [(0,)]


def test_refuses_to_seed_twice(tmp_path):
    path = tmp_path / "lab.db"
    seed.seed(f"sqlite:///{path}", TODAY)
    with pytest.raises(seed.SeedRefused):
        seed.seed(f"sqlite:///{path}", TODAY)


def test_refuses_runway_db_without_creating_it(tmp_path):
    path = tmp_path / "runway.db"
    with pytest.raises(seed.SeedRefused, match="real DB"):
        seed.seed(f"sqlite:///{path}", TODAY)
    assert not path.exists()


def test_cli_refusal_exits_1(tmp_path, capsys):
    assert main(["seed", "--db", f"sqlite:///{tmp_path / 'runway.db'}"]) == 1
    assert "real DB" in capsys.readouterr().err


def test_cli_seeds_with_an_explicit_anchor(tmp_path, capsys):
    path = tmp_path / "lab.db"
    assert main(["seed", "--db", f"sqlite:///{path}", "--today", "2026-10-15"]) == 0
    assert "anchored on 2026-10-15" in capsys.readouterr().out


def test_world_refers_only_to_its_own_cast_and_projects(world, cast):
    codenames = {project["codename"] for project in world["projects"]}
    assert len(cast) == len(world["people"])
    for item in world["items"]:
        assert item["person"] is None or item["person"] in cast, item["title"]
        assert item["project"] is None or item["project"] in codenames, item["title"]
    for project in world["projects"]:
        assert set(project["people"]) <= cast, project["codename"]
    for interaction in world["interactions"]:
        assert interaction["person"] in cast


def test_done_items_have_a_completed_event(world):
    for item in world["items"]:
        types = [event["type"] for event in item["events"]]
        assert (item["status"] == "done") == ("completed" in types), item["title"]


def test_every_person_in_the_quick_add_cases_is_in_the_cast(cast):
    lines = CASES_PATH.read_text(encoding="utf-8").splitlines()
    people = {json.loads(line)["expected"]["person"] for line in lines if line.strip()}
    # A team counterpart ("infra team") isn't a cast member (guide rule PE2).
    missing = {person for person in people if person and not person.endswith(" team")} - cast
    assert not missing, f"cases name people who aren't in world.json: {sorted(missing)}"
