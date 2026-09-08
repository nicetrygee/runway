# Slice B — Recommendation engine + "Now" view

*Parallel slice. Worktree `../runway-slice-b`, branch `slice/recommend`. Depends on Slice 0 (merged). This is the killer feature — the thing that makes Runway not-a-task-list. Read `docs/product-brief.md` and `docs/technical-design.md` first.*

## Objective

Implement `recommend()` to green, then surface it as the "What should I do now?" page: a short ranked list of next actions with a reason on each, that fits the time the EM actually has.

## Owns / creates (your files)

- **`recommend.py`** (yours alone — no collision): implement `recommend()` and the signal helpers (`priority_weight`, `urgency`, `blocking`, `staleness`, `context_switch_penalty`) so all 9 tests in `tests/test_recommend.py` pass. Honour the contract in the module docstring: candidate filter (`stream in {task, commitment}`, `status != done`, `priority != Ignore`), **hard time-fit filter**, the nothing-fits fallback (smallest items, `fits=False`, explanatory note), and a human `reason` on every recommendation.
- **`/now` route + `templates/now.html`**: an available-minutes input (default ~60, or "minutes until your next meeting"), the ranked list with each item's reason, and an "when you have more time" overflow section for items that didn't fit.
- **Row→Item mapping** in the route/`db.py` layer (keeps `recommend.py` pure): a `db` read helper returning the user's candidate rows, mapped into `recommend.Item`.

## Touches (shared — coordinate, keep additive)

`app.py` (a new `/now` route), `db.py` (one read query), `templates/now.html` + `templates/layout.html` ("Now" nav link).

## Do NOT

Change capture/classify (Slice A). Add the commitments/waiting views (Slice C). Touch the schema. Modify other slices' routes.

## Acceptance criteria

- `pytest tests/test_recommend.py` → **9 passed**.
- `/now` renders a ranked list; with a small available-time value the large items drop out and the fallback note appears; every row shows its reason string.
- A route test for `/now`. Existing suite stays green.

## Kickoff prompt

> Read `docs/product-brief.md`, `docs/technical-design.md`, and `docs/slices/slice-b-recommend.md`. Implement **Slice B — Recommendation engine + Now view** exactly as that spec defines it. First make all 9 tests in `tests/test_recommend.py` pass by implementing `recommend()` and its signal helpers in `recommend.py` — keep that module pure (no DB/Flask/LLM imports). Then add the `/now` route, its template, a nav link, and a `db.py` read helper that maps rows to `recommend.Item`. Do not change capture, the schema, or other slices' routes. Start in plan mode and show me your plan first. Keep the whole suite green and add a `/now` route test.
