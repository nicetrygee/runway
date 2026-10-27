# Slice E1a — Guard and Rules (merge before 1b)

*Eval/observability slice. Read `docs/eval-observability.md` first. Phase 0 (`eval-phase-0`) is done: Quick Add runs through `llm.complete`, is traced, and has live/replay evals. Phase 1a makes that safe and hard to break before 1b adds depth. Five PRs, each in its own worktree:*

| PR | Worktree / branch | Depends on |
|---|---|---|
| E1a.1 Weekly summary through the interface | `../runway-e1a-summary` · `eval/e1a-summary` | this docs PR merged |
| E1a.2 Hosted guard | `../runway-e1a-guard` · `eval/e1a-guard` | this docs PR merged (can run in parallel with E1a.1) |
| E1a.3 Prompt versioning | `../runway-e1a-prompts` · `eval/e1a-prompts` | E1a.1 merged |
| E1a.4 Rules (AGENTS.md + lint/type enforcement) | `../runway-e1a-rules` · `eval/e1a-rules` | E1a.1, E1a.2, E1a.3 merged |
| E1a.5 CI replay | `../runway-e1a-ci` · `eval/e1a-ci` | this docs PR merged (can go any time; best after E1a.3) |

## Objective

After Phase 0, three things can still go wrong without anyone noticing:

- an AI call that bypasses the interface (the weekly summary still calls Anthropic directly);
- a call that leaves the machine by accident;
- a prompt that changes without a version bump, so replay quietly scores stale recordings.

Phase 1a closes each gap **with code, not just prose** (Principle 4): every AI call goes through `llm.complete` and is traced, hosted calls need an explicit lab flag, prompt changes without a version bump fail the tests, provider SDKs can only be imported in `llm.py`, and CI runs replay on every PR.

**Out of scope:** the Anthropic adapter, more eval cases, repeat runs, the dashboard, score thresholds (D5) and the real-environment split (D8). All of these are 1b or later.

## Decisions (confirmed by the owner, recorded in `eval-observability.md`)

| # | Question | Decision |
|---|---|---|
| D20 | Which model does the weekly summary use once it goes through the interface? The Anthropic adapter isn't due until 1b. | **The local model via the LM Studio adapter.** That fits Principle 1 (zero running cost), takes the only hosted call out of normal use, and doesn't pull 1b work forward. Keep the `WEEKLY_SUMMARY_AI_ENABLED` opt-in, because a local call adds several seconds to the page load. |
| D21 | Remove `anthropic` from `requirements.txt` until 1b? | **Yes.** Nothing imports it after E1a.1; 1b re-adds it, with an exact pin, alongside the adapter. |
| D22 | What does "hosted" mean to the guard, and what's the flag called? | Hosted = any adapter whose endpoint isn't loopback (`localhost`, `127.0.0.1`, `::1`). That includes `LLM_BASE_URL` pointed at a remote OpenAI-compatible host. The flag is `RUNWAY_LAB=1`. |
| D23 | How is "bump the prompt version" enforced? | A committed manifest `prompts.lock.json` maps each `PROMPT_VERSION` to a hash of its prompt template and output schema. A test fails if the current hash doesn't match the current version's entry. |
| D24 | What does CI replay fail on? | **Only missing recordings or a crash.** Score thresholds wait for D5 (1b) and the release gate (Phase 5). The report is uploaded as a CI artifact so scores can be seen. |

---

## E1a.1 — Weekly summary through the interface

**Owns / creates**
- `generate_weekly_summary(tasks)` in `classify.py` calls `llm.complete(..., schema=WeeklySummary, feature="weekly_summary", prompt_version=WEEKLY_SUMMARY_PROMPT_VERSION)` instead of `ai_client.messages.parse`.
  - It starts the system prompt with `/no_think`, like Quick Add.
  - It adds a `WEEKLY_SUMMARY_PROMPT_VERSION = "weekly_summary.v1"` constant.
  - The prompt wording and the `WeeklySummary` schema don't change.
- Tests in `tests/test_classify.py`, using the `fake_llm` fixture:
  - the summary is parsed from the fake reply;
  - a trace row is written with `feature = "weekly_summary"` and the prompt version;
  - `LLMUnavailable` propagates to the caller.

**Touches**
- `classify.py`:
  - remove `import anthropic`, `ANTHROPIC_API_KEY` and `ai_client`;
  - update the module docstring.
- `app.py`, in both `/summary` and `/review`:
  - drop the `ai_client is None` "not configured" branch;
  - catch `LLMUnavailable` and flash "Weekly AI summary needs the local model — is LM Studio running?";
  - keep the plain-list / numbers fallback exactly as it is;
  - keep the `WEEKLY_SUMMARY_AI_ENABLED` gate and the skip when there are no tasks.
- `tests/test_weekly_summary.py` and `tests/test_capture.py` (the `/review` test at line ~102), which patch `ai_client`:
  - replace the `ai_client` monkeypatches;
  - turn the "enabled but not configured" test into an "enabled but LM Studio unreachable" test;
  - keep every other behaviour asserted.
- `requirements.txt`: remove `anthropic` (D21).
- `.env.example`: remove `ANTHROPIC_API_KEY` and reword the `WEEKLY_SUMMARY_AI_ENABLED` comment to say it uses the local model.
- `AGENTS.md`: rewrite the Weekly Summary (AI) section, and update the `classify.py` entry in the module split.

**Do NOT**
- Add an Anthropic adapter or evals for the summary (Principle 2: traced, no evals).
- Change the summary prompt wording, its schema, or Quick Add.

**Acceptance**
- With LM Studio running and `WEEKLY_SUMMARY_AI_ENABLED=1`, `/summary` and `/review` show the AI recap, and each view writes a `weekly_summary` row to `traces.db` with every trace field populated.
- With LM Studio stopped, both pages flash the error and render the plain fallback. Nothing crashes.
- `grep -rn anthropic --include=*.py .` finds nothing outside `venv/`.
- ruff, mypy and the full test suite are green.

**Kickoff prompt**
> Read `docs/eval-observability.md` and `docs/slices/slice-e1a-guard-rules.md`. Implement **E1a.1 only**. Start in plan mode and show me the plan before editing. Write tests first. Keep the existing suite green. Don't change the summary prompt wording, the `WeeklySummary` schema, or anything in Quick Add.

---

## E1a.2 — Hosted guard

**Owns / creates**
- In `llm.py`:
  - Each adapter exposes a `hosted: bool`. `LMStudioAdapter.hosted` is true when the host in `LLM_BASE_URL` isn't loopback (D22). Test and replay adapters are `hosted = False`.
  - `complete()` checks the guard **before** `adapter.send`. If the adapter is hosted and `RUNWAY_LAB` isn't `"1"`, it raises `HostedCallBlocked`, a new exception that subclasses `LLMUnavailable` so existing fallbacks still catch it.
  - A blocked call is still traced: `success = 0`, with the error recorded and no raw request or response, because nothing was sent.
  - The trace gains a `hosted` column (0/1), so later dashboards can split local from hosted calls. Add it with `ALTER TABLE … ADD COLUMN` when the column is missing, so existing `traces.db` files keep working.
- Tests in `tests/test_llm.py`, with no network:
  - loopback URLs (`localhost`, `127.0.0.1`, `[::1]`) count as local and remote URLs count as hosted;
  - a hosted adapter without the flag raises `HostedCallBlocked` and never calls `send`;
  - a hosted adapter with the flag goes through;
  - blocked and allowed calls are both traced, with `hosted` set.

**Touches**
- `app.py`: log one WARNING at startup when `RUNWAY_LAB=1`, so lab mode is never on by accident without a trace in the logs.
- `evals/__main__.py`: `--live` already catches `LLMUnavailable`, so a blocked call exits 1 with the guard's message. Add a test for this.
- `.env.example`: add `RUNWAY_LAB`, with a comment saying the data in this environment must be synthetic (D6).
- `AGENTS.md`: describe the guard in the `llm.py` entry.

**Do NOT**
- Build the real-environment split, i.e. separate DBs or a `RUNWAY_ENV` (D8, before the first real note).
- Add an Anthropic adapter. 1b's adapter just sets `hosted = True` and gets the guard for free.

**Acceptance**
- With `LLM_BASE_URL=https://example.invalid/v1` and no `RUNWAY_LAB`, Quick Add flashes the unavailable error, no network request is made, and a trace row records the block.
- The same request with `RUNWAY_LAB=1` gets past the guard and fails on the network instead, which proves the guard let it through.
- The default local setup behaves exactly as it does today.
- ruff, mypy and the full test suite are green.

**Kickoff prompt**
> Read `docs/eval-observability.md` and `docs/slices/slice-e1a-guard-rules.md`. Implement **E1a.2 only**. Start in plan mode. Write the guard tests first. The guard must run before any request is sent, and a blocked call must still be traced.

---

## E1a.3 — Prompt versioning

**Owns / creates**
- Each feature's prompt becomes a module-level template constant in `classify.py`: `QUICK_ADD_SYSTEM` (with a `{today}` placeholder) and `WEEKLY_SUMMARY_SYSTEM`. The feature functions format these templates. **The rendered prompt must be byte-for-byte identical to today's**, so the existing recordings stay valid.
- `prompts.py`:
  - a registry: one entry per feature with its version constant, template and output schema;
  - `prompt_hash(template, schema)`, a sha256 of the template text plus `schema.model_json_schema()` serialised with sorted keys;
  - a small CLI, `python -m prompts lock`, that writes the hash for every current version into `prompts.lock.json`.
- `prompts.lock.json` (committed): `{"quick_add.v1": "<sha256>", "weekly_summary.v1": "<sha256>"}`. Entries for old versions stay, so the file is a history.
- `tests/test_prompts.py`:
  - every registered feature's current version has a lock entry, and its hash matches;
  - two different versions never share a hash;
  - the failure message says what to do: *"Prompt or schema for quick_add changed but PROMPT_VERSION is still quick_add.v1. Bump the version, run `python -m prompts lock`, then run `python -m evals quick_add --live` to re-record."*

**Touches**
- `AGENTS.md`: replace "bump it whenever the prompt changes" with the enforced workflow (edit, bump, lock, re-record).
- `evals/runner.py`: no behaviour change. If it needs the prompt version, it reads it through the registry.

**Do NOT**
- Change the prompt wording, bump any version, or touch `recordings.jsonl` / `cases.jsonl`.
- Hash the *rendered* prompt. "Today" changes every day; the template doesn't.

**Acceptance**
- `python -m evals quick_add --replay` gives identical per-field scores before and after this PR (proof the prompt didn't change).
- Changing one character of either template, or one field of either schema, makes `pytest tests/test_prompts.py` fail with the message above. Show this in the PR description, then revert it.
- ruff, mypy and the full test suite are green.

**Kickoff prompt**
> Read `docs/eval-observability.md` and `docs/slices/slice-e1a-guard-rules.md`. Implement **E1a.3 only**. Start in plan mode. The rendered prompts must not change by a single byte: prove it with replay before and after. Don't bump any prompt version or edit recordings or cases.

---

## E1a.4 — Rules (AGENTS.md + enforcement)

**Owns / creates**
- A new **"AI features — rules"** section near the top of `AGENTS.md`, under the North star. Every rule is either enforced by a named check, or is marked *(review only)*:
  1. Every AI call goes through `llm.complete`. Features never import a provider SDK. → ruff banned-api
  2. Every AI call is traced. → `llm.complete` always calls `tracing.record`, tested in `test_llm.py`
  3. Prompt or schema changes bump the version, update the lock and get re-recorded. → `test_prompts.py` and CI replay
  4. Hosted calls need `RUNWAY_LAB=1`, and lab data is synthetic. → the guard in `llm.py`, tested; the synthetic-data half is *(review only)*
  5. Eval cases are written in a separate session from the feature code, and the owner approves every expected answer. Never edit `cases.jsonl` in the same PR as feature or prompt code. → *(review only)*
  6. AI output is a proposal. A human approves before any write. → *(review only)* for now; enforced by tests from Phase 3
  7. Replay never falls back to a live call. → `test_eval_runner.py`
  8. Data PRs (cases, recordings) are separate from code PRs. → *(review only)*
- `pyproject.toml`:
  - Enable ruff's `TID251` (flake8-tidy-imports banned-api) to ban `openai` and `anthropic` everywhere, with a per-file ignore for `llm.py` only.
  - Add a stricter mypy override for `llm`, `tracing`, `prompts`, `classify` and `evals.*`: `disallow_untyped_defs`, `disallow_incomplete_defs`, `warn_return_any`.
- Type annotations for any functions in those modules that the stricter mypy flags. Annotations only: no behaviour changes.

**Touches**
- `tests/`: a test that runs ruff on a temp file containing `import openai` (outside `llm.py`) and checks that it reports `TID251`. This proves the ban is live and not configured in name only.

**Do NOT**
- Turn on strict mypy repo-wide, or new ruff rule sets beyond `TID` (the curated-set reasoning in `pyproject.toml` still holds).
- Change runtime behaviour.

**Acceptance**
- `ruff check .` and `mypy .` pass with the new config.
- Adding `import openai` to `classify.py` makes `ruff check .` fail with TID251. Show this in the PR description, then revert it.
- Each rule in the new AGENTS.md section names its check, or says *(review only)*.
- The full test suite is green.

**Kickoff prompt**
> Read `docs/eval-observability.md` and `docs/slices/slice-e1a-guard-rules.md`. Implement **E1a.4 only**. Start in plan mode. Add type annotations only where the stricter mypy override flags them, and change no behaviour. Every rule you write into AGENTS.md must name the check that enforces it, or be marked review only.

---

## E1a.5 — CI replay

**Owns / creates**
- A new step in `.github/workflows/ci.yml`, after the tests: `python -m evals quick_add --replay` (D24). It needs no model or secrets. Pin `LLM_MODEL=qwen3-8b` in the step's `env` so the recording key matches what the recordings were made with, not whatever the default happens to be.
- Upload the `evals/reports/*-replay.md` report as a workflow artifact (`if: always()`), so per-field scores show on every PR.

**Touches**
- `AGENTS.md`, Tests section: CI now also runs replay, and a missing recording fails the build, which usually means a prompt version was bumped without a `--live` re-record.

**Do NOT**
- Fail on scores. There are no thresholds until D5. No live runs in CI. No new secrets.

**Acceptance**
- CI is green on the PR, and the replay report is downloadable from the run.
- A throwaway commit that bumps `PROMPT_VERSION` without re-recording makes the CI replay step fail and name every case. Link that failed run in the PR description, then drop the commit.

**Kickoff prompt**
> Read `docs/eval-observability.md` and `docs/slices/slice-e1a-guard-rules.md`. Implement **E1a.5 only**. Replay in CI must need no model and no secrets, and must fail only on missing recordings or a crash, never on scores.

---

## After E1a.5 merges

1. Run `python -m evals quick_add --live` once against LM Studio, then `--replay` with LM Studio stopped, and confirm the scores still match. This proves Phase 1a changed nothing the model sees.
2. Open `/summary` with the AI recap on, and check that the `weekly_summary` trace row is there.
3. Owner writes `docs/explain-back/phase-1a.md` (no AI).
4. Run `npx ccusage@latest daily --json > docs/usage/ccusage-after-phase1a.json`.
5. Tag `eval-phase-1a`.
