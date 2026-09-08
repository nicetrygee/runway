# AGENTS.md

## North star (do not lose sight of this)
Runway answers, for one Engineering Manager: "What should I do right now,
what am I waiting on, what am I neglecting, where am I a bottleneck?"
It is a personal cognition aid, NOT a team task tracker. When in doubt,
reduce the EM's mental overhead — derive attention from state + time +
relationships. Never build toward being a Jira/Linear clone.

## Model
- One `items` table with a `stream` (task | commitment | delegation | waiting)
  and an optional `person_id`. Model direction, not four separate lists.
- `events` is append-only history. Never compute the past by mutating rows.
- The recommendation engine (`recommend.py`) is a PURE function: no DB, no LLM.
- New enums live once in code (VALID_*), mirrored in schema.sql CHECKs and here.

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
- `tasks.user_id` has an explicit index (`idx_tasks_user_id`) since every dashboard query filters on it. If you're bootstrapping against an existing `runway.db` created before this was added, re-run `schema.sql` (the `CREATE INDEX IF NOT EXISTS` is safe to apply to an already-populated table).

## Architecture

- **Module split (Slice 0 foundation)**: `app.py` is now thin — Flask wiring, routes, the auth decorator, config, and the error handler. Everything else moved out:
  - `db.py` — every DB query, one named function per read/write, plus `log_event` (an event append is just another INSERT on the same handle, so a write and its event stay atomic). `app.py` re-exports the handle with `from db import db` so `from app import db` still resolves (existing tests rely on this).
  - `classify.py` — AI extraction: `ExtractedTask`, `WeeklySummary`, `extract_task_from_text`, `generate_weekly_summary`, the `ai_client` setup.
  - `events.py` — read-only history helpers (`timeline_for_item`, `events_between`, `event_counts`). Write-side (`log_event`) stays in `db.py` on purpose — keeps the import graph acyclic (`app → db`, `app → classify`, `events → db`; nothing imports `app`).
  - `recommend.py` — the pure recommendation engine (Slice B; currently a `NotImplementedError` skeleton).
  - `capacity.py` — weekly review + capacity aggregations (Slice D; placeholder for now).
- Every `tasks` write (`insert_task`, `update_task`, `set_status` in `db.py`) bumps `last_touched_at` and appends a matching `events` row (`created` / `touched` / `status_changed`, plus `completed` when status becomes `done`).
- **Auth**: `login_required` decorator guards routes. Sessions use filesystem backend (`flask_session/`, gitignored). Passwords hashed with Werkzeug. Registration requires a password of at least 8 characters.
- **CSRF**: `Flask-WTF`'s `CSRFProtect` is wired up globally in `app.py`. Every POST form includes a hidden `csrf_token` field (see any of `templates/add.html`, `edit.html`, `login.html`, `index.html`'s delete form). The JSON `/status/<id>` endpoint reads the token from the `X-CSRFToken` header instead (set in `static/app.js` from the `<meta name="csrf-token">` tag in `layout.html`). Tests run with `WTF_CSRF_ENABLED = False` (set in `tests/conftest.py`) so they can post form data directly; `tests/test_csrf.py` re-enables it for one test to confirm protection actually rejects an unprotected POST.
- **Sessions**: filesystem-backed via `flask_session/` (gitignored), configured through a `cachelib.file.FileSystemCache` with `threshold=100` passed as `SESSION_CLIENT` — once the file count passes that, flask-session prunes the oldest on each new write. (Older flask-session versions used a `SESSION_FILE_THRESHOLD` config key directly; that's deprecated as of 0.8.0.)
- **Templates**: Jinja2, extending `layout.html`. Dark theme, Space Mono + Syne fonts.
- **Frontend JS**: `static/app.js` — a single AJAX status-update via `fetch()` to `/status/<id>`. No framework.
- **Task status values**: `backlog`, `in_progress`, `blocked`, `done` (enforced by CHECK constraint in SQLite and validated server-side).
- **Task type values**: `incident`, `rfc`, `1on1`, `hiring`, `delivery`, `other`.
- **Quick Add (AI)**: `POST /quick-add` (`app.py`) sends freeform text to Claude (`claude-sonnet-5` via the `anthropic` SDK's `messages.parse`, structured output into the `ExtractedTask` Pydantic model — chosen over Opus to keep per-call cost down for this lightweight extraction) and re-renders `templates/add.html` with the extracted fields pre-filled for the user to review before saving — it never inserts a task directly. Optional: requires `ANTHROPIC_API_KEY` in the environment; without it the route flashes an error and falls back to the blank form. `ExtractedTask.task_type` is a hardcoded `Literal` mirroring `VALID_TASK_TYPES` — keep the two in sync. Tests (`tests/test_quick_add.py`) monkeypatch `app.ai_client` and `app.extract_task_from_text` rather than calling the real API.
- **Weekly Summary (AI)**: `GET /summary` (`app.py`) queries tasks with `status = 'done'` updated in the last 7 days and renders `templates/summary.html`. The raw completed-task list always renders. On top of that, an AI prose recap (`generate_weekly_summary`, same `messages.parse` + Pydantic pattern as quick-add, model `claude-sonnet-5`, output into `WeeklySummary`) only runs if `WEEKLY_SUMMARY_AI_ENABLED=1` is set — this is a real per-page-view API call, so unlike quick-add it's opt-in even when `ANTHROPIC_API_KEY` is present, not just enabled-if-configured. If the flag is on but the key isn't configured, or the call fails, the route flashes and falls back to the plain list — it never blocks the page. Tests (`tests/test_weekly_summary.py`) monkeypatch `app.ai_client`, `app.generate_weekly_summary`, and `app.WEEKLY_SUMMARY_AI_ENABLED` rather than calling the real API.

## Conventions

- No linter, formatter, or typechecker config.
- `requirements.txt` and `requirements-dev.txt` are exact-pinned (`==`). When bumping a dependency, install the new version in `venv`, run the test suite, then update the pin to match — don't hand-edit a version number without testing it.
- Flash messages use categories `"success"` and `"error"`.
- `VALID_TASK_TYPES` and `VALID_STATUSES` in `app.py` are the single source of truth for server-side validation (used by `validate_task_form`, `edit`, and `update_status`) — keep them in sync with the CHECK constraints in `schema.sql` when adding new values.
- Slice 0 added four more `VALID_*` lists in `app.py`, next to the two above, mirroring the new `tasks` columns' CHECK constraints in `schema.sql`. Not wired into any form/validation yet — Slices A–D do that as they build the surfaces that use them:
  - `VALID_STREAMS` — `task`, `commitment`, `delegation`, `waiting`
  - `VALID_ITEM_TYPES` — `People`, `Delivery`, `Technical`, `Stakeholder`, `Strategy`, `Hiring`, `Operational`, `Personal-admin`
  - `VALID_PRIORITIES` — `Critical`, `Important`, `Normal`, `Delegate`, `Ignore`
  - `VALID_MODES` — `reactive`, `proactive`
- The `/status/<id>` endpoint expects JSON with `Content-Type: application/json` and key `"status"`.
- Using `cs50.SQL` means there is no explicit connection management; the wrapper handles it.
- A global `@app.errorhandler(Exception)` in `app.py` catches unhandled exceptions (e.g. DB errors), logs the full traceback server-side via `app.logger.exception`, and returns a generic response instead of leaking a stack trace — JSON for JSON requests, `templates/error.html` otherwise.
- `/login` (10/min) and `/register` (5/min) are rate-limited per-IP via Flask-Limiter, using the default in-memory storage. This is per-process, not shared — running under Gunicorn with 2 workers (see "Running reliably in the background") means the effective limit is roughly doubled. Switch to a Redis backend if this needs to be a real global limit.
- Logging is configured via `logging.basicConfig(..., force=True)` near the top of `app.py` — `force=True` is required because Flask/Werkzeug's dev server CLI configures the root logger before app.py's module body finishes, so a plain `basicConfig()` call is silently a no-op. Login/logout/register events log at INFO, failed logins at WARNING, unhandled exceptions at ERROR (via `app.logger.exception` in the error handler) — never log raw passwords.
