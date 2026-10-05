"""Retire the legacy `tasks.task_type` and `tasks.cognitive_load` columns.

`item_type` and `effort_minutes` replace them. Any row still missing a
new value is backfilled from the legacy one first (same mapping as
migrate_slice0.py), then both legacy columns are dropped. Run
migrate_slice0.py first on a pre-Slice-0 db. Safe to run repeatedly.

Back up runway.db before running: dropping a column can't be undone.

Usage:
    python migrate_retire_legacy_fields.py            # uses ./runway.db (or $DATABASE_URL)
    python migrate_retire_legacy_fields.py path/to/x.db
"""
import sqlite3
import sys

from migrate_slice0 import LOAD_TO_EFFORT, TYPE_MAP, db_path_from_env_or_arg, existing_columns

LEGACY_COLUMNS = ("task_type", "cognitive_load")


def retire_legacy_fields(con):
    """Backfill, then drop the legacy columns. Returns the columns dropped."""
    have = existing_columns(con.cursor(), "tasks")
    if "item_type" not in have:
        raise SystemExit("tasks has no item_type column: run migrate_slice0.py first.")

    if "task_type" in have:
        for legacy, new in TYPE_MAP.items():
            con.execute(
                "UPDATE tasks SET item_type = ? WHERE item_type IS NULL AND task_type = ?",
                (new, legacy),
            )
    if "cognitive_load" in have:
        for load, minutes in LOAD_TO_EFFORT.items():
            con.execute(
                "UPDATE tasks SET effort_minutes = ? "
                "WHERE effort_minutes IS NULL AND cognitive_load = ?",
                (minutes, load),
            )

    dropped = [col for col in LEGACY_COLUMNS if col in have]
    for col in dropped:
        con.execute(f"ALTER TABLE tasks DROP COLUMN {col}")
    return dropped


def main():
    path = db_path_from_env_or_arg()
    print(f"Migrating {path} ...")
    con = sqlite3.connect(path)
    with con:
        dropped = retire_legacy_fields(con)
    con.close()
    print(f"  columns dropped: {dropped or 'none (already retired)'}. Done.")


if __name__ == "__main__":
    sys.exit(main())
