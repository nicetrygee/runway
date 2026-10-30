# ADR 0001 — Local model for Quick Add

- **Status:** Accepted. All three candidates measured, including peak memory.
- **Date:** 2026-10-30
- **Decides:** D2 (model, quantisation, context length). Feeds D4, D5 and D7 in `docs/eval-observability.md`.
- **Slice:** E1b.6 (`docs/slices/slice-e1b-eval-depth.md`)

## Context

Phase 0 runs Quick Add on Qwen3-8B (4-bit) provisionally, with thinking off via `/no_think` (D2, D30).
E1b.6 picks the default local model from three candidates: Qwen3-8B Q4, Qwen3-4B, and one other
family at 7–9B (Llama 3.1 8B Instruct). D2 sets the order of criteria:

1. **Peak memory** must leave room for Runway and a browser on a 16 GB machine. A candidate that
   fails this is out, whatever its accuracy.
2. **Accuracy**, per field, on the full Quick Add set.
3. **p50 latency.**

## Method

- **Case set:** the full E1b.3 Quick Add set, 41 cases (20 normal, 6 edge, 8 failure,
  7 adversarial), prompt `quick_add.v1`, unchanged across models. No per-model prompt tuning.
- **Command:** `python -m evals quick_add --live --repeat 3 --model <id>`, one model loaded at a
  time, cases run one at a time.
- **Settings:** temperature 0, `max_tokens` 1024, thinking off. LM Studio, 4096-token context,
  4 parallel slots (the runner sends one request at a time).
- **`/no_think` (D30):** the LM Studio adapter strips it for non-Qwen models. Checked in
  `traces.db`: none of the 123 Llama requests contain it, starting with the first (trace 247).
- **Recordings:** committed in `evals/quick_add/recordings.jsonl`, 3 runs × 41 cases per model.
  Replay reproduces every number below without LM Studio.
- **Reports:** `evals/reports/20261030T090224Z-live.*` (Qwen3-8B),
  `20261030T083952Z-live.*` (Qwen3-4B) and `20261030T092800Z-live.*` (Llama 3.1 8B).
  Reports are not committed.

## Results

### Footprint

| | Qwen3-8B | Qwen3-4B | Llama 3.1 8B |
|---|---|---|---|
| LM Studio ID | `qwen3-8b` | `qwen/qwen3-4b` | `meta-llama-3.1-8b-instruct` |
| Quantisation | 4-bit | 4-bit | 4-bit |
| Context loaded / max | 4096 / 40,960 | 4096 / 40,960 | 4096 / 131,072 |
| Size on disk | 4.62 GB | 2.28 GB | 4.53 GB |
| Loaded size (LM Studio) | 4.31 GiB | 2.12 GiB | 4.22 GiB |
| Model memory (wired over baseline, peak) | 5.2 GB | 3.3 GB | 5.2 GB |
| System memory used over baseline (median / peak) | 5.9 / 6.6 GB | 4.6 / 6.8 GB | 5.1 / 5.6 GB |
| System memory used, peak (of 16 GB) | 12.8 GB | 12.7 GB | 13.3 GB |
| Lowest free-memory level (macOS) | 41% | 60% | 40% |
| Swap growth during the run | none | none | none |

**How memory was measured.** Per-process figures in Activity Monitor don't show the model: the
weights are memory-mapped and wired for the GPU, so no LM Studio process shows more than about
150 MB. Instead, a script sampled system-wide `vm_stat`, `vm.swapusage` and
`kern.memorystatus_level` every second, for each model in turn:

1. Unload all models, then take 15 s of baseline.
2. Load the model (4096 context).
3. Send the 41 Quick Add notes through `classify`, one at a time. No recordings were written, and
   traces went to a throwaway DB.

"Used" is app memory + wired + compressed, as Activity Monitor computes it. Wired over baseline is
the cleanest measure of the model itself. "Used" also moves with other apps: the 4B's used-memory
peak is about the same as the 8B's, though its wired memory is 2 GB lower. Before the runs, other
apps were already using about 6–7.5 GB.

### Accuracy (3 runs each)

All three models agreed with themselves across runs on every field except one: the 4B flipped
`stream` on one case. So mean and worst run are equal except for the 4B's `stream`
(mean 74%, worst 73%, flip rate 2%). Every other flip rate is 0%.

| Field | Qwen3-8B | Qwen3-4B | Llama 3.1 8B |
|---|---|---|---|
| item_type | 46% | 32% | **56%** |
| priority | **71%** | 63% | 56% |
| effort_minutes | **88%** | **88%** | 56% |
| person | **98%** | 78% | 88% |
| stream | **76%** | 74% (worst 73%) | 68% |
| mode | 54% | 66% | **68%** |
| due_date | **63%** | 37% | 39% |
| Cases with any wrong field | 38/41 | 41/41 | 37/41 |

### Accuracy by case group (run 0, correct cases)

| Group | Model | item_type | priority | effort | person | stream | mode | due_date |
|---|---|---|---|---|---|---|---|---|
| normal (20) | Qwen3-8B | 8 | 17 | 16 | 20 | 18 | 13 | 13 |
| | Qwen3-4B | 4 | 14 | 18 | 18 | 18 | 15 | 6 |
| | Llama 3.1 8B | 10 | 16 | 11 | 20 | 16 | 15 | 8 |
| edge (6) | Qwen3-8B | 3 | 6 | 5 | 6 | 5 | 4 | 4 |
| | Qwen3-4B | 4 | 6 | 4 | 5 | 3 | 4 | 2 |
| | Llama 3.1 8B | 4 | 6 | 3 | 5 | 3 | 4 | 2 |
| failure (8) | Qwen3-8B | 3 | 5 | 8 | 8 | 4 | 2 | 6 |
| | Qwen3-4B | 2 | 5 | 7 | 5 | 5 | 4 | 5 |
| | Llama 3.1 8B | 4 | 1 | 4 | 5 | 3 | 4 | 3 |
| adversarial (7) | Qwen3-8B | 5 | 1 | 7 | 6 | 4 | 3 | 3 |
| | Qwen3-4B | 3 | 1 | 7 | 4 | 5 | 4 | 2 |
| | Llama 3.1 8B | 5 | 0 | 5 | 6 | 6 | 5 | 3 |

### Efficiency

| | Qwen3-8B | Qwen3-4B | Llama 3.1 8B |
|---|---|---|---|
| p50 latency (runs 0 / 1 / 2) | 8.6 / 10.1 / 9.8 s | 3.6 / 4.5 / 5.0 s | 7.3 / 9.3 / 9.9 s |
| p95 latency (runs 0 / 1 / 2) | 10.8 / 11.7 / 11.1 s | 4.4 / 6.2 / 6.7 s | 10.3 / 12.0 / 12.4 s |
| Output tokens/s | 9.3–11.5 | 18.4–25.3 | 8.1–11.2 |
| Tokens per suite run (41 cases) | 14,983 in / 3,542 out | 14,983 in / ~3,638 out | 15,180 in / 3,099 out |
| Mean per call | 365 in / 86 out | 365 in / 89 out | 370 in / 76 out |

All three stay well inside the Quick Add context budget of 1k in / 256 out (D29).

## Observations

- **All three are deterministic at temperature 0.** One flip in 369 case runs (the 4B's `stream`
  on qa-015). Qwen3-8B's three fresh runs also match its earlier single E1b.3 run exactly. This
  supports D4's fallback to 1 run for day-to-day live runs.
- **Qwen3-8B leads on five of seven fields**, including the two factual fields users notice most:
  `due_date` (63% vs 37–39%) and `person` (98% vs 78–88%).
- **Llama 3.1 8B leads on `item_type` (56%) and `mode` (68%)**, but has two systematic faults:
  - `effort_minutes`: it defaults to 240 for short tasks (expected 5–60 in most misses).
  - `due_date`: relative dates drift by a few days, and some are far off (a year out, or in the
    past).
  It also does worst on failure-group `priority` (1/8) and adversarial `priority` (0/7).
- **Qwen3-4B is about twice as fast** as either 8B model and needs about half the memory. It is
  weakest on `item_type`, `person` and `due_date`: relative dates are often off by one to five
  days, and it often leaves `person` empty when the note names someone.
- **`item_type` is weak on every model** (32–56%), and the Qwen models default heavily to
  `Delivery`. This looks like a prompt or label issue as much as a model one, and belongs to
  Phase 2 rather than this choice.
- **Adversarial `priority` is 0–1 of 7 on every model**: misleading urgency cues move priority
  whichever model runs.

## Decision

**Qwen3-8B (4-bit, 4096 context)** stays the default local model.

- **Memory: all three pass D2's limit.** At peak, with the user's normal apps open, the system
  used 12.7–13.3 GB of 16 GB. That leaves about 3 GB for Runway (a small Flask app) and a browser,
  with no swap growth and macOS's free-memory level at 40% or above. Qwen3-8B adds about 5.2 GB
  of model memory.
- It leads on five of seven fields, with the widest margins on `due_date` and `person`.
- Llama 3.1 8B uses about the same memory and has about the same latency, and is ahead on only
  `item_type` and `mode`. Its `effort_minutes` and `due_date` faults are systematic, not noise.
- **Runner-up: Qwen3-4B.** It is the only candidate that saves memory or time: about 2 GB less
  model memory and about half the latency. If memory gets tighter, Llama 3.1 8B uses as much as
  Qwen3-8B, so the 4B is the real fallback.

Because the default doesn't change, `llm.DEFAULT_MODEL`, `.env.example` and the CI `LLM_MODEL` pin
stay as they are.

## Follow-on values for `eval-observability.md`

- **D2 → Decided:** Qwen3-8B, 4-bit, 4096 context; about 5.2 GB model memory, 12.8 GB system peak.
- **D4 → Decided:** every model agreed with itself across repeats (one flip in 369 case runs), so
  day-to-day live runs can drop to 1 repeat; keep 3 for model or prompt changes.
- **D5 floors (targets, not enforced):** 5 points below Qwen3-8B's worst run: item_type 41%,
  priority 66%, effort_minutes 83%, person 93%, stream 71%, mode 49%, due_date 58%.
- **D7:** about 15.0k in / 3.5k out tokens per suite run on Qwen's tokenizer (15.2k / 3.1k on
  Llama's). The hosted cap needs the same measurement on `claude-sonnet-5-5`, since token counts
  differ by tokenizer.

## Revisit when

- A newer small model in the same memory budget is released, or Qwen ships a new 8B-class model.
- `quick_add.v2` lands (Phase 2). The ranking may change once `/no_think` and the `item_type`
  guidance are reworked; Llama's `item_type` lead is worth re-checking then.
- p50 latency becomes a user complaint. The 8B's ~9 s p50 is the main cost of this choice, and
  Qwen3-4B (~4 s) is the alternative.
- The machine's memory budget changes.
