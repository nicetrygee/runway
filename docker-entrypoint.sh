#!/bin/sh
set -e

# Bootstrap a fresh runway.db on first run, mirroring the README's quickstart.
if [ ! -f runway.db ]; then
    python3 -c "import sqlite3; sqlite3.connect('runway.db').executescript(open('schema.sql').read())"
fi

exec gunicorn app:app --bind 0.0.0.0:8000
