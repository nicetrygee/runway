# AGENTS.md

## Setup & run

```bash
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
```

```bash
flask --app app:app run --debug
```

Set a `SECRET_KEY` via env or `.env` (gitignored, see `.env.example`) — the app fails fast at startup with a `RuntimeError` if it's missing.

`--debug` is for local dev only — it enables the Werkzeug debugger, which allows remote code execution if the server is ever reachable from an untrusted network. Never run with `--debug` (or `debug=True`) outside local dev.

## Running reliably in the background (macOS)

`flask run` is fine for active coding, but it dies the moment you close the terminal and won't come back on its own. For "keep this running without me watching it," use Gunicorn (multi-worker, replaces a crashed worker automatically) under launchd (macOS's service manager, restarts the whole process if it dies):

```bash
launchctl load deploy/com.nicetrygee.runway.plist    # start it (also survives crashes)
launchctl unload deploy/com.nicetrygee.runway.plist  # stop it for good
launchctl list | grep runway                         # check if it's running
```

The plist deliberately lives in `deploy/`, not `~/Library/LaunchAgents/` — macOS auto-loads (and, combined with `KeepAlive`, auto-*starts*) anything placed in `~/Library/LaunchAgents/` at every login. Keeping it in the repo means it only ever runs when you explicitly `launchctl load` it; nothing starts automatically at login or reboot. Verified live: killing the Gunicorn master with `kill -9` causes launchd to respawn it within ~2s; `launchctl unload` stops it and it stays stopped.

Binds to `127.0.0.1:8000` (localhost only, not exposed on the LAN) with 2 workers. Logs go to `logs/` (gitignored) — `gunicorn.log` / `gunicorn-error.log`.

Caveat: with 2 workers, Flask-Limiter's in-memory storage is per-process, so `/login`'s "10/min" is really closer to 20/min in aggregate (each worker tracks its own counter). Not worth fixing for local personal use; would need a shared backend (Redis) to actually enforce a global limit across workers.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Tests don't touch `runway.db` — `tests/conftest.py` points `DATABASE_URL` at a temp SQLite file (bootstrapped from `schema.sql`, reset before every test) and sets `SECRET_KEY` itself, so no `.env` is needed. It also calls `limiter.reset()` before every test since `flask_limiter`'s in-memory storage is a module-level singleton shared across the whole test session — without the reset, one test's requests count against another's rate-limit budget. CI runs the suite on every push/PR to `main` via `.github/workflows/ci.yml`.

## DB

- SQLite via `cs50.SQL`. DB path comes from `DATABASE_URL` env var, defaulting to `sqlite:///runway.db` (gitignored) — tests override this to point at a temp file.
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

- No linter, formatter, or typechecker config.
- `requirements.txt` and `requirements-dev.txt` are exact-pinned (`==`). When bumping a dependency, install the new version in `venv`, run the test suite, then update the pin to match — don't hand-edit a version number without testing it.
- Flash messages use categories `"success"` and `"error"`.
- `VALID_TASK_TYPES` and `VALID_STATUSES` in `app.py` are the single source of truth for server-side validation (used by `validate_task_form`, `edit`, and `update_status`) — keep them in sync with the CHECK constraints in `schema.sql` when adding new values.
- The `/status/<id>` endpoint expects JSON with `Content-Type: application/json` and key `"status"`.
- Using `cs50.SQL` means there is no explicit connection management; the wrapper handles it.
- A global `@app.errorhandler(Exception)` in `app.py` catches unhandled exceptions (e.g. DB errors), logs the full traceback server-side via `app.logger.exception`, and returns a generic response instead of leaking a stack trace — JSON for JSON requests, `templates/error.html` otherwise.
- `/login` (10/min) and `/register` (5/min) are rate-limited per-IP via Flask-Limiter, using the default in-memory storage. This is per-process, not shared — running under Gunicorn with 2 workers (see "Running reliably in the background") means the effective limit is roughly doubled. Switch to a Redis backend if this needs to be a real global limit.
- Logging is configured via `logging.basicConfig(..., force=True)` near the top of `app.py` — `force=True` is required because Flask/Werkzeug's dev server CLI configures the root logger before app.py's module body finishes, so a plain `basicConfig()` call is silently a no-op. Login/logout/register events log at INFO, failed logins at WARNING, unhandled exceptions at ERROR (via `app.logger.exception` in the error handler) — never log raw passwords.
