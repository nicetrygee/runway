# Slice E0 — Walking Skeleton (SERIAL, merge before 1a)

*Eval/observability slice. Read `docs/eval-observability.md` first. Three PRs, each in its own worktree:*

| PR | Worktree / branch | Depends on |
|---|---|---|
| E0.1 Interface + tracing | `../runway-e0-interface` · `eval/e0-interface` | docs PR merged |
| E0.2 Cases *(data)* | `../runway-e0-cases` · `eval/e0-cases` | docs PR merged (can run in parallel with E0.1) |
| E0.3 Runner | `../runway-e0-runner` · `eval/e0-runner` | E0.1 and E0.2 merged |

## Objective

Prove the core loop end to end on Quick Add, as thinly as possible: **one live command and one replay command that produce the same per-field scores**. Everything else (environments, dashboard, CI, linting, the full synthetic world) is out of scope.

---

## E0.1 — Interface + tracing

**Owns / creates**
- `llm.py`: the one interface every AI feature calls, e.g. `complete(messages, schema=None, ...)`. It returns a normalised result (parsed output, raw text, usage, latency) and contains one adapter: **LM Studio over its OpenAI-compatible API**. Config comes from the environment (`LLM_BASE_URL`, default `http://localhost:1234/v1`; `LLM_MODEL`). It strips any `<think>…</think>` block before parsing.
- `tracing.py`: writes one row per call to **`traces.db`**, a separate SQLite file that is gitignored. The row holds: request ID, feature, model, model settings (temperature, thinking flag, etc.), prompt version, input, normalised output, raw provider request and response, input and output tokens, latency, tokens per second, success or error.
- Tests for both, with no network: a fake adapter and a temp traces DB.

**Touches**
- `classify.py`:
  - `extract_task_from_text` calls `llm.complete` instead of the Anthropic client.
  - It gains a `now` parameter (default `clock.utc_now()`, the same source of "today" it uses now) so evals can fix "today".
  - Add a `PROMPT_VERSION` constant, and `/no_think` in the system prompt.
  - The `ExtractedTask` schema is unchanged and passed as structured output.
- `app.py` `/quick-add`: if LM Studio is unreachable, flash an error and fall back to the blank form. Same graceful behaviour as today's missing-key path.
- `tests/test_quick_add.py`: update the monkeypatch targets to the new seam. Keep the same behaviours asserted.
- `.gitignore` (`traces.db`), `.env.example` (the new vars), `requirements.txt` (exact pin for the OpenAI SDK), `AGENTS.md` (Quick Add's new model path).

**Do NOT**
- Touch the weekly summary. It stays on its existing opt-in Anthropic path until Phase 1a.
- Add an Anthropic adapter, environments, a dashboard, CI changes or linting.
- Change the `ExtractedTask` fields or the review-before-save flow.

**Acceptance**
- With LM Studio running Qwen3-8B, Quick Add pre-fills the form from a note, and a row appears in `traces.db` with every field listed above populated.
- With LM Studio stopped, Quick Add flashes an error and shows the blank form. Nothing crashes.
- Full test suite green. New tests cover normalisation, think-block stripping and trace writing.

**Kickoff prompt**
> Read `docs/eval-observability.md` and `docs/slices/slice-e0-skeleton.md`. Implement **E0.1 only**. Start in plan mode and show me the plan before editing. Write tests first. Keep the existing suite green. Do not touch the weekly summary or add an Anthropic adapter.

---

## E0.2 — Cases *(data PR, no-self-grading session)*

**This PR is written in a fresh session that reads ONLY this spec and `eval-observability.md`, never `classify.py`, `llm.py` or the prompt.** The owner approves every expected answer before merge.

**Owns / creates**
- `evals/quick_add/cases.jsonl`: about 10 cases, one JSON object per line:
  ```json
  {"id": "qa-001", "today": "2026-10-15", "note": "…", "group": "normal",
   "source": "handwritten|generated",
   "expected": {"title": "…", "item_type": "…", "priority": "…",
                "effort_minutes": 30, "person": "…",
                "stream": "…", "mode": "…", "due_date": "YYYY-MM-DD"},
   "acceptable": {"priority": ["Important", "Normal"]}}
  ```
- Allowed values (they mirror the `VALID_*` lists in `app.py`, listed here so this session never has to open code):
  - `item_type`: People, Delivery, Technical, Stakeholder, Strategy, Hiring, Operational, Personal-admin
  - `priority`: Critical, Important, Normal, Delegate, Ignore
  - `effort_minutes`: 5, 30, 60, 120, 240, 480
  - `stream`: task, commitment, delegation, waiting
  - `mode`: reactive, proactive
- Use an empty string for no person or no date.
- Quick Add also returns `blast_radius`, `sprint` and `notes`. Like `title`, they are free text, so they are left out of `expected` and not scored in E0.
- `acceptable` is optional, and only for judgement fields (priority, mode, item_type) where more than one answer is reasonable.
- **Fictional people and projects only.** No real names or real work.
- The cases include at least:
  - 3 relative-date cases ("Friday", "next week", "end of month"), including "Friday" said on a Friday
  - 2 cases with no person
  - 2 commitment or waiting cases
  - 1 vague note where low confidence is expected

**Acceptance**
- Every line validates against the schema above, and a small test enforces this.
- The owner has reviewed and approved each `expected` and `acceptable` value.

**Kickoff prompt**
> Read only `docs/eval-observability.md` and `docs/slices/slice-e0-skeleton.md`. Do not open any code files. Draft E0.2: about 10 synthetic Quick Add eval cases in the schema given, plus a schema-validation test. Show me each case's expected answers for approval before committing.

---

## E0.3 — Runner

**Owns / creates**
- `evals/` command, e.g. `python -m evals quick_add --live` / `--replay`:
  - **Live:** for each case, call `extract_task_from_text(note, now=today)` through the interface. This traces as normal. Write each response to `evals/quick_add/recordings.jsonl` (committed), keyed on (model, model settings, prompt version, note, today).
  - **Replay:** read recordings, make no model call, and re-score. **If a recording is missing for a case, exit non-zero and name the case.** Never fall back to a live call.
  - **Scoring per field:**
    - exact match for factual fields (person, due_date, stream)
    - within one step for effort_minutes
    - exact match *or* in `acceptable` for priority, mode and item_type
    - title, blast_radius, sprint and notes are free text and excluded from scoring in E0
  - **Report:** writes `evals/reports/<timestamp>-<mode>.md` (gitignored) with per-field accuracy, per-case failures, and total tokens and latency for live runs.
- Unit tests (pytest) for the scorers and for replay's missing-recording failure.

**Do NOT**
- Repeat runs, the dashboard, CI integration, LLM-as-judge, or more cases.

**Acceptance (Phase 0 done-when)**
- `--live` runs all cases and writes a report and recordings.
- **With LM Studio stopped,** `--replay` produces identical per-field scores.
- Deleting one recording makes `--replay` fail loudly, naming the case.
- Full test suite green.

**Kickoff prompt**
> Read `docs/eval-observability.md` and `docs/slices/slice-e0-skeleton.md`. Implement **E0.3 only**. Start in plan mode. Write scorer and replay tests first. Don't edit `evals/quick_add/cases.jsonl`; if a case looks wrong, tell me instead.

---

## After E0.3 merges

1. Run `--live` once, commit the recordings, then run `--replay` with LM Studio stopped and compare the scores.
2. Owner writes the explain-back note (no AI).
3. Run `npx ccusage@latest daily --json > ccusage-after-phase0.json`.
4. Tag `eval-phase-0`.
