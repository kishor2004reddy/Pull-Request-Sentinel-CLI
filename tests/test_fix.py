import subprocess
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from pr_sentinel.cli.fix import (
    fix as fix_cmd,
    _build_instructions,
    _launch_prompt,
    _prepare_branch,
)
from pr_sentinel.integrations.azure_devops import FindingThread
from pr_sentinel.providers import claude, copilot
from pr_sentinel.diff import git_diff


def _ft(finding_id="a", *, file=None, line=None, comment="do the thing"):
    return FindingThread(
        finding_id=finding_id, thread_id=1, status="active",
        comment=comment, file=file, line=line,
    )


# --- instructions / prompt --------------------------------------------------

def test_build_instructions_orders_and_locates():
    threads = [
        _ft("a", file="src/app.py", line=42, comment="Fix the secret"),
        _ft("b", file="src/util.py", comment="No line here"),
        _ft("c", comment="PR-level note"),
    ]
    md = _build_instructions(threads)
    assert "fixing 3 review comment(s)" in md
    assert "one at a time" in md
    # Findings appear in order, each with the right location form.
    assert md.index("Finding 1 — src/app.py:42") < md.index("Finding 2 — src/util.py")
    assert "Finding 3 — (no specific file — PR-level comment)" in md
    # Comment bodies are carried verbatim.
    assert "Fix the secret" in md
    assert "PR-level note" in md


def test_launch_prompt_mentions_path_and_count(tmp_path):
    p = tmp_path / "fix-instructions.md"
    prompt = _launch_prompt(p, 4)
    assert str(p) in prompt
    assert "4" in prompt


# --- provider interactive argv ----------------------------------------------

def test_claude_interactive_argv(monkeypatch):
    monkeypatch.setattr(claude.shutil, "which", lambda name: "/bin/claude")
    assert claude.interactive_argv("hello") == ["/bin/claude", "hello"]
    assert claude.interactive_argv("hi", model="opus") == [
        "/bin/claude", "--model", "opus", "hi"
    ]


def test_copilot_interactive_argv(monkeypatch):
    monkeypatch.setattr(copilot.shutil, "which", lambda name: "/bin/copilot")
    assert copilot.interactive_argv("hello") == ["/bin/copilot", "-i", "hello"]
    assert copilot.interactive_argv("hi", model="gpt-5") == [
        "/bin/copilot", "--model", "gpt-5", "-i", "hi"
    ]


# --- branch handling (real throwaway repo) ----------------------------------

@pytest.fixture
def repo(tmp_path: Path) -> Path:
    def g(*a: str) -> None:
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True, text=True)

    g("init", "-b", "main")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "Test")
    (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
    g("add", ".")
    g("commit", "-m", "init")
    g("branch", "feature/x")  # the PR source branch
    return tmp_path


def test_prepare_branch_no_branch_switches_to_source(repo: Path):
    _prepare_branch(None, True, "feature/x", "main", repo)
    assert git_diff.get_current_branch(cwd=repo) == "feature/x"


def test_prepare_branch_no_branch_already_on_source(repo: Path):
    git_diff.checkout_branch("feature/x", cwd=repo)
    _prepare_branch(None, True, "feature/x", "feature/x", repo)
    assert git_diff.get_current_branch(cwd=repo) == "feature/x"


def test_prepare_branch_new_branch_from_source(repo: Path):
    _prepare_branch("fix/1", False, "feature/x", "main", repo)
    assert git_diff.get_current_branch(cwd=repo) == "fix/1"


def test_prepare_branch_dirty_tree_blocks_switch(repo: Path):
    (repo / "f.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(click.UsageError):
        _prepare_branch(None, True, "feature/x", "main", repo)


# --- command-level flag validation ------------------------------------------

def test_fix_branch_and_no_branch_mutually_exclusive():
    result = CliRunner().invoke(fix_cmd, ["--pr", "1", "--branch", "x", "--no-branch"])
    assert result.exit_code != 0
    assert "cannot be combined" in result.output
