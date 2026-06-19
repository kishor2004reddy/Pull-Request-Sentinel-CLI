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


def get_repo_root(cwd: Path | None = None) -> str | None:
    """Absolute path to the git work-tree root, or None if unavailable.

    Used to turn the repo-relative file paths in findings into absolute paths
    for editor deep-links. Best-effort: never raises.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def get_remote_url(remote: str = "origin", cwd: Path | None = None) -> str | None:
    """URL of the named git remote, or None if it isn't configured.

    Used to auto-detect the Azure DevOps org/project/repo for `push-azure`.
    Best-effort: never raises (a repo may have no remote, or none by that name).
    """
    try:
        result = subprocess.run(
            ["git", "config", "--get", f"remote.{remote}.url"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def fetch_remote(
    remote: str = "origin",
    refs: list[str] | None = None,
    cwd: Path | None = None,
) -> None:
    """Fetch from the named remote.

    Used by --fetch so the diff uses {remote}/{base} and {remote}/{head},
    matching exactly what Azure DevOps shows for the PR.

    When ``refs`` is given, only those branches are fetched (tags skipped),
    which is far lighter than fetching every ref on repos with many branches.
    Full history is always fetched (never shallow) so the base...head
    merge-base stays resolvable and the diff is correct.
    """
    args = ["git", "fetch", "--no-tags", remote]
    if refs:
        args.extend(refs)
    _run(args, cwd=cwd)


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


def is_working_tree_dirty(cwd: Path | None = None) -> bool:
    """True if tracked files have uncommitted (staged or unstaged) changes.

    Untracked files are ignored on purpose: they carry across a branch switch
    harmlessly (e.g. the ``reports/`` output dir), so they shouldn't block
    ``pr-sentinel fix`` from moving to the PR's source branch. Only tracked-file
    modifications make a checkout unsafe.
    """
    _ensure_repo(cwd)
    out = _run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=cwd)
    return bool(out.strip())


def checkout_branch(name: str, cwd: Path | None = None) -> None:
    """Check out an existing branch ``name``.

    Git's DWIM means a bare name with no local branch but a matching
    remote-tracking ref (after a fetch) is created and tracked — so this works
    for the PR's source branch whether or not it has been checked out locally
    before.
    """
    _ensure_repo(cwd)
    _run(["git", "checkout", name], cwd=cwd)


def create_branch(name: str, start_point: str, cwd: Path | None = None) -> None:
    """Create branch ``name`` from ``start_point`` and check it out.

    Used by ``fix --branch`` to start an isolated branch from the PR's source
    branch. Raises :class:`GitError` if ``name`` already exists, so the caller
    can surface that rather than silently reusing an existing branch.
    """
    _ensure_repo(cwd)
    _run(["git", "checkout", "-b", name, start_point], cwd=cwd)


def get_short_status(cwd: Path | None = None) -> str:
    """``git status --short`` output: the post-fix summary of what changed.

    Covers staged, unstaged, and untracked entries, so it shows everything an
    interactive fix session touched (edits and any new files). Empty string when
    nothing changed.
    """
    _ensure_repo(cwd)
    return _run(["git", "status", "--short"], cwd=cwd).strip()
