"""Slice D migration: adds the additive `settings` table.

Mirrors tests/test_migration.py's pattern for migrate_slice0.py.
"""
import os
import sqlite3
import subprocess
import sys

MIGRATE_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "migrate_slice_d.py")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")


def run_migration(path):
    subprocess.run([sys.executable, MIGRATE_SCRIPT, path], check=True, capture_output=True)


def bootstrap_schema0_db(path):
    with open(SCHEMA_PATH) as f:
        schema_sql = f.read()
    con = sqlite3.connect(path)
    con.executescript(schema_sql)
    con.execute("INSERT INTO users (id, username, hash) VALUES (1, 'alice', 'x')")
    con.commit()
    con.close()


def test_migration_creates_settings_table(tmp_path):
    db_path = str(tmp_path / "runway.db")
    bootstrap_schema0_db(db_path)
    run_migration(db_path)

    con = sqlite3.connect(db_path)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "settings" in tables


def test_migration_does_not_touch_slice0_tables(tmp_path):
    db_path = str(tmp_path / "runway.db")
    bootstrap_schema0_db(db_path)

    con = sqlite3.connect(db_path)
    before = {row[1] for row in con.execute("PRAGMA table_info(tasks)")}
    con.close()

    run_migration(db_path)

    con = sqlite3.connect(db_path)
    after = {row[1] for row in con.execute("PRAGMA table_info(tasks)")}
    con.close()
    assert before == after


def test_settings_round_trip(tmp_path):
    db_path = str(tmp_path / "runway.db")
    bootstrap_schema0_db(db_path)
    run_migration(db_path)

    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO settings (user_id, key, value) VALUES (1, 'available_hours', '38')"
    )
    con.commit()
    row = con.execute(
        "SELECT value FROM settings WHERE user_id = 1 AND key = 'available_hours'"
    ).fetchone()
    con.close()
    assert row[0] == "38"


def test_migration_is_idempotent(tmp_path):
    db_path = str(tmp_path / "runway.db")
    bootstrap_schema0_db(db_path)

    run_migration(db_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO settings (user_id, key, value) VALUES (1, 'available_hours', '38')"
    )
    con.commit()
    con.close()

    run_migration(db_path)  # must not raise or wipe existing rows

    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT value FROM settings WHERE user_id = 1").fetchall()
    con.close()
    assert rows == [("38",)]


def test_settings_unique_constraint_per_user_and_key(tmp_path):
    db_path = str(tmp_path / "runway.db")
    bootstrap_schema0_db(db_path)
    run_migration(db_path)

    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO settings (user_id, key, value) VALUES (1, 'available_hours', '38')")
    con.commit()
    try:
        con.execute("INSERT INTO settings (user_id, key, value) VALUES (1, 'available_hours', '40')")
        con.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    finally:
        con.close()
    assert raised
