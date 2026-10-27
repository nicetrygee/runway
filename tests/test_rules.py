"""AGENTS.md AI rule 1, checked live: provider SDKs can only be imported in
llm.py. Runs the real ruff against the repo's pyproject.toml, so this fails if
the TID251 ban is ever dropped or its per-file ignore widened to features."""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

BANNED_IMPORTS = [
    "import openai\n",
    "import anthropic\n",
    "from openai import OpenAI\n",
    "from anthropic import Anthropic\n",
]


def ruff_check(source, as_filename):
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache",
         "--stdin-filename", as_filename, "-"],
        input=source, capture_output=True, text=True, cwd=REPO_ROOT,
    )


@pytest.mark.parametrize("source", BANNED_IMPORTS)
@pytest.mark.parametrize("filename", ["classify.py", "app.py", "evals/runner.py"])
def test_provider_sdk_import_is_banned_outside_llm(source, filename):
    result = ruff_check(source, filename)

    assert result.returncode != 0
    assert "TID251" in result.stdout
    assert "llm.complete" in result.stdout


@pytest.mark.parametrize("source", BANNED_IMPORTS)
def test_provider_sdk_import_is_allowed_in_llm(source):
    result = ruff_check(source, "llm.py")

    assert "TID251" not in result.stdout
