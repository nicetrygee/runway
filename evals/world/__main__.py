"""`python -m evals.world seed --db sqlite:///lab.db [--today YYYY-MM-DD]` — see seed.py."""
import argparse
import sys
from datetime import date

import clock
from evals.world import seed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.world")
    parser.add_argument("command", choices=["seed"])
    parser.add_argument("--db", required=True, help="target, e.g. sqlite:///lab.db")
    parser.add_argument("--today", type=date.fromisoformat, default=None,
                        help="anchor date (default: today, UTC)")
    args = parser.parse_args(argv)

    today = args.today or clock.utc_now().date()
    try:
        counts = seed.seed(args.db, today)
    except seed.SeedRefused as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    world = seed.load_world()
    print(f"Seeded {args.db} anchored on {today.isoformat()}")
    print(", ".join(f"{stream}: {n}" for stream, n in counts.items()))
    print(f"Log in as {world['user']['username']} / {world['user']['password']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
