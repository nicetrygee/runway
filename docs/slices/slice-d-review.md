# Slice D — Weekly review + capacity

*Parallel slice. Worktree `../runway-slice-d`, branch `slice/review`. Depends on Slice 0 (`events`; merged). MVP feature #5 — closes the loop. Read `docs/product-brief.md` and `docs/technical-design.md` first.*

## Objective

The Friday ritual: counts, a time breakdown, a capacity read, delegation suggestions, and a carry-forward reset. Turns the app from a recorder into something that helps the EM manage their own capacity.

## Owns / creates (your files)

- **`capacity.py`** (yours alone — low collision): aggregations over `events` + `tasks` — completed / carried-forward / delegated / waiting counts for the week; `effort_minutes` sums grouped by `item_type` and by `mode`; strategic-time % (`proactive` effort ÷ total); a capacity read (`available_hours` vs committed effort for next week → "116% committed"); delegation suggestions (items with `priority = Delegate`, or high-effort/low-priority).
- **Settings** — the one schema touch in the parallel phase, and it's isolated to this slice: a small additive `settings(user_id, key, value)` table for `available_hours` (default 38) and "meeting hours this week". Ship it as `migrate_slice_d.py` in the same idempotent, additive style as `migrate_slice0.py`. Because it changes the schema, **merge this slice last** (see the run guide).
- **`/review` route + `templates/review.html`**: the Friday view plus a "carry into next week" action that writes carry-forward `events`.
- **Optional**: revive `generate_weekly_summary` (already in `classify.py`) as an AI narrative on top of the real numbers, gated by `WEEKLY_SUMMARY_AI_ENABLED`.

## Touches (shared — coordinate, keep additive)

`app.py` (a new `/review` route), `capacity.py` (yours), `events.py` (reads), `classify.py` (the existing summary function only), `db.py` (aggregate queries), `templates/review.html` + `templates/layout.html` (nav link), and its own `migrate_slice_d.py`.

## Do NOT

Implement `recommend()` (B), capture (A), or the relationship surfaces (C). Modify Slice 0's schema files — add your settings table via your own migration.

## Acceptance criteria

- `/review` shows counts, the time breakdown, and a capacity read that match seeded event/effort data; delegation suggestions render; the carry-forward action writes `events`.
- `migrate_slice_d.py` is idempotent (safe to re-run) and `settings` persist.
- New tests: seed events/items and assert the aggregate numbers; settings round-trip. Existing suite stays green.

## Kickoff prompt

> Read `docs/product-brief.md`, `docs/technical-design.md`, and `docs/slices/slice-d-review.md`. Implement **Slice D — Weekly review + capacity** exactly as that spec defines it. Put the aggregations in `capacity.py`, add a `settings(user_id, key, value)` table via an idempotent `migrate_slice_d.py`, and build the `/review` route + template with a carry-forward action that writes events. Optionally revive `generate_weekly_summary` behind `WEEKLY_SUMMARY_AI_ENABLED`. Do not implement `recommend()`, capture, or the relationship views, and do not edit Slice 0's schema files. Start in plan mode and show me your plan first. Keep the suite green and add aggregation + settings tests.
