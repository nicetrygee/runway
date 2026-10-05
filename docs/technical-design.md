# Runway — Technical Design (MVP differentiation)

*Status: delivered. Written Sep 2026 as the plan for the MVP build, which was carried out in slices: Slice 0 merged 8 Sep 2026 (PR #1), Slices A–D and the dashboard merged 9 Sep 2026 (PRs #3–#7). Companion to [`product-brief.md`](product-brief.md). The plan is kept below as written, with present-tense corrections where the code differs. [Where the build differed from the plan](#where-the-build-differed-from-the-plan) lists every departure. For the current architecture and conventions, `AGENTS.md` is authoritative.*

## Starting point

The pre-build app was a single-file Flask app, and the design extended it rather than replacing it:

- **Single-file Flask app:** `app.py` (~170 lines at the time): all routes, the `login_required` decorator, config, and a global exception handler.
- **DB:** SQLite via `cs50.SQL`, `?` placeholders, `DATABASE_URL` env (default `sqlite:///runway.db`). Tables bootstrapped from `schema.sql` with `CHECK` constraints. `tasks.user_id` indexed.
- **`tasks`:** `user_id, title, task_type, blast_radius, sprint, cognitive_load (1–5), due_date, notes, status`. `task_type ∈ {incident, rfc, 1on1, hiring, delivery, other}`, `status ∈ {backlog, in_progress, blocked, done}`.
- **AI:** an `anthropic` client with `extract_task_from_text()` (freeform → structured `ExtractedTask` via Pydantic) and a dormant `WeeklySummary`. The API key was optional; the app ran without it.
- **Conventions:** `VALID_TASK_TYPES` / `VALID_STATUSES` in `app.py` were the single source of truth for validation, kept in sync with the `CHECK` constraints in `schema.sql` **and** with `AGENTS.md`. CSRF via Flask-WTF on every POST; `/status/<id>` reads the token from the `X-CSRFToken` header. pytest, `limiter.reset()` per test, CI on every push.

## Design principles

1. **Model direction + time, not lists.** One item table with a `stream` discriminator and an optional person, not four tables.
2. **The recommendation engine is a pure function.** No DB access, no LLM call. Inputs are plain data, output is a ranked list. That makes it unit-testable, and it was the most important decision for a test-first build: the engine was built against a spec of cases, not against a running app.
3. **An append-only `events` log is the source of history.** Weekly-review counts, staleness/neglect, and the later "EM memory" all read from it. History is never computed by mutating rows.
4. **Break the single file into modules.** Parallel slices could not all edit one `app.py` without constant conflicts. The foundation slice carved out the data, AI and pure-logic layers so each later slice mostly added its own module, template and routes.
5. **Keep the single-source-of-truth discipline.** Every new enum lives once in code, mirrored in `schema.sql` `CHECK`s and in `AGENTS.md`, as the original `VALID_*` lists did.

## Data model

### `people`
Lightweight, not an HR record. The join point for commitments, delegation, waiting-on, and the later people-attention view.

```sql
CREATE TABLE people (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    role        TEXT,            -- e.g. "Product", "CTO", "Engineer", free text
    notes       TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX idx_people_user_id ON people(user_id);
```

### `events` (append-only)
Every meaningful change appends a row. Rows are never updated. They are deleted only with their item (see the `ON DELETE CASCADE` note under [Where the build differed](#where-the-build-differed-from-the-plan)).

```sql
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    item_id     INTEGER,             -- nullable: some events aren't item-scoped
    person_id   INTEGER,             -- nullable
    event_type  TEXT NOT NULL,       -- created | status_changed | touched | completed
                                     -- | delegated | followed_up | note_added | interaction
                                     -- (enforced by a CHECK)
    payload     TEXT,                -- JSON blob, event-specific
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)   REFERENCES users(id),
    FOREIGN KEY (item_id)   REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (person_id) REFERENCES people(id)
);
CREATE INDEX idx_events_user_id ON events(user_id);
CREATE INDEX idx_events_item_id ON events(item_id);
```

### Evolving `tasks`
The table kept its name, `tasks`, for continuity; conceptually its rows are "items", since they're no longer just my to-dos.

New / changed columns:

| Column | Values | Purpose |
|---|---|---|
| `stream` | `task` \| `commitment` \| `delegation` \| `waiting` | direction of the item (see brief) |
| `item_type` | People, Delivery, Technical, Stakeholder, Strategy, Hiring, Operational, Personal-admin | EM taxonomy, alongside `task_type` |
| `priority` | Critical \| Important \| Normal \| Delegate \| Ignore | triage priority |
| `effort_minutes` | integer (canonical) | from 5m/30m/1h/2h/half-day/multi-day → 5/30/60/120/240/480 |
| `mode` | `reactive` \| `proactive` | strategic-time protection + weekly balance |
| `person_id` | FK → people, nullable | counterparty (to-whom / delegated-to / waited-on) |
| `is_blocking` | integer 0/N | how many people/things this is holding up (bottleneck signal) |
| `last_touched_at` | timestamp | staleness/neglect + follow-up aging; bumped on every write |
| `due_date`, `status`, `notes`, `cognitive_load`, `task_type` | (kept) | `status` unchanged; `cognitive_load` an optional second axis |

**Effort mapping** (store minutes, present labels): `5m→5, 30m→30, 1h→60, 2h→120, half-day→240, multi-day→480`. Multi-day just means "won't fit any single window" for time-fit purposes.

**Migration of existing rows** (`migrate_slice0.py`, idempotent):
- `task_type` → `item_type`: `incident→Operational`, `rfc→Technical`, `1on1→People`, `hiring→Hiring`, `delivery→Delivery`, `other→Operational`. The old column was kept.
- `status` unchanged.
- `stream` defaults to `task` for all existing rows.
- `priority` defaults to `Normal`; `mode` defaults to `reactive`.
- `effort_minutes` seeded from `cognitive_load` (1→30, 2→60, 3→120, 4→240, 5→480). It's only a rough seed until items are re-estimated.
- `last_touched_at` backfilled from `updated_at`, falling back to `created_at`.

The migration follows the `IF NOT EXISTS` pattern so it's safe to re-run against a populated `runway.db`.

### Enums
`VALID_STREAMS`, `VALID_ITEM_TYPES`, `VALID_PRIORITIES`, `VALID_MODES` sit alongside `VALID_TASK_TYPES` / `VALID_STATUSES` in `app.py`, mirrored in `schema.sql` `CHECK` constraints and listed in `AGENTS.md`.

## Module structure

The plan:

```
app.py          # Flask wiring, routes, auth decorator, error handler (thin)
db.py           # all cs50.SQL queries, one function per read/write
recommend.py    # the PURE recommendation engine (no imports of app/db)
classify.py     # AI extraction — extends the existing extract_task_from_text
capacity.py     # weekly review + capacity aggregations
events.py       # append-only event helpers (log_event, read timelines)
templates/      # one template per surface
static/app.js   # keep the vanilla-JS fetch approach
tests/          # mirrors the modules; recommend.py gets the densest tests
```

All of these modules exist, with two differences. `app.py` did not stay thin (about 690 lines today). `log_event` lives in `db.py`, not `events.py`. Both are explained below. cs50 conventions and the CSRF/session/rate-limit wiring were kept as they were, and the refactor was behaviour-preserving: the suite stayed green through it.

## The recommendation engine (feature 2)

A pure function, built first against test cases, before any UI.

```python
# recommend.py  — no DB, no network, no Flask
def recommend(items: list[Item], available_minutes: int, now: datetime,
              limit: int = 5, recent_item_type: str | None = None
              ) -> list[Recommendation]:
    """Rank the EM's own actionable work for right now.
    Only `task` and `commitment` items that aren't done or Ignore are
    candidates (delegation/waiting are someone else's court). Returns top
    `limit`, each with a human-readable reason."""
```

**Scoring:** a weighted sum of normalised signals, after a hard time-fit filter:

```
score(item) =
      W_PRIORITY  * priority_weight(item.priority)      # Critical≫Important>Normal; Ignore=excluded
    + W_DUE       * urgency(item.due_date, now)          # rises as the deadline nears; overdue = max
    + W_BLOCK     * blocking(item.is_blocking)           # bottleneck: unblock others first
    + W_NEGLECT   * staleness(item.last_touched_at, now) # nudge quiet high-stakes items up
    - W_SWITCH    * context_switch_penalty(item, recent) # small penalty for switching item_type
```

**Time-fit is a hard rule, not a weight:** any candidate whose `effort_minutes > available_minutes` is filtered out *before* ranking. If nothing fits, the engine returns the smallest items marked `fits=False` with a "you only have N minutes" reason, rather than an empty screen. This is the "25 minutes before a meeting shouldn't suggest a 2-hour doc" behaviour, and it's the first thing the tests assert.

Weights are named constants at the top of `recommend.py` so they're tunable in one place. They are hand-set.

**Worked example:** 25 minutes available →
1. *Follow up with Product on migration scope* — 10 min · blocking 3 people  ← high `is_blocking`, fits easily
2. *Review hiring feedback* — 20 min · Important  ← fits
3. *Prepare exec update* — 45 min · due tomorrow  ← **excluded from "now"** (doesn't fit), shown separately as work for a longer window

The engine is deterministic and LLM-free. The LLM's job is capture/classification (Slice A) and, later, EM-memory Q&A, not ranking.

## Capacity + weekly review (feature 5)

Reads from `events` + `tasks`, no calendar:
- **Counts:** completed / carried-forward / delegated / waiting, from the week's event history.
- **Time breakdown:** `effort_minutes` summed by `item_type` and by `mode`, plus one manually entered "meeting hours this week" figure. Strategic-time % = proactive effort ÷ total.
- **Capacity read:** `available_hours` (a per-user setting, default 38) vs committed open `effort_minutes` → "116% committed" / "~24h available".
- **Delegation suggestions:** items with `priority == Delegate`, or high-effort Normal-priority items.
- **Reset prompt:** "what do you want to carry into next week?" writes carry-forward events.

## Build order — slices

**Slice 0 — Foundation (serial, merged before anything else).**
Schema migration (`people`, `events`, evolved `tasks`, new enums + `CHECK`s), the `app.py` module refactor, the `log_event` helper, `last_touched_at` bumping on writes, backfill, tests kept green, and the `AGENTS.md`/`CLAUDE.md` north star.
*Acceptance:* full suite green; existing tasks migrated and visible; new columns populated with sane defaults; enums validated server-side and by `CHECK`.

Then four slices in parallel, each on its own branch and PR, because they touch mostly separate modules and templates and only add routes:

**Slice A — Capture & classify (Work Inbox).** Fast add, plus `classify.py` extended from `extract_task_from_text` to the new taxonomy, detecting a `person` and the `stream`. Graceful no-AI fallback (manual fields).
*Acceptance:* a single line of text becomes a correctly classified item with type/priority/effort/person/due; works with and without an API key.

**Slice B — Recommendation engine + "Now" view.** `recommend.py` and its test suite first, then the ranked UI with per-item reasons and an available-time input.
*Acceptance:* the worked-example cases pass, including the 25-minute time-fit exclusion; empty-fit fallback renders.

**Slice C — Commitments + Delegated/Waiting + follow-up.** The relationship surfaces over `stream` + `person_id`, aging, and escalation prompts ("waiting 6 days", "delegated item overdue — follow up?"). Follow-ups append events.
*Acceptance:* an item in each stream shows on the right surface with correct age; an overdue delegation/waiting raises a follow-up prompt.

**Slice D — Weekly review + capacity.** `capacity.py` aggregations, the Friday view, and the carry-forward reset. The dormant `WeeklySummary` hook became an optional AI narrative on top of the real numbers.
*Acceptance:* counts and time breakdown match seeded event data; capacity read and delegation suggestions render; reset writes carry-forward events.

**Assembled last — the EM dashboard** composes the A–D surfaces on `/`.

**Ordering rule:** no slice A–D started until Slice 0 was merged. Parallel work on a shared, still-changing schema is how merge hell happens, so the foundation went first, serially, and the slices fanned out after it.

## Where the build differed from the plan

- **`app.py` is not thin.** The modules were split out as planned, but routes, form validation and the small pure helpers its own routes use (relationship aging and escalation) stayed in `app.py`, which is about 690 lines. A further split into blueprints wasn't needed to keep slices from colliding, so it wasn't done.
- **`log_event` lives in `db.py`; `events.py` is read-only.** This keeps the import graph acyclic (`events → db`, never the reverse). Each task write and its event append run inside `db.transaction()`, so they commit or roll back together (PR #27).
- **`task_type` was kept, not replaced.** It's still required and shown in the UI, alongside `item_type`. `item_type` is nullable, and displays fall back to `task_type` when it's unset.
- **Events cascade on item delete.** `events.item_id` is `ON DELETE CASCADE` and cs50 enables SQLite foreign keys, so deleting a task removes its history. That's a real exception to "append-only, never deleted".
- **A `settings` table was added in Slice D**, holding per-user `available_hours` and `meeting_hours_this_week`. It's created by `migrate_slice_d.py`, not `schema.sql`.
- **Timestamps are naive UTC** from SQLite's `CURRENT_TIMESTAMP`, and the code gets "now" from `clock.utc_now()` (`clock.py`, added Oct 2026) so comparisons don't drift by the server's UTC offset.
- **The context-switch penalty is implemented but not wired.** `recommend()` accepts `recent_item_type`, but `/now` doesn't pass it, so `W_SWITCH` currently has no effect.
- **The weekly AI narrative is off by default**, behind `WEEKLY_SUMMARY_AI_ENABLED=1`, even when an API key is set, because it's a real paid call.

## Workflow

The build ran as a planned, agent-assisted process:

- These two docs were committed to `docs/` so coding agents working in the repo read the same plan.
- `AGENTS.md` and `CLAUDE.md` carry the north star, so every change stays aligned to *why*, not just *what*. Both files now live at the repo root and have moved on from the originals in this plan.
- One branch and PR per slice (`slice/0-foundation`, `slice/capture`, `slice/recommend`, `slice/relationships`, `slice/review`, then `feature/dashboard`). Each slice spec asked for a plan first, then tests from the slice's acceptance criteria, then implementation to green. The recommend tests went in ahead of `recommend()`, marked xfail until Slice B landed.
- CI ran on every push and gated each merge.

Per-slice specs, including the kickoff prompt each slice started from, are in [`docs/slices/`](slices/).
