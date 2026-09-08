# Slice C — Commitments + Delegated / Waiting + follow-up

*Parallel slice. Worktree `../runway-slice-c`, branch `slice/relationships`. Depends on Slice 0 (streams, `people`, `events`; merged). Read `docs/product-brief.md` and `docs/technical-design.md` first.*

## Objective

The relationship surfaces — my commitments, what I'm waiting on from others, and what I've delegated — with aging and follow-up escalation. MVP features #3 and #4 (and the seed of #6). This is the slice of EM work ordinary task managers don't model.

## Owns / creates (your files)

- **Stream views**: `/commitments` (`stream=commitment`, grouped by the person it's owed to), `/waiting` (`stream=waiting`, aged), `/delegated` (`stream=delegation`, aged). Separate pages or one combined view with sections — your call; keep it simple. New templates.
- **Aging + escalation**: compute age from `last_touched_at` / `due_date` and surface it — "waiting 6 days for Product to confirm scope", "James' investigation was due yesterday — follow up?".
- **Follow-up action**: a control that logs a `followed_up` event and bumps `last_touched_at` (reuse `db.set_*` + `log_event`).
- **Person grouping**: show items grouped by person, and a minimal per-person view ("James has 4 open items from you").
- **`db.py`**: stream-filtered read queries; the follow-up write. **`events.py`**: any read helpers you need for aging.

## Touches (shared — coordinate, keep additive)

`app.py` (new routes), `db.py` (stream queries + follow-up write), `templates/*` (new pages) + `templates/layout.html` (nav links), the `people` table (read/group; person create is Slice A's job at capture, but a manual "add person" form here is fine).

## Do NOT

Implement `recommend()` (B). Build capture/classification (A) — you consume the `stream`/`person_id` that A sets, and may let the user change an existing item's stream. Weekly aggregates (D). Touch the schema.

## Acceptance criteria

- Items with `stream` commitment/delegation/waiting appear on the correct surface with a correct age.
- An overdue delegation or waiting item shows a follow-up prompt; acting on it writes a `followed_up` event and bumps `last_touched_at`.
- Grouping by person works. New tests: stream filtering, age calculation, follow-up event. Existing suite stays green.

## Kickoff prompt

> Read `docs/product-brief.md`, `docs/technical-design.md`, and `docs/slices/slice-c-relationships.md`. Implement **Slice C — Commitments + Delegated/Waiting + follow-up** exactly as that spec defines it. Add the stream views, aging/escalation, a follow-up action that writes a `followed_up` event, and person grouping — using stream-filtered queries in `db.py` and read helpers in `events.py`. Do not implement `recommend()`, capture/classification, or the weekly review, and do not change the schema. Start in plan mode and show me your plan first. Keep the suite green and add tests for stream filtering, aging, and the follow-up event.
