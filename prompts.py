"""Prompt versioning (D23): every prompt version is pinned to a hash.

Each AI feature's prompt is a template plus an output schema, versioned by a
constant in classify.py. prompts.lock.json (committed) maps every version to a
hash of its template and schema; tests/test_prompts.py fails if the prompt in
code no longer matches the lock for its version. That's what stops a prompt
edit without a version bump, which replay would otherwise score against stale
recordings without noticing.

`python -m prompts lock` adds the current versions to the lock. It refuses to
change the hash of a version that's already locked: bump the version instead.
"""
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

import classify

LOCK_PATH = Path(__file__).resolve().parent / "prompts.lock.json"


@dataclass(frozen=True)
class Prompt:
    version: str
    template: str
    schema: type[BaseModel]
    has_evals: bool = False  # recordings to re-record when the version is bumped


REGISTRY = {
    "quick_add": Prompt(
        classify.PROMPT_VERSION,
        classify.QUICK_ADD_SYSTEM,
        classify.ExtractedTask,
        has_evals=True,
    ),
    "weekly_summary": Prompt(
        classify.WEEKLY_SUMMARY_PROMPT_VERSION,
        classify.WEEKLY_SUMMARY_SYSTEM,
        classify.WeeklySummary,
    ),
}


def prompt_hash(template: str, schema: type[BaseModel]) -> str:
    """sha256 of the template (unrendered, so "today" doesn't change it) plus
    the output schema as canonical JSON."""
    canonical_schema = json.dumps(schema.model_json_schema(), sort_keys=True)
    return hashlib.sha256(f"{template}\n{canonical_schema}".encode()).hexdigest()


def load_lock(path: Path | None = None) -> dict[str, str]:
    path = path or LOCK_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def check(lock: dict[str, str], registry: dict[str, Prompt] | None = None) -> list[str]:
    """One message per feature whose current version is unlocked or has drifted."""
    registry = REGISTRY if registry is None else registry
    problems = []
    for feature, prompt in registry.items():
        locked = lock.get(prompt.version)
        if locked is None:
            problems.append(
                f"{prompt.version} ({feature}) isn't in prompts.lock.json. "
                "Run `python -m prompts lock` and commit the result."
            )
        elif locked != prompt_hash(prompt.template, prompt.schema):
            rerecord = (
                f", then run `python -m evals {feature} --live` to re-record"
                if prompt.has_evals else ""
            )
            problems.append(
                f"Prompt or schema for {feature} changed but its version is still "
                f"{prompt.version}. Bump the version, run `python -m prompts lock`"
                f"{rerecord}."
            )
    return problems


def lock(path: Path | None = None, registry: dict[str, Prompt] | None = None) -> int:
    """Add every current version to the lock, keeping old entries. Returns 1,
    writing nothing, if a locked version's prompt has changed."""
    path = path or LOCK_PATH
    registry = REGISTRY if registry is None else registry
    entries = load_lock(path)
    changed = []
    for feature, prompt in registry.items():
        current = prompt_hash(prompt.template, prompt.schema)
        if entries.get(prompt.version, current) != current:
            changed.append(f"{prompt.version} ({feature})")
        entries[prompt.version] = current
    if changed:
        print(
            f"error: {', '.join(changed)} already locked with a different prompt. "
            "Bump the version instead of re-locking it.",
            file=sys.stderr,
        )
        return 1
    path.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["lock"]:
        print("usage: python -m prompts lock", file=sys.stderr)
        return 2
    status = lock(LOCK_PATH, REGISTRY)
    if status == 0:
        print(f"Locked {', '.join(p.version for p in REGISTRY.values())} in {LOCK_PATH.name}")
    return status


if __name__ == "__main__":
    sys.exit(main())
