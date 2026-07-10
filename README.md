# Runway

![CI](https://github.com/nicetrygee/runway/actions/workflows/ci.yml/badge.svg)

A task planner built for Engineering Managers, who don't just juggle deadlines — they juggle incidents, RFCs, 1:1s, hiring, and delivery work in the same afternoon, each demanding a different kind of attention. Runway tracks tasks by **cognitive load** (1–5) and **blast radius** (who's affected if it slips) alongside the usual title/due-date/status, so the dashboard reflects actual capacity, not just a to-do count.

## Quickstart

```bash
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
sqlite3 runway.db < schema.sql
```

Set `SECRET_KEY` via env or a `.env` file (e.g. `SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")`) — the app won't start without it.

```bash
flask --app app:app run --debug
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

25 tests covering auth, task CRUD/validation, the global error handler, and rate limiting. CI runs the suite on every push/PR to `main`.

## Engineering notes

- **Validation**: task type, status, and cognitive load are validated server-side against the same constraints enforced by SQLite's `CHECK` clauses — bad input gets a flash message, not a stack trace.
- **Error handling**: a global exception handler logs the full traceback server-side and returns a generic response to the client either way, whether or not `--debug` was left on by accident.
- **Rate limiting**: `/login` and `/register` are rate-limited per-IP to slow brute-force attempts.
- **Logging**: structured, leveled logs for auth events (login/logout/register, failed attempts) — never raw passwords.
- **Runs reliably in the background**: a Gunicorn + launchd setup (`deploy/`) that self-heals if the process crashes, without auto-starting on login/reboot. See `AGENTS.md` for the exact commands.

`app.py` is a single ~240-line file by design — this app doesn't have enough surface area to justify splitting into blueprints/packages, and doing so would add indirection without adding clarity.

Deeper technical notes (DB config, conventions, rationale for specific choices) live in `AGENTS.md`.

## Project layout

| Path | Purpose |
|---|---|
| `app.py` | Routes, auth, validation, error handling, rate limiting, logging |
| `schema.sql` | `users` and `tasks` tables |
| `templates/` | Jinja2 templates (dashboard, add/edit forms, login/register, error page) |
| `static/` | Dark-themed CSS and a small `fetch()`-based status-update script |
| `tests/` | pytest suite |
| `deploy/` | launchd config for running in the background |

## Design choices

**Blast radius + cognitive load instead of a priority flag.** Blast radius names who or what is blocked if a task slips. Cognitive load (1–5) separates *time-consuming* from *mentally demanding* — a light calendar day can still be a heavy one. Splitting those two dimensions gives a more honest capacity signal than a single priority label.

**SQLite + vanilla JS over Postgres + a frontend framework.** This is a single-user local tool, not a service with concurrent writers. `cs50.SQL` keeps queries readable, and one `fetch()` call for the status dropdown is all the interactivity the UI actually needs — reaching for a framework here would be solving a problem this app doesn't have.
