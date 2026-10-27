"""`python -m evals quick_add --live | --replay` — see evals/runner.py."""
import argparse
import sys

from dotenv import load_dotenv

import llm
from evals import runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals")
    parser.add_argument("feature", choices=["quick_add"])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="call the model and record")
    mode.add_argument("--replay", action="store_true", help="re-score recordings, no model")
    args = parser.parse_args(argv)

    try:
        run = runner.run_live() if args.live else runner.run_replay()
    except (runner.MissingRecording, llm.LLMUnavailable) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"Quick Add — {run.mode} — {run.model} — {run.prompt_version}\n")
    print(runner.format_summary(run))
    failed = sum(1 for r in run.cases if not all(r.scores.values()))
    print(f"\n{failed}/{len(run.cases)} cases with at least one wrong field")
    print(f"Report: {run.report_path}")
    return 0


if __name__ == "__main__":
    load_dotenv()  # LLM_BASE_URL / LLM_MODEL, as the app gets them
    sys.exit(main())
