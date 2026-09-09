"""Idempotent Slice D migration for an existing runway.db.

Additive only: adds the `settings` table (user_id, key, value) used for
per-user weekly-review preferences (available_hours, meeting_hours_this_week).
Does not touch `tasks`, `people`, or `events` — those are Slice 0's schema,
untouched here. Safe to run repeatedly.

Usage:
    python migrate_slice_d.py                 # uses ./runway.db (or $DATABASE_URL)
    python migrate_slice_d.py path/to/x.db
"""
import os
import sqlite3
import sys


def db_path_from_env_or_arg():
    if len(sys.argv) > 1:
        return sys.argv[1]
    url = os.environ.get("DATABASE_URL", "sqlite:///runway.db")
    return url.replace("sqlite:///", "", 1) if url.startswith("sqlite:///") else url


def ensure_settings_table(cur):
    """Create the settings table + index if missing. Shared by main() and
    tests/conftest.py so the DDL lives in exactly one place."""
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            UNIQUE(user_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_settings_user_id ON settings(user_id);
        """
    )


def main():
    path = db_path_from_env_or_arg()
    print(f"Migrating {path} ...")
    con = sqlite3.connect(path)
    cur = con.cursor()
    ensure_settings_table(cur)
    con.commit()
    con.close()
    print("  settings table ensured. Done.")


if __name__ == "__main__":
    main()
