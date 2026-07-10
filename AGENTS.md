# AGENTS.md

## Setup & run

```bash
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
```

```bash
flask --app app:app run --debug
```

Set a `SECRET_KEY` via env or `.env` (gitignored) — the app fails fast at startup with a `RuntimeError` if it's missing.

`--debug` is for local dev only — it enables the Werkzeug debugger, which allows remote code execution if the server is ever reachable from an untrusted network. Never run with `--debug` (or `debug=True`) outside local dev.

## DB

- SQLite via `cs50.SQL`. The db file is `runway.db` (gitignored).
- On first query, `cs50.SQL` auto-creates the file if it doesn't exist, but tables are only created if `schema.sql` has been executed. Run `schema.sql` against the db to bootstrap tables:
  ```bash
  sqlite3 runway.db < schema.sql
  ```
- Query placeholder style is `?` (not `%s` or `:named`). This is the cs50 library convention.

## Architecture

- **Single-file Flask app**: `app.py` (~157 lines) contains all routes, auth decorator, and app config.
- **Auth**: `login_required` decorator guards routes. Sessions use filesystem backend (`flask_session/`, gitignored). Passwords hashed with Werkzeug.
- **Templates**: Jinja2, extending `layout.html`. Dark theme, Space Mono + Syne fonts.
- **Frontend JS**: `static/app.js` — a single AJAX status-update via `fetch()` to `/status/<id>`. No framework.
- **Task status values**: `backlog`, `in_progress`, `blocked`, `done` (enforced by CHECK constraint in SQLite and validated server-side).
- **Task type values**: `incident`, `rfc`, `1on1`, `hiring`, `delivery`, `other`.

## Conventions

- No test suite exists.
- No linter, formatter, or typechecker config. The project has no build pipeline or CI.
- Flash messages use categories `"success"` and `"error"`.
- `VALID_TASK_TYPES` and `VALID_STATUSES` in `app.py` are the single source of truth for server-side validation (used by `validate_task_form`, `edit`, and `update_status`) — keep them in sync with the CHECK constraints in `schema.sql` when adding new values.
- The `/status/<id>` endpoint expects JSON with `Content-Type: application/json` and key `"status"`.
- Using `cs50.SQL` means there is no explicit connection management; the wrapper handles it.
- A global `@app.errorhandler(Exception)` in `app.py` catches unhandled exceptions (e.g. DB errors), logs the full traceback server-side via `app.logger.exception`, and returns a generic response instead of leaking a stack trace — JSON for JSON requests, `templates/error.html` otherwise.
