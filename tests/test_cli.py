import click
import pytest

from pr_sentinel import cli


class _FakeStdin:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture(autouse=True)
def _clear_pat_env(monkeypatch):
    monkeypatch.delenv("AZURE_DEVOPS_PAT", raising=False)
    monkeypatch.delenv("SYSTEM_ACCESSTOKEN", raising=False)


def test_resolve_pat_prefers_env(monkeypatch):
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "env-token")
    # Even on a TTY, a set env var wins and no prompt is shown.
    monkeypatch.setattr(cli.shared.sys, "stdin", _FakeStdin(True))
    monkeypatch.setattr(cli.shared, "getpass", lambda *a, **k: pytest.fail("should not prompt"))
    assert cli._resolve_azure_pat("scope") == "env-token"


def test_resolve_pat_prompts_when_tty_and_no_env(monkeypatch):
    monkeypatch.setattr(cli.shared.sys, "stdin", _FakeStdin(True))
    monkeypatch.setattr(cli.shared, "getpass", lambda *a, **k: "  typed-token  ")
    assert cli._resolve_azure_pat("scope") == "typed-token"  # stripped


def test_resolve_pat_does_not_leak_into_environ(monkeypatch):
    import os

    monkeypatch.setattr(cli.shared.sys, "stdin", _FakeStdin(True))
    monkeypatch.setattr(cli.shared, "getpass", lambda *a, **k: "typed-token")
    cli._resolve_azure_pat("scope")
    # The prompted PAT must not be pushed into the environment (would leak into
    # provider subprocesses we spawn).
    assert "AZURE_DEVOPS_PAT" not in os.environ


def test_resolve_pat_errors_when_not_a_tty(monkeypatch):
    monkeypatch.setattr(cli.shared.sys, "stdin", _FakeStdin(False))
    monkeypatch.setattr(cli.shared, "getpass", lambda *a, **k: pytest.fail("must not prompt in CI"))
    with pytest.raises(click.UsageError):
        cli._resolve_azure_pat("scope")


def test_resolve_pat_errors_when_prompt_empty(monkeypatch):
    monkeypatch.setattr(cli.shared.sys, "stdin", _FakeStdin(True))
    monkeypatch.setattr(cli.shared, "getpass", lambda *a, **k: "   ")
    with pytest.raises(click.UsageError):
        cli._resolve_azure_pat("scope")


# --- Alignment diff budget --------------------------------------------------

def _file(diff: str) -> dict:
    return {"filePath": "a.cs", "changeType": "modified", "diff": diff}


def test_alignment_diff_block_not_truncated_under_budget():
    block, truncated = cli._alignment_diff_block([_file("+ small change\n")])
    assert truncated is False
    assert "TRUNCATED" not in block


def test_alignment_diff_block_truncates_over_its_own_budget():
    # Exceed the alignment budget (500k), which is far above the routed agents'
    # 100k chunk budget — confirming alignment uses the larger, dedicated cap.
    big = "+" + ("x" * (cli.ALIGNMENT_DIFF_BUDGET + 10_000))
    block, truncated = cli._alignment_diff_block([_file(big)])
    assert truncated is True
    assert "TRUNCATED (exceeded alignment diff budget)" in block
    # Diff content is capped at the budget (plus the appended marker line).
    assert len(block) <= cli.ALIGNMENT_DIFF_BUDGET + 100


def test_alignment_budget_is_larger_than_chunk_budget():
    # The whole point: alignment must NOT inherit the routed agents' chunk budget.
    assert cli.ALIGNMENT_DIFF_BUDGET > cli.DEFAULT_CHUNK_BUDGET
