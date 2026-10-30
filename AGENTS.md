# AGENTS.md

## North star (do not lose sight of this)
Runway answers, for one Engineering Manager: "What should I do right now,
what am I waiting on, what am I neglecting, where am I a bottleneck?"
It is a personal cognition aid, NOT a team task tracker. When in doubt,
reduce the EM's mental overhead — derive attention from state + time +
relationships. Never build toward being a Jira/Linear clone.

## AI features — rules
Every AI feature follows these. Each rule names the check that enforces it, or
says *review only* when nothing automated can. Background:
`docs/eval-observability.md`.

1. **Every AI call goes through `llm.complete`.** Features never import a
   provider SDK. → ruff `TID251` (`pyproject.toml`: `openai`/`anthropic` banned
   outside `llm.py`), proven live by `tests/test_rules.py`.
2. **Every AI call is traced**, including failures and blocked calls.
   → `tests/test_tracing.py` (success, failure, one row per call),
   `tests/test_llm.py` (blocked hosted calls).
3. **A prompt or schema change bumps its version**, then
   `python -m prompts lock`, then `python -m evals quick_add --live` to
   re-record. → `tests/test_prompts.py` (fails on drift, with the fix);
   CI replay fails on missing recordings.
4. **Hosted calls need `RUNWAY_LAB=1`.** → the guard in `llm.complete`
   (`tests/test_llm.py`). **Lab data is synthetic** → *review only*.
5. **No self-grading.** Eval cases are written in a separate session from the
   feature, from the spec only, and the owner approves every expected answer.
   Never edit `cases.jsonl` in a PR that touches feature or prompt code.
   → *review only*.
6. **AI output is a proposal; a human approves before any write.**
   → `tests/test_quick_add.py::test_quick_add_never_saves_a_task` for today's
   features; *review only* for new ones until Phase 3 adds approval tests.
7. **Replay never falls back to a live call.** → `tests/test_eval_runner.py`
   (missing-recording tests).
8. **Cases are data PRs, separate from code.** Recordings are the exception:
   re-recordings for a version bump, and first recordings for new cases, ride
   along in that PR as a separate commit, since they're machine output, not
   judgement. → *review only*.
9. **AI modules are fully typed** (`llm`, `tracing`, `prompts`, `classify`,
   `evals.*`). → the mypy override in `pyproject.toml`.

## Model
- One item table — named `tasks` for continuity, conceptually "items" — with a
  `stream` (task | commitment | delegation | waiting) and an optional
  `person_id`. Model direction, not four separate lists. Alongside it:
  `people` (the counterparty directory), `events`, and per-user `settings`.
- `events` is append-only history. Never compute the past by mutating rows.
- The recommendation engine (`recommend.py`) is a PURE function: no DB, no LLM.
  `capacity.py` follows the same rule.
- Every stored timestamp is naive UTC (SQLite's `CURRENT_TIMESTAMP`). Get "now"
  from `clock.utc_now()`, never `datetime.now()`, or comparisons drift by the
  server's UTC offset.
- New enums live once in code (VALID_*), mirrored in schema.sql CHECKs and here.

## Setup & run

```bash
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
sqlite3 runway.db < schema.sql
```

```bash
flask --app app:app run --debug
```

Set a `SECRET_KEY` via env or `.env` (gitignored, see `.env.example`) — the app fails fast at startup with a `RuntimeError` if it's missing.

A minimal Docker image (`Dockerfile`, `docker-entrypoint.sh`) bootstraps a fresh `runway.db` on first run and serves via Gunicorn on port 8000. It's for a quick local look, not deployment — the DB isn't persisted unless you mount a volume over `/app/runway.db`.

`--debug` is for local dev only — it enables the Werkzeug debugger, which allows remote code execution if the server is ever reachable from an untrusted network. Never run with `--debug` (or `debug=True`) outside local dev.

Evals (LM Studio running for `--live`; `--replay` needs no model):

```bash
python -m evals quick_add --live    # call the model, write recordings + report
python -m evals quick_add --replay  # re-score recordings only; fails if any are missing
```

## Running reliably in the background (macOS)

`flask run` is fine for active coding, but it dies the moment you close the terminal and won't come back on its own. For "keep this running without me watching it," use Gunicorn (multi-worker, replaces a crashed worker automatically) under launchd (macOS's service manager, restarts the whole process if it dies):

First, edit `deploy/com.nicetrygee.runway.plist` and replace the `/path/to/runway` placeholders with this repo's absolute path on your machine — plist files can't expand `$HOME` or env vars.

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
ruff check .      # lint
mypy .            # type check
pytest tests/ -v
```

Tests don't touch `runway.db` — `tests/conftest.py` points `DATABASE_URL` at a temp SQLite file (bootstrapped from `schema.sql`, reset before every test) and sets `SECRET_KEY` itself, so no `.env` is needed. It also calls `limiter.reset()` before every test since `flask_limiter`'s in-memory storage is a module-level singleton shared across the whole test session — without the reset, one test's requests count against another's rate-limit budget. CI (`.github/workflows/ci.yml`) runs ruff, mypy, and the suite on every push/PR to `main`, then `python -m evals quick_add --replay` (no model, no secrets; `LLM_MODEL` pinned to the recordings' model); all four must pass. Replay fails only on a missing recording or a crash — usually a prompt version bumped without a `--live` re-record — never on scores (D24). Per-field scores appear in the run's summary and as the `eval-replay-report` artifact.

## DB

- SQLite via `cs50.SQL`. DB path comes from `DATABASE_URL` env var, defaulting to `sqlite:///runway.db` (gitignored) — tests override this to point at a temp file.
- On first query, `cs50.SQL` auto-creates the file if it doesn't exist, but tables are only created if `schema.sql` has been executed. Bootstrap a fresh db with `schema.sql` alone; it is the complete current schema:
  ```bash
  sqlite3 runway.db < schema.sql
  ```
- Upgrading an older db: back it up, then run `migrate_slice0.py` (pre-Slice-0 dbs), `migrate_slice_d.py` (dbs without `settings`), and `migrate_retire_legacy_fields.py` (drops `task_type`/`cognitive_load` after backfilling `item_type`/`effort_minutes`), in that order. All three are idempotent, so running the whole chain is always safe. Any new migration must leave the db matching `schema.sql`; `tests/test_migrate_retire_legacy_fields.py` checks the chain against it.
- Query placeholder style is `?` (not `%s` or `:named`). This is the cs50 library convention.
- `tasks.user_id` has an explicit index (`idx_tasks_user_id`) since every dashboard query filters on it. If you're bootstrapping against an existing `runway.db` created before this was added, re-run `schema.sql` (the `CREATE INDEX IF NOT EXISTS` is safe to apply to an already-populated table).

## Architecture

- **Module split (Slice 0 foundation)**: `app.py` is now thin — Flask wiring, routes, the auth decorator, config, and the error handler. Everything else moved out:
  - `db.py` — every DB query, one named function per read/write, plus `log_event`. Every task write and its event append run inside `with transaction():`, so they commit or roll back together. `app.py` re-exports the handle with `from db import db` so `from app import db` still resolves (existing tests rely on this).
  - `classify.py` — the AI features, both via `llm.complete`: `ExtractedTask` / `extract_task_from_text` / `PROMPT_VERSION` / `QUICK_ADD_SYSTEM` (Quick Add) and `WeeklySummary` / `generate_weekly_summary` / `WEEKLY_SUMMARY_PROMPT_VERSION` / `WEEKLY_SUMMARY_SYSTEM` (weekly recap). Each system prompt is a module-level template constant (`QUICK_ADD_SYSTEM` takes `{today}` via `str.format`); the functions send exactly that template. No provider SDK is imported here.
  - `prompts.py` — prompt versioning (D23). `REGISTRY` maps each feature to its version, template and output schema; `prompts.lock.json` (committed) pins every version, old ones included, to a sha256 of template + schema. `tests/test_prompts.py` fails if a prompt or schema changes without a version bump, with the fix in the message. **To change a prompt or schema:** edit it → bump its version → `python -m prompts lock` → `python -m evals quick_add --live` to re-record (Quick Add only; the summary has no evals). `lock` refuses to change the hash of an already-locked version. Not hashed: how the user message is built (e.g. the summary's task-line format) — that's code, covered by its own tests.
  - `llm.py` — the one LLM interface (`complete(messages, *, feature, prompt_version, schema=None, settings=ModelSettings())` → `LLMResult`). Features never call a provider SDK directly. One adapter so far, `LMStudioAdapter` (OpenAI-compatible; `LLM_BASE_URL` / `LLM_MODEL`). Strips `<think>…</think>` before parsing; raises `LLMUnavailable` when the provider can't be reached. **Hosted guard (D22):** every adapter declares `hosted: bool` (`LMStudioAdapter` is hosted when the `LLM_BASE_URL` host isn't loopback; test and replay fakes say `hosted = False`, and there's deliberately no default). `complete()` raises `HostedCallBlocked` (a subclass of `LLMUnavailable`, so existing fallbacks catch it) *before* sending unless `RUNWAY_LAB=1`, and the block is traced. `app.py` logs a WARNING at startup when lab mode is on. See `docs/eval-observability.md`.
  - `tracing.py` — writes one row per `llm.complete` call (success or failure) to `traces.db`, a separate gitignored SQLite file (`TRACES_DB_PATH`). Each row records `hosted` (0/1; NULL on rows from before the guard — older files gain the column in place). A tracing failure is logged and swallowed, never raised.
  - `evals/` — the eval runner, its own command (D9), not pytest: `python -m evals quick_add --live` runs every case in `evals/quick_add/cases.jsonl` through the real `extract_task_from_text(note, now=<case today>)` against LM Studio, one at a time, traced as normal, and writes each raw response to `evals/quick_add/recordings.jsonl` (committed). `--replay` re-runs the same feature code with `llm.adapter` swapped for one that serves those recordings — no model call, and its incidental traces go to a throwaway file, not `traces.db`. Recordings are keyed on (model, `llm.ModelSettings()`, `classify.PROMPT_VERSION`, note, today, run); a case with no matching recording makes replay exit 1 naming the case — it never falls back to live. **`--repeat N`** (D4, default 1) runs every case N times, one whole pass after another and never in parallel; each run gets its own recording with `run` 0..N-1 in its key (a recording with no `run` predates E1b.4 and counts as run 0), and replay with `--repeat N` needs all N, naming each missing `<case> run <r>`. Repeat runs report per field the mean accuracy, the worst run and the **flip rate** (share of cases whose runs disagree on that field's *value* — an unparseable reply counts as its own value), plus per-run tokens, p50/p95 latency and tokens/s, taken from the recordings so a replay reproduces the live report. **`--model ID`** overrides `LLM_MODEL` for that run only. So bumping the prompt version or `LLM_MODEL` needs a fresh `--live` run. `--live` stays behind the hosted guard (the recording wrapper forwards the real adapter's `hosted`); a blocked call exits 1. `evals/scoring.py` holds the pure per-field scorers (exact for person/due_date/stream, within one bucket for effort, exact-or-`acceptable` for priority/mode/item_type; free-text fields unscored). Each run writes `evals/reports/<timestamp>-<mode>.md` and the same content as `<timestamp>-<mode>.json` (both gitignored; the JSON is for the dashboard and model comparisons). Cases follow AI rule 5 (no self-grading).
  - `events.py` — read-only history helpers (`timeline_for_item`, `events_between`, `event_counts`, `last_followup_for_item`). Write-side (`log_event`) stays in `db.py` on purpose — keeps the import graph acyclic (`app → db`, `app → classify`, `events → db`, `classify → llm → tracing`, `prompts → classify`, `app`/`classify`/`tracing → clock`; nothing imports `app`).
  - `recommend.py` — the pure recommendation engine (Slice B): candidate filter, hard time-fit filter with a smallest-first fallback, weighted score (priority, due urgency, blocking, staleness, minus a context-switch penalty), and a plain-English reason per item. Weights are constants at the top of the file.
  - `capacity.py` — weekly review + capacity aggregations, plus the dashboard's neglected/bottleneck widgets (Slice D). Pure, like `recommend.py`: no DB access, takes plain rows and returns computed aggregates.
  - `clock.py` — `utc_now()`, the single source of "now" (see Model).
- Every `tasks` write in `db.py` bumps `last_touched_at` and appends a matching `events` row: `insert_task` → `created`; `update_task` → `touched`; `set_status` → `status_changed` (plus `completed` when status becomes `done`); `follow_up` → `followed_up`; `set_relationship` → `delegated` or `touched`; `carry_forward_item` → `touched` with a `carry_forward` payload.
- **Auth**: `login_required` decorator guards routes. Sessions use filesystem backend (`flask_session/`, gitignored). Passwords hashed with Werkzeug. Registration requires a password of at least 8 characters.
- **CSRF**: `Flask-WTF`'s `CSRFProtect` is wired up globally in `app.py`. Every POST form includes a hidden `csrf_token` field (see any of `templates/add.html`, `edit.html`, `login.html`, `index.html`'s delete form). The JSON `/status/<id>` endpoint reads the token from the `X-CSRFToken` header instead (set in `static/app.js` from the `<meta name="csrf-token">` tag in `layout.html`). Tests run with `WTF_CSRF_ENABLED = False` (set in `tests/conftest.py`) so they can post form data directly; `tests/test_csrf.py` re-enables it for one test to confirm protection actually rejects an unprotected POST.
- **Sessions**: filesystem-backed via `flask_session/` (gitignored), configured through a `cachelib.file.FileSystemCache` with `threshold=100` passed as `SESSION_CLIENT` — once the file count passes that, flask-session prunes the oldest on each new write. (Older flask-session versions used a `SESSION_FILE_THRESHOLD` config key directly; that's deprecated as of 0.8.0.)
- **Templates**: Jinja2, extending `layout.html`. Dark theme, Space Mono + Syne fonts.
- **Frontend JS**: `static/app.js` — a single AJAX status-update via `fetch()` to `/status/<id>`. No framework.
- **Task status values**: `backlog`, `in_progress`, `blocked`, `done` (enforced by CHECK constraint in SQLite and validated server-side).
- **Task type values**: `incident`, `rfc`, `1on1`, `hiring`, `delivery`, `other`.
- **Quick Add (AI)**: `POST /quick-add` (`app.py`) sends freeform text to a local model (Qwen3-8B in LM Studio, provisionally) through `llm.complete`, with structured output constrained to the `ExtractedTask` Pydantic model, and re-renders `templates/add.html` with the extracted fields pre-filled for the user to review before saving — it never inserts a task directly. `extract_task_from_text(text, now=None)` takes an optional `now` so evals can fix "today"; the system prompt starts with `/no_think` (thinking off) and is versioned by `classify.PROMPT_VERSION`, enforced by `prompts.lock.json` (see `prompts.py`). Every call is traced to `traces.db`. If LM Studio is unreachable (`LLMUnavailable`) the route flashes an error and falls back to the blank form; any other failure flashes "Couldn't parse that". `ExtractedTask`'s `item_type`/`priority`/`stream`/`mode` are hardcoded `Literal`s mirroring the matching `VALID_*` lists — keep them in sync. Tests never hit the network: `tests/test_quick_add.py` monkeypatches `app.extract_task_from_text`; `test_classify.py`/`test_llm.py`/`test_tracing.py` use the `fake_llm` fixture (`tests/conftest.py`), which swaps `llm.adapter`. `conftest.py` also points `TRACES_DB_PATH` at a temp file.
- **Weekly Summary (AI)**: `GET /summary` (`app.py`) queries tasks with `status = 'done'` updated in the last 7 days and renders `templates/summary.html`; `/review` shows the same recap as its AI narrative. The raw completed-task list (or the review numbers) always renders. On top of that, an AI prose recap (`generate_weekly_summary`: the local model through `llm.complete`, `/no_think`, structured output into `WeeklySummary`, traced as feature `weekly_summary` with `WEEKLY_SUMMARY_PROMPT_VERSION`) only runs if `WEEKLY_SUMMARY_AI_ENABLED=1` is set — it's a model call that adds seconds to every page view, so unlike Quick Add it's opt-in. It's traced but has no evals. If LM Studio is unreachable (`LLMUnavailable`) the route flashes "is LM Studio running?"; any other failure flashes "Couldn't generate a summary"; either way it falls back to the plain page and never blocks it. Tests: `tests/test_classify.py` covers the function with the `fake_llm` fixture; `tests/test_weekly_summary.py` monkeypatches `app.generate_weekly_summary` and `app.WEEKLY_SUMMARY_AI_ENABLED` for both routes.

## Conventions

- Lint and type checks are configured in `pyproject.toml`: ruff (`E`, `F`, `W`, `I`, `B`, plus `TID` for the provider-SDK import ban (AI rule 1), line length 100; `flask_session` pinned as third-party so import order doesn't depend on whether the app has been run locally) and mypy (lenient repo-wide; `disallow_untyped_defs`, `disallow_incomplete_defs` and `warn_return_any` for the AI modules, AI rule 9). Both are CI gates — run them before pushing. No auto-formatter is configured.
- Python version is kept the same in the local venv, CI (`actions/setup-python`), and the `Dockerfile` — currently 3.14. Bump all three together.
- Dependabot (`.github/dependabot.yml`) opens weekly PRs for pip, GitHub Actions, and the Docker base image.
- `requirements.txt` and `requirements-dev.txt` are exact-pinned (`==`). When bumping a dependency, install the new version in `venv`, run the test suite, then update the pin to match — don't hand-edit a version number without testing it.
- Flash messages use categories `"success"` and `"error"`.
- The `VALID_*` lists in `app.py` are the single source of truth for server-side validation — keep them in sync with the CHECK constraints in `schema.sql` when adding new values. `validate_task_form` (shared by `/add` and `/edit`) checks item type, priority, mode, effort and, on `/edit`, status; `validate_capture_fields` checks stream on `/add`; `/items/<id>/relationship` checks stream; `/status/<id>` checks status. `/add` and `/edit` render the same fields from `templates/_item_fields.html`.
  - `VALID_STATUSES` — `backlog`, `in_progress`, `blocked`, `done`
  - `VALID_STREAMS` — `task`, `commitment`, `delegation`, `waiting`
  - `VALID_ITEM_TYPES` — `People`, `Delivery`, `Technical`, `Stakeholder`, `Strategy`, `Hiring`, `Operational`, `Personal-admin`
  - `VALID_PRIORITIES` — `Critical`, `Important`, `Normal`, `Delegate`, `Ignore`
  - `VALID_MODES` — `reactive`, `proactive`
- The `/status/<id>` endpoint expects JSON with `Content-Type: application/json` and key `"status"`. It returns 404 `{"error": "Task not found"}` for a task that isn't the user's.
- Ownership: every query and write is scoped with `AND user_id = ?`, but that alone doesn't stop the matching event being logged. Any route that writes to an item must confirm the item is the user's — either check `db_module.get_task(id, uid)` first (`/edit`, `/follow-up`, `/items/<id>/relationship`) or use a write helper that returns whether its `UPDATE` matched a row and only then logs (`set_status`, `carry_forward_item`). Parse ids from form input defensively — bad input is a validation error, not a 500.
- Using `cs50.SQL` means there is no explicit connection management; the wrapper handles it.
- A global `@app.errorhandler(Exception)` in `app.py` catches unhandled exceptions (e.g. DB errors), logs the full traceback server-side via `app.logger.exception`, and returns a generic response instead of leaking a stack trace — JSON for JSON requests, `templates/error.html` otherwise.
- `/login` (10/min) and `/register` (5/min) are rate-limited per-IP via Flask-Limiter, using the default in-memory storage. This is per-process, not shared — running under Gunicorn with 2 workers (see "Running reliably in the background") means the effective limit is roughly doubled. Switch to a Redis backend if this needs to be a real global limit.
- Logging is configured via `logging.basicConfig(..., force=True)` near the top of `app.py` — `force=True` is required because Flask/Werkzeug's dev server CLI configures the root logger before app.py's module body finishes, so a plain `basicConfig()` call is silently a no-op. Login/logout/register events log at INFO, failed logins at WARNING, unhandled exceptions at ERROR (via `app.logger.exception` in the error handler) — never log raw passwords.
