# Slice 0 — Foundation (SERIAL, merge before A–D)

*Execution spec for the first build agent. Companion files shipped with this slice: `schema.sql` (fresh installs), `migrate_slice0.py` (existing DBs), `recommend.py` (pure-engine skeleton), `tests/test_recommend.py` (Slice B target, currently red). Read `docs/product-brief.md` and `docs/technical-design.md` first.*

## Objective

Put the data model and the code structure in place so slices A–D can be built in parallel without colliding — **without changing any existing user-visible behaviour**. This slice is plumbing: migration + refactor + history helper + north-star files. No new screens.

## Why it's serial

It touches `schema.sql` and `app.py` broadly. If A–D start against a still-moving schema and a single 300-line `app.py`, you get constant merge conflicts. Land this on `main` first; then fan out.

## Scope

1. **Schema (additive).**
   - Fresh installs: replace `schema.sql` with the shipped version (adds `people`, `events`, and the new `tasks` columns).
   - Existing `runway.db`: run `python migrate_slice0.py`. It is idempotent, adds columns via `ALTER TABLE`, creates `people`/`events`, and backfills (`task_type`→`item_type`, `cognitive_load`→`effort_minutes` seed, `updated_at`→`last_touched_at`). Verified: safe to run twice.
   - `task_type` is **kept** and coexists with the new `item_type`; the current Add/Edit UI keeps using `task_type` until Slice A migrates it. Do not drop it in this slice.

2. **Enum single-source-of-truth.** In code, add `VALID_STREAMS`, `VALID_ITEM_TYPES`, `VALID_PRIORITIES`, `VALID_MODES` next to the existing `VALID_STATUSES`. Each must match the `CHECK` constraints in `schema.sql` and be listed in `AGENTS.md` — the same discipline the repo already applies to `VALID_TASK_TYPES`.

3. **Module refactor (behaviour-preserving).** Split the single `app.py` so parallel slices touch different files. Move code, don't rewrite it; keep the CSRF, session, rate-limit, logging, and error-handler wiring exactly as-is.

   | New module | Moves out of `app.py` | Later owner |
   |---|---|---|
   | `db.py` | every `db.execute(...)` query, wrapped in named functions (e.g. `tasks_for_user`, `insert_task`, `update_task`, `set_status`, `delete_task`, `completed_since`), **plus `log_event(...)`** (an event is just another INSERT on the same handle) | all slices |
   | `classify.py` | `ExtractedTask`, `WeeklySummary`, `extract_task_from_text`, `generate_weekly_summary`, the `ai_client` setup | Slice A / D |
   | `events.py` | *new* — **read-side helpers only** (`timeline_for_item`, `events_between`, counts). `log_event` lives in `db.py`, so a write and its event append stay atomic with no circular import | C / D |
   | `recommend.py` | *new* — shipped skeleton, pure, no imports of `app`/`db` | Slice B |
   | `capacity.py` | *new* — placeholder module + docstring only this slice | Slice D |
   | `app.py` | keeps routes, `login_required`, config, error handler — now thin | — |

   Routes stay at the same URLs (`/`, `/add`, `/quick-add`, `/summary`, `/edit/<id>`, `/delete/<id>`, `/status/<id>`, `/login`, `/register`, `/logout`); they call into `db.py`/`classify.py` instead of inlining SQL and AI calls.

   **Keep the tests green — the re-export.** The `db` handle (`SQL(...)`) moves to `db.py`, but the existing tests import it as `from app import db` (e.g. the `_add_task` helper in `tests/test_tasks.py`). So `app.py` must re-export it with `from db import db`, so `from app import db` still resolves. Without this one line the suite goes red for a reason unrelated to the refactor's correctness.

4. **`last_touched_at` + event on every write.** `insert_task`, `update_task`, and `set_status` in `db.py` set `last_touched_at = CURRENT_TIMESTAMP` and append a matching `events` row via `log_event` (`created` / `touched` / `status_changed`, and `completed` when status becomes `done`). **Decided: `log_event` lives in `db.py` and `events.py` is read-only** — so the write-plus-log invariant is atomic and the import graph stays acyclic (`app → db`, `events → db`, `app → events`; nothing imports `app`).

5. **North-star files.** Add the `AGENTS.md` block and `CLAUDE.md` from the design-doc appendices.

## Non-goals (do NOT do in this slice)

- No new UI, no new user-facing routes, no changes to how Add/Edit look or validate today.
- Do not implement `recommend()` — that's Slice B. Ship the skeleton red.
- Do not drop `task_type` or migrate the capture UI to `item_type` — that's Slice A.
- No `people`/`commitment`/`waiting` screens — those are C.

## Acceptance criteria

- `sqlite3 runway.db < schema.sql` bootstraps a fresh DB with all new tables/columns; `python migrate_slice0.py` upgrades an existing one and is safe to re-run.
- The **existing** test suite passes unchanged (the refactor is behaviour-preserving), plus a new `tests/test_migration.py` asserting the new columns exist and backfill mapped `task_type`→`item_type` correctly.
- `tests/test_recommend.py` is present and **failing with `NotImplementedError`** (9 tests) — it is Slice B's target, not this slice's.
- Creating/editing/status-changing a task writes a row to `events` and bumps `last_touched_at`.
- `AGENTS.md` lists every new `VALID_*` enum and the north-star block; `CLAUDE.md` exists.
- CI green on the branch.

## Agent kickoff prompt

> Read `docs/product-brief.md`, `docs/technical-design.md`, and `docs/slices/slice-0-foundation.md`. Implement **Slice 0 — Foundation** exactly as that spec defines it. It is behaviour-preserving plumbing: schema migration, an additive enum set, a module refactor that moves code without rewriting it, a `log_event` helper with `last_touched_at` bumping, and the north-star files. Do **not** implement `recommend()` and do **not** change any existing screen. Start in plan mode and show me the module-by-module move plan before editing. Keep the existing test suite green throughout; add `tests/test_migration.py`. The shipped `schema.sql`, `migrate_slice0.py`, `recommend.py`, and `tests/test_recommend.py` are starting points — wire them in, don't regenerate them.

## After this merges

Cut four worktrees and hand each agent its slice spec:

```
git worktree add ../runway-slice-a slice/capture      # Slice A — Work Inbox + classify
git worktree add ../runway-slice-b slice/recommend    # Slice B — engine (make test_recommend green) + Now view
git worktree add ../runway-slice-c slice/relationships # Slice C — commitments + delegated/waiting + follow-up
git worktree add ../runway-slice-d slice/review        # Slice D — weekly review + capacity
```
