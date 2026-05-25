import subprocess
from pathlib import Path

import click

from pr_sentinel.config import DEFAULT_UNIFIED_CONTEXT


class GitError(click.ClickException):
    pass


def _run(args: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as e:
        raise GitError("git executable not found on PATH.") from e

    if result.returncode != 0:
        raise GitError(
            f"git {' '.join(args[1:])} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def _ensure_repo(cwd: Path | None = None) -> None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise GitError("git executable not found on PATH.") from e

    if result.returncode != 0 or result.stdout.strip() != "true":
        location = f"'{cwd}'" if cwd else "Current directory"
        raise GitError(f"{location} is not inside a git repository.")


def get_current_branch(cwd: Path | None = None) -> str:
    _ensure_repo(cwd)
    return _run(["git", "branch", "--show-current"], cwd=cwd).strip()


def get_branch_diff(
    base: str,
    head: str = "HEAD",
    unified: int = DEFAULT_UNIFIED_CONTEXT,
    cwd: Path | None = None,
) -> str:
    _ensure_repo(cwd)
    return _run(["git", "diff", f"--unified={unified}", f"{base}...{head}"], cwd=cwd)


def get_staged_diff(unified: int = DEFAULT_UNIFIED_CONTEXT, cwd: Path | None = None) -> str:
    _ensure_repo(cwd)
    return _run(["git", "diff", "--cached", f"--unified={unified}"], cwd=cwd)
