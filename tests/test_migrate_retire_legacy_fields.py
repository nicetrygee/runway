"""Retiring task_type / cognitive_load, and the full upgrade chain.

An old db run through every migrate_*.py script must end up with the same
tables and columns as a fresh schema.sql install.
"""
import os
import sqlite3
import subprocess
import sys

import pytest
from test_migration import make_legacy_db

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCHEMA_PATH = os.path.join(ROOT, "schema.sql")
UPGRADE_CHAIN = ["migrate_slice0.py", "migrate_slice_d.py", "migrate_retire_legacy_fields.py"]


def run(script, path):
    return subprocess.run([sys.executable, os.path.join(ROOT, script), path],
                          capture_output=True, text=True)


def run_chain(path):
    for script in UPGRADE_CHAIN:
        result = run(script, path)
        assert result.returncode == 0, result.stderr


def columns(path, table):
    con = sqlite3.connect(path)
    cols = {row[1]: (row[2], row[3]) for row in con.execute(f"PRAGMA table_info({table})")}
    con.close()
    return cols  # name -> (type, notnull)


def tables(path):
    con = sqlite3.connect(path)
    names = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    con.close()
    return names


def test_upgraded_db_matches_fresh_schema(tmp_path):
    old, fresh = str(tmp_path / "old.db"), str(tmp_path / "fresh.db")
    make_legacy_db(old)
    run_chain(old)
    with open(SCHEMA_PATH) as f:
        sqlite3.connect(fresh).executescript(f.read())

    assert tables(old) == tables(fresh)
    for table in tables(fresh):
        assert columns(old, table) == columns(fresh, table), table


def test_backfills_then_drops_legacy_columns(tmp_path):
    db_path = str(tmp_path / "old.db")
    make_legacy_db(db_path)
    assert run("migrate_slice0.py", db_path).returncode == 0

    # Rows created between Slice 0 and now could lack the new values.
    con = sqlite3.connect(db_path)
    con.execute("UPDATE tasks SET item_type = NULL, effort_minutes = NULL WHERE id = 1")
    con.execute("UPDATE tasks SET item_type = 'Strategy', effort_minutes = 5 WHERE id = 2")
    con.commit()
    con.close()

    assert run("migrate_retire_legacy_fields.py", db_path).returncode == 0

    cols = columns(db_path, "tasks")
    assert "task_type" not in cols and "cognitive_load" not in cols
    con = sqlite3.connect(db_path)
    rows = dict((r[0], r[1:]) for r in con.execute(
        "SELECT id, item_type, effort_minutes FROM tasks WHERE id IN (1, 2)"))
    con.close()
    assert rows[1] == ("Operational", 30)  # incident / load 1, backfilled
    assert rows[2] == ("Strategy", 5)      # already set, left alone


def test_keeps_rows_and_events(tmp_path):
    db_path = str(tmp_path / "old.db")
    make_legacy_db(db_path)
    assert run("migrate_slice0.py", db_path).returncode == 0
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO events (user_id, item_id, event_type) VALUES (1, 1, 'created')")
    con.commit()
    con.close()

    assert run("migrate_retire_legacy_fields.py", db_path).returncode == 0

    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 6
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    con.close()


def test_whole_chain_is_safe_to_rerun(tmp_path):
    db_path = str(tmp_path / "old.db")
    make_legacy_db(db_path)
    run_chain(db_path)
    before = columns(db_path, "tasks")
    run_chain(db_path)
    assert columns(db_path, "tasks") == before


def test_refuses_a_pre_slice0_db(tmp_path):
    db_path = str(tmp_path / "old.db")
    make_legacy_db(db_path)

    result = run("migrate_retire_legacy_fields.py", db_path)

    assert result.returncode != 0
    assert "migrate_slice0.py first" in result.stderr
    assert "task_type" in columns(db_path, "tasks")


@pytest.mark.parametrize("script", UPGRADE_CHAIN)
def test_each_script_is_a_noop_on_a_fresh_install(tmp_path, script):
    db_path = str(tmp_path / "fresh.db")
    with open(SCHEMA_PATH) as f:
        sqlite3.connect(db_path).executescript(f.read())
    before = {t: columns(db_path, t) for t in tables(db_path)}

    assert run(script, db_path).returncode == 0
    assert {t: columns(db_path, t) for t in tables(db_path)} == before
