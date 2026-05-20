import subprocess

import click


class GitError(click.ClickException):
    pass


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run(
            args,
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


def _ensure_repo() -> None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise GitError("git executable not found on PATH.") from e

    if result.returncode != 0 or result.stdout.strip() != "true":
        raise GitError("Current directory is not inside a git repository.")


def get_current_branch() -> str:
    _ensure_repo()
    return _run(["git", "branch", "--show-current"]).strip()


def get_branch_diff(base: str, unified: int = 80) -> str:
    _ensure_repo()
    return _run(["git", "diff", f"--unified={unified}", f"{base}...HEAD"])


def get_staged_diff(unified: int = 80) -> str:
    _ensure_repo()
    return _run(["git", "diff", "--cached", f"--unified={unified}"])
