# Runway — AI Evaluation & Observability Design

*Status: v3, Oct 2026. Companion to `product-brief.md` and `technical-design.md`. This is the source of truth for every eval/observability slice. Slice specs live in `docs/slices/slice-e*.md`.*

## Why this exists

Runway is the vehicle for learning how to build an evaluation and observability layer for AI features, following the loop in Eferhire, "The Missing Layer in AI Agents" (HackerNoon, 28 Aug 2026): **trace → replay → score → regression-test → analyse failures**. The aim is to understand it well enough to apply to a real production system.

**Done means:**
- By the end of Phase 2, the full loop has run at least once on a real failure: the failure becomes an eval case, a fix is made, and the suite goes green.
- The owner can sketch a production design in about 20 minutes, including the trade-offs.

Everything after Phase 2 is optional.

## Principles

1. **Zero running cost by default.** Local models in LM Studio. Anthropic only for deliberate drift runs, with a capped budget.
2. **Every AI call is traced,** including the weekly summary (traced, no evals).
3. **One interface for every feature, with a native adapter per provider:** OpenAI-compatible for LM Studio, the Anthropic SDK for Claude. Features never call a provider SDK directly.
4. **Check with code first.** Use a model as judge only once its grades have been checked against hand grades.
5. **A human approves before any write.**
6. **All lab data is synthetic.** Hosted calls require an explicit lab flag. **The full real-environment split must exist before the first real note is entered.**
7. **Each phase adds one new kind of risk.**
8. **Hand-built observability:** traces in a separate SQLite file and a dashboard page inside Runway, using OpenTelemetry naming conventions for AI traces.
9. **Built for a 16GB machine:** memory and context are budgets, and they're measured.
10. **No self-grading.** Eval cases are written in a separate agent session from the feature, from the spec only. A human approves every expected answer.

## Decisions

| # | Decision | Status |
|---|---|---|
| D1 | Small capped Anthropic budget for drift runs: prepaid credit, auto-reload off, own workspace and key | Decided |
| D2 | Final local model, quantisation and context length. Phase 0 uses Qwen3-8B (4-bit) provisionally, with thinking off via `/no_think` in the system prompt | Open (Phase 1b) |
| D3 | Judge model: a different family from the one being judged, run as a separate pass | Open |
| D4 | Repeat runs per case: start at 3 | Open |
| D5 | Release-gate thresholds per scoring dimension | Open |
| D6 | All lab data is synthetic. `runway.db` confirmed to hold no real data | Decided |
| D7 | Budget cap amount, sized from measured tokens per suite run | Open |
| D8 | Real-environment split deferred. Cheap guard now; full split before the first real note | Decided |
| D9 | Eval runner is its own command (live / replay / report). Pytest only for unit tests | Decided |
| D10 | Runway's slice-spec approach, not Spec Kit | Decided |
| D13 | Walking skeleton first | Decided |
| D14 | One interface with a native adapter per provider | Decided |
| D15 | A labelling guide per field. Exact match for factual fields; within one step, or a set of acceptable answers, for judgement fields | Decided |
| D16 | Each eval case carries a fixed "today" | Decided |
| D17 | Replay matches on (model, **model settings**, prompt version, input, today). It proves code and scorer changes only; prompt, model or settings changes need a live run. It fails loudly when there's no recording | Decided |
| D18 | Traces store the common shape plus raw provider request and response, in a separate SQLite file | Decided |
| D19 | Phases 2–5 each start with their own grilling round before their spec is written | Decided |
| D20 | Weekly summary runs on the local model through `llm.complete`, still opt-in via `WEEKLY_SUMMARY_AI_ENABLED` | Decided |
| D21 | `anthropic` is removed from `requirements.txt` until the Phase 1b adapter re-adds it | Decided |
| D22 | Hosted = any adapter whose endpoint isn't loopback; hosted calls need `RUNWAY_LAB=1`, checked before sending, and blocked calls are traced | Decided |
| D23 | Prompt version bumps are enforced by `prompts.lock.json` (hash of template + output schema per version) and a test | Decided |
| D24 | CI replay fails only on missing recordings or a crash, never on scores (until D5) | Decided |

## Architecture

```
feature (classify.py, later agents)
   │  complete(messages, schema=…, tools=…)  ← the one interface
   ▼
llm.py ── adapter: LM Studio (OpenAI-compatible)   [Phase 0]
       └─ adapter: Anthropic SDK                   [Phase 1b]
   │  every call → tracing.py → traces.db (separate SQLite file)
   ▼
evals/  ── cases (synthetic, committed) ── recordings (committed)
        └─ runner: live | replay → per-field scores → report
```

- **Trace record (minimum):** request ID, feature, model, model settings, prompt version, input, normalised output, raw provider request and response, input and output tokens, latency, tokens per second, success or error.
- **Recordings vs traces:** `traces.db` is local and gitignored. Live eval runs also write **recordings** to a committed file, so replay works anywhere, including CI later, with no model.
- **Efficiency on 16GB:**
  - one model in memory at a time
  - cases run one after another
  - stable content first in prompts (system prompt, then schema and tools, then variable input)
  - structured output constrained by a schema
  - a context budget per feature
  - Runway runs natively, not in Docker, during live evals

## Phases

Every phase ends in a tagged release (`eval-phase-N`), an explain-back note written by the owner, and a write-up.

| Phase | Scope |
|---|---|
| **0 Skeleton** | Minimal interface and LM Studio adapter; traces file; about 10 synthetic Quick Add cases; per-field scoring; live and replay commands. See `slice-e0-skeleton.md` |
| **1a Guard and rules** | Hosted guard and AGENTS.md rules; prompt versioning; linting and types; CI replay; weekly summary through the interface and traced. See `slice-e1a-guard-rules.md` |
| **1b Eval depth** | Labelling-guide questionnaire and guide; full eval set (normal, failure, adversarial); synthetic world; repeat-run baseline; dashboard v1 with efficiency metrics; final model choice; Anthropic adapter and first drift report |
| **2 "What next?"** | Read-only tool-using agent; `recommend.py` stays pure and is called as a tool; the engine's ranking is the correct answer; step tracing and a step cap; failure investigation view |
| **3 "Process my notes"** | Propose-then-approve writes; limited tools; idempotency; injection set; approve, edit and reject decisions as a production signal |
| **4 "Ask Runway"** | Multi-turn sessions; grounding through cited item IDs; "I don't know" cases; local judge with calibration (the "EM-memory Q&A" from `technical-design.md`) |
| **5 Close the loop** | Near-miss flagging; traces turned into synthetic eval cases; release gate in CI; full drift run; lightweight platform comparison; retrospective |

Phases 2–5 each begin with a grilling round before their slice spec is written.

## Engineering practice

- One worktree, one branch, one PR. Mostly serial.
- Each PR has one concern and its own done-when evidence. Data PRs (cases, recordings) are separate from code PRs.
- Every PR updates the docs it makes stale, especially `AGENTS.md`.
- Each phase is a GitHub milestone, each PR an issue. Releases are tagged per phase.
- Every AI feature sits behind a flag. ADRs go in `docs/decisions/`.
- Review order: CI, then an agent review pass, then the owner merges.
- Build usage is logged per phase with `ccusage`.
