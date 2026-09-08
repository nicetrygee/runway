# Slice A — Capture & classify (Unified Work Inbox)

*Parallel slice. Worktree `../runway-slice-a`, branch `slice/capture`. Depends on Slice 0 (merged to `main`). Read `docs/product-brief.md` and `docs/technical-design.md` first.*

## Objective

Make capturing work effortless: one line of freeform text becomes a correctly classified item. MVP feature #1 — if capture isn't frictionless, nothing else in the product matters. This slice extends the AI extraction that already exists (`extract_task_from_text`) to the new EM taxonomy and to detecting a person and the item's stream.

## Owns / creates (your files)

- **`classify.py`** (yours alone — low collision): extend `ExtractedTask` to the new fields — `item_type` (the 8-type taxonomy), `priority`, `effort_minutes` (map the labels 5m/30m/1h/2h/half-day/multi-day → 5/30/60/120/240/480), `person` (a name string), `stream` (task/commitment/delegation/waiting), `mode` (reactive/proactive). Rework the system prompt so it classifies against the EM taxonomy and spots whether the note is a promise (`commitment`), a hand-off (`delegation`), or a block (`waiting`), plus any person named.
- **Capture UI**: update the Add / Quick-add flow to the new fields, keeping the existing "review the extracted values, then save" pattern. Reworked `templates/add.html` (or a new `inbox.html`).
- **People upsert on capture**: when extraction yields a person name, find-or-create in `people` via a new `db.get_or_create_person(user_id, name)`.

## Touches (shared — coordinate, keep additive)

`app.py` (the `add`/`quick-add` routes), `db.py` (`insert_task` writing the new columns + `person_id`, plus the person helper), `templates/add.html`, `templates/layout.html` (add an "Inbox" nav link). Use the `VALID_*` enums Slice 0 defined — don't redefine them.

## Do NOT

Implement `recommend()` (Slice B). Build the commitments/waiting/delegated **views** (Slice C owns those surfaces — you only set `stream`/`person_id` at capture time). Weekly review (Slice D). Change the schema.

## Acceptance criteria

- "Sarah asked me to review the Q4 hiring plan" extracts a sensible `item_type`, a person "Sarah", an `effort_minutes`, a `priority`, and ideally the right `stream` — with the user able to correct any field before saving.
- Works **with and without** `ANTHROPIC_API_KEY` (manual-entry fallback, matching today's behaviour).
- Capturing an item that names a person creates or links a `people` row and sets `person_id`.
- New tests: extraction (mock the AI call), person upsert, insert with the new fields. Existing suite stays green.

## Kickoff prompt

> Read `docs/product-brief.md`, `docs/technical-design.md`, and `docs/slices/slice-a-capture.md`. Implement **Slice A — Capture & classify** exactly as that spec defines it. Work in `classify.py`, the capture route in `app.py`, the `insert_task`/person helper in `db.py`, and `templates/add.html` (+ one nav link in `layout.html`). Do not implement `recommend()`, the relationship views, or the weekly review, and do not change the schema. Start in plan mode and show me your plan first. Keep the existing test suite green and add tests for extraction, person upsert, and the new-field insert. Mock the Anthropic call in tests. Note: verify the model id in `classify.py` (currently `claude-sonnet-5`) against the current API model list before relying on it.
