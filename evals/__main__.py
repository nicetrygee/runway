"""`python -m evals quick_add --live | --replay [--repeat N] [--model ID]` — see
evals/runner.py."""
import argparse
import os
import sys

from dotenv import load_dotenv

import llm
from evals import runner


def _positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {value!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {n}")
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals")
    parser.add_argument("feature", choices=["quick_add"])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="call the model and record")
    mode.add_argument("--replay", action="store_true", help="re-score recordings, no model")
    parser.add_argument("--repeat", type=_positive_int, default=1, metavar="N",
                        help="runs per case (default 1); replay needs all N recorded")
    parser.add_argument("--model", metavar="ID",
                        help="override LLM_MODEL for this run only")
    args = parser.parse_args(argv)

    old_model = os.environ.get("LLM_MODEL")
    old_adapter = llm.adapter
    if args.model:
        os.environ["LLM_MODEL"] = args.model
        if isinstance(llm.adapter, llm._LazyAdapter):
            llm.adapter = llm._LazyAdapter()  # built fresh, so it reads the override
    try:
        run_eval = runner.run_live if args.live else runner.run_replay
        run = run_eval(repeat=args.repeat)
    except (runner.MissingRecording, llm.LLMUnavailable) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        if args.model:
            llm.adapter = old_adapter
            if old_model is None:
                os.environ.pop("LLM_MODEL", None)
            else:
                os.environ["LLM_MODEL"] = old_model

    runs = f" — {run.repeat} runs" if run.repeat > 1 else ""
    print(f"Quick Add — {run.mode} — {run.model} — {run.prompt_version}{runs}\n")
    print(runner.format_summary(run))
    failed = len({r.case_id for r in run.cases if not all(r.scores.values())})
    in_any = " in at least one run" if run.repeat > 1 else ""
    print(f"\n{failed}/{len(run.cases) // run.repeat} cases with at least one wrong field{in_any}")
    print(f"Report: {run.report_path}")
    print(f"JSON: {run.json_path}")
    return 0


if __name__ == "__main__":
    load_dotenv()  # LLM_BASE_URL / LLM_MODEL, as the app gets them
    sys.exit(main())
