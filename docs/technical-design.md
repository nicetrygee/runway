# Runway — Technical Design (MVP differentiation)

*Status: draft v1, Sep 2026. Companion to `product-brief.md`. This doc is the source of truth every build agent works against — put it in the repo (`docs/`) so terminal agents and `CLAUDE.md` can reference it.*

## What we're building on (current state)

The MVP is in better shape than "basic" implies, and this design extends it rather than replacing it:

- **Single-file Flask app** — `app.py` (~170 lines): all routes, the `login_required` decorator, config, and a global exception handler.
- **DB** — SQLite via `cs50.SQL`, `?` placeholders, `DATABASE_URL` env (default `sqlite:///runway.db`). Tables bootstrapped from `schema.sql` with `CHECK` constraints. `tasks.user_id` is indexed.
- **`tasks` today** — `user_id, title, task_type, blast_radius, sprint, cognitive_load (1–5), due_date, notes, status`. `task_type ∈ {incident, rfc, 1on1, hiring, delivery, other}`, `status ∈ {backlog, in_progress, blocked, done}`.
- **AI already wired** — an `anthropic` client with `extract_task_from_text()` (freeform → structured `ExtractedTask` via Pydantic) and a dormant `WeeklySummary`. The API key is optional; the app runs without it.
- **Conventions to respect** — `VALID_TASK_TYPES` / `VALID_STATUSES` in `app.py` are the single source of truth for validation and must stay in sync with the `CHECK` constraints in `schema.sql` **and** with `AGENTS.md`. CSRF via Flask-WTF on every POST; `/status/<id>` reads the token from the `X-CSRFToken` header. Tests use pytest, `limiter.reset()` per test, CI on every push.

## Design principles

1. **Model direction + time, not lists.** One item table with a `stream` discriminator and an optional person — not four tables.
2. **The recommendation engine is a pure function.** No DB access, no LLM call. Inputs are plain data, output is a ranked list. This makes it unit-testable and is the single most important decision for a test-first agentic build — agents build it against a spec of cases, not against a running app.
3. **An append-only `events` log is the source of history.** Weekly-review counts, staleness/neglect, and the later "EM memory" all read from it. Never compute history by mutating rows.
4. **Break the single file into small modules.** Parallel agents cannot all edit one 170-line `app.py` without constant conflicts. The foundation slice carves it into a small package; each later slice then mostly adds its own module + template + a couple of additive routes.
5. **Keep the single-source-of-truth discipline.** Every new enum lives once in code, mirrored in `schema.sql` `CHECK`s and in `AGENTS.md`, exactly as the current `VALID_*` lists do.

## Data model changes

### New table: `people`
Lightweight — not an HR record. The join point for commitments, delegation, waiting-on, and the later people-attention view.

```sql
CREATE TABLE people (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    role        TEXT,            -- e.g. "Product", "CTO", "Engineer", free text
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX idx_people_user_id ON people(user_id);
```

### New table: `events` (append-only)
Every meaningful change appends a row. Nothing here is ever updated or deleted.

```sql
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    item_id     INTEGER,             -- nullable: some events aren't item-scoped
    person_id   INTEGER,             -- nullable
    event_type  TEXT NOT NULL,       -- created | status_changed | touched | completed
                                     -- | delegated | followed_up | note_added | interaction
    payload     TEXT,                -- JSON blob, event-specific
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id)   REFERENCES users(id),
    FOREIGN KEY (item_id)   REFERENCES items(id),
    FOREIGN KEY (person_id) REFERENCES people(id)
);
CREATE INDEX idx_events_user_id ON events(user_id);
CREATE INDEX idx_events_item_id ON events(item_id);
```

### Evolve `tasks` → `items`
Rename and extend. (Rename is optional — keeping the table named `tasks` is fine — but "items" reflects that it's no longer just my to-dos.)

New / changed columns:

| Column | Values | Purpose |
|---|---|---|
| `stream` | `task` \| `commitment` \| `delegation` \| `waiting` | direction of the item (see brief) |
| `item_type` | People, Delivery, Technical, Stakeholder, Strategy, Hiring, Operational, Personal-admin | new taxonomy (replaces `task_type`) |
| `priority` | Critical \| Important \| Normal \| Delegate \| Ignore | triage priority |
| `effort_minutes` | integer (canonical) | from 5m/30m/1h/2h/half-day/multi-day → 5/30/60/120/240/480 |
| `mode` | `reactive` \| `proactive` | strategic-time protection + weekly balance |
| `person_id` | FK → people, nullable | counterparty (to-whom / delegated-to / waited-on) |
| `is_blocking` | integer 0/N | how many people/things this is holding up (bottleneck signal) |
| `last_touched_at` | TEXT timestamp | staleness/neglect + follow-up aging; updated on any change |
| `due_date`, `status`, `notes`, `cognitive_load` | (kept) | `status` unchanged; `cognitive_load` optional second axis |

**Effort mapping** (store minutes, present labels): `5m→5, 30m→30, 1h→60, 2h→120, half-day→240, multi-day→480`. Multi-day just means "won't fit any single window" for time-fit purposes.

**Migration from existing rows:**
- `task_type` → `item_type`: `incident→Operational`, `rfc→Technical`, `1on1→People`, `hiring→Hiring`, `delivery→Delivery`, `other→Operational`. Keep the old column for one release if you want a safety net.
- `status` unchanged.
- `stream` defaults to `task` for all existing rows.
- `priority` defaults to `Normal`; `mode` defaults to `reactive`.
- `effort_minutes` from `cognitive_load` as a rough seed (1→30, 2→60, 3→120, 4→240, 5→480) — it's only a backfill; users re-estimate as they touch items.
- `last_touched_at` backfilled to the row's creation time (or `datetime('now')` if none is recorded).

Follow the existing `CREATE INDEX IF NOT EXISTS` pattern so the migration is safe to re-run against a populated `runway.db`.

### Enum single-source-of-truth
Add `VALID_STREAMS`, `VALID_ITEM_TYPES`, `VALID_PRIORITIES`, `VALID_MODES` alongside the existing `VALID_STATUSES`, mirror each in `schema.sql` `CHECK` constraints, and list them in `AGENTS.md` — same discipline as today.

## Module structure (refactor as part of the foundation)

Split the single file so parallel agents touch different files:

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

Keep cs50 conventions and the CSRF/session/rate-limit wiring exactly as-is. This refactor is behaviour-preserving — the test suite must stay green through it.

## The recommendation engine (feature 2) — the crown jewel

A pure function. This is what agents build first, against cases, before any UI.

```python
# recommend.py  — no DB, no network, no Flask
def recommend(items: list[Item], available_minutes: int, now: datetime,
              limit: int = 5) -> list[Recommendation]:
    """Rank the EM's own actionable work for right now.
    Only `stream == 'task'` and `commitment` items I own are candidates
    (delegation/waiting are someone else's court). Returns top `limit`,
    each with a human-readable reason."""
```

**Scoring** — a weighted sum of normalised signals, then a hard time-fit filter:

```
score(item) =
      W_PRIORITY  * priority_weight(item.priority)      # Critical≫Important>Normal; Ignore=excluded
    + W_DUE       * urgency(item.due_date, now)          # rises as the deadline nears; overdue = max
    + W_BLOCK     * blocking(item.is_blocking)           # bottleneck: unblock others first
    + W_NEGLECT   * staleness(item.last_touched_at, now) # nudge quiet high-stakes items up
    - W_SWITCH    * context_switch_penalty(item, recent) # small penalty for thrashing item_type/mode
```

**Time-fit is a hard rule, not a weight:** filter out any candidate whose `effort_minutes > available_minutes` *before* ranking. If nothing fits, return the smallest available items with a "you only have N minutes — here's what fits" note rather than an empty screen. This is the "25 minutes before a meeting shouldn't suggest a 2-hour doc" behaviour, and it's the first thing the tests assert.

Weights live as named constants at the top of `recommend.py` so they're tunable in one place. Start them by hand; a later slice can learn them.

**Worked example** (the doc's case): 25 minutes available →
1. *Follow up with Product on migration scope* — 10 min · blocking 3 people  ← high `is_blocking`, fits easily
2. *Review hiring feedback* — 20 min · Important  ← fits
3. *Prepare exec update* — 45 min · due tomorrow  ← **excluded from "now"** (doesn't fit) but shown under "when you have 45 min"

Keep it deterministic and LLM-free for the MVP. The LLM's job is capture/classification (slice A) and, much later, EM-memory Q&A — not ranking.

## Capacity + weekly review (feature 5)

Reads from `events` + `items`, no calendar:
- **Counts** — completed / carried-forward / delegated / waiting, from event history over the week.
- **Time breakdown** — sum `effort_minutes` grouped by `item_type` and by `mode`, plus one manually entered "meeting hours this week" figure. Strategic-time % = proactive effort ÷ total.
- **Capacity read** — `available_hours` (a user setting, default 38) vs sum of committed `effort_minutes` for next week → "116% committed" / "~24h available".
- **Delegation suggestions** — items with `priority == Delegate`, or high-effort low-priority items, surfaced as "3 items could be delegated".
- **Reset prompt** — "what do you want to carry into next week?" writes carry-forward events.

## Build order — slices

**Slice 0 — Foundation (SERIAL, must merge before anything else).**
Schema migration (`people`, `events`, evolved `items`, new enums + `CHECK`s), the `app.py` → package refactor, `events.log_event` helper, `last_touched_at` auto-update on writes, backfill, tests kept green, and the `AGENTS.md`/`CLAUDE.md` north-star (appendix below).
*Acceptance:* full suite green; existing tasks migrated and visible; new columns populated with sane defaults; enums validated server-side and by `CHECK`.

Then **parallel** — one git worktree + branch + agent each, because they touch mostly separate modules/templates and only append routes:

**Slice A — Capture & classify (Work Inbox).** Fast add + extend `classify.py` from the existing `extract_task_from_text` to the new taxonomy, detect a `person`, and flag `commitment`/`waiting`. Graceful no-AI fallback (manual fields), matching today's optional-key behaviour.
*Acceptance:* a single line of text becomes a correctly classified item with type/priority/effort/person/due; works with and without an API key.

**Slice B — Recommendation engine + "Now" view.** `recommend.py` + its dense test suite first, then the ranked UI with per-item reasons and an available-time input.
*Acceptance:* the worked-example cases pass, including the 25-minute time-fit exclusion; empty-fit fallback renders.

**Slice C — Commitments + Delegated/Waiting + follow-up.** The relationship surfaces over `stream` + `person_id`, aging, and escalation prompts ("waiting 6 days", "delegated item overdue — follow up?"). Follow-ups append events.
*Acceptance:* an item in each stream shows on the right surface with correct age; an overdue delegation/waiting raises a follow-up prompt.

**Slice D — Weekly review + capacity.** `capacity.py` aggregations + the Friday view + carry-forward reset. Reuse/retire the dormant `WeeklySummary` hook here (optional AI narrative on top of the real numbers).
*Acceptance:* counts and time breakdown match seeded event data; capacity read and delegation suggestions render; reset writes carry-forward events.

**Assemble last — the EM dashboard** is the composition of A–D surfaces on one screen; build it once the pieces exist.

**The rule that saves your evening:** don't start A–D until Slice 0 is merged. Parallel agents on a shared, still-changing schema is how you get merge hell. Foundation first, serial; then fan out.

## Running this in your tools (the agentic workflow)

- **This Claude project = the planning brain.** These two docs live here and travel across every session and surface. The GitHub sync means planning here always sees current code.
- **Also commit these docs into the repo `docs/`.** Your Warp/terminal agents read the *repo*, not the claude.ai project — so the repo copy is what actually shares context with them. Re-sync updates them here.
- **`AGENTS.md` + `CLAUDE.md` carry the north star** (appendix) so every agent stays aligned to *why*, not just *what*. You already keep `AGENTS.md`; add a short `CLAUDE.md` that points at it (Claude Code reads `CLAUDE.md` by default).
- **One git worktree + branch per slice**, each in its own Warp pane: `git worktree add ../runway-slice-b slice/recommend`. Agents can't collide when they're in separate working directories.
- **Plan mode first, tests first.** Have each agent produce a plan you approve, then write tests from the slice's acceptance criteria, then implement to green.
- **PR + CI + a Claude review pass** before merge — you already have CI on every push.

**Per-agent kickoff prompt template:**
> Read `docs/product-brief.md` and `docs/technical-design.md`. You are implementing **Slice B — Recommendation engine + Now view**. Its acceptance criteria are in the design doc. Work only in `recommend.py`, its test file, the Now-view route and template. Start in plan mode; write the tests from the worked-example cases first; keep the whole suite green. Do not touch the schema.

## Appendix A — paste-ready `AGENTS.md` north-star block

```markdown
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
```

## Appendix B — `CLAUDE.md`

```markdown
# Runway
See AGENTS.md for architecture, conventions, and the product north star.
Before implementing anything, read docs/product-brief.md and
docs/technical-design.md. Respect the slice boundaries defined there;
Slice 0 (foundation) must be merged before slices A–D begin.
```
