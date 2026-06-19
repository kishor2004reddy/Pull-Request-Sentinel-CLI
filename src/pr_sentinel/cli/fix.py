import subprocess
from pathlib import Path

import click
from rich.panel import Panel

from pr_sentinel import providers
from pr_sentinel.diff import git_diff
from pr_sentinel.integrations import azure_devops
from pr_sentinel.config import (
    DEFAULT_OUT_DIR,
    DEFAULT_PROVIDER,
    DEFAULT_REMOTE,
    FIX_INSTRUCTIONS_FILENAME,
    VALID_PROVIDERS,
    default_model_for,
)
from pr_sentinel.cli.shared import (
    console,
    _fetch_pr,
    _resolve_azure_client,
    _resolve_azure_pat,
)


def _build_instructions(threads: list[azure_devops.FindingThread]) -> str:
    """Render the fix-instructions file: a how-to header + each PR comment verbatim.

    The comment body is included as-is (it already reads as an instruction); the
    location comes from the thread's pinned file/line when present, else the file
    named in the comment, else a PR-level note. Findings keep their listed order
    so the session can work through them top to bottom.
    """
    n = len(threads)
    lines = [
        "# PR Sentinel — Fix Instructions",
        "",
        f"You are fixing {n} review comment(s) left on a pull request. Work through "
        "them **one at a time, in the order listed**. For each finding:",
        "",
        "1. Open the named file (and read any related files you need to understand it).",
        "2. Make the **minimal** change that resolves the comment.",
        "3. Pause so I can review the edit before you move on to the next finding.",
        "",
        "Do not change unrelated code. Line numbers are hints — verify by reading the file.",
        "",
        "---",
    ]
    for i, t in enumerate(threads, 1):
        if t.file and t.line:
            loc = f"{t.file}:{t.line}"
        elif t.file:
            loc = t.file
        else:
            loc = "(no specific file — PR-level comment)"
        lines += ["", f"## Finding {i} — {loc}", "", t.comment.strip()]
    lines.append("")
    return "\n".join(lines)


def _launch_prompt(instructions_path: Path, count: int) -> str:
    """The short seed prompt that points the interactive session at the file."""
    return (
        f"Read the file `{instructions_path}` and follow its instructions exactly. "
        f"It lists {count} code-review comment(s) from a pull request to fix in this "
        "repository, one at a time. Start with the first finding."
    )


def _require_clean(repo_dir: Path | None) -> None:
    if git_diff.is_working_tree_dirty(cwd=repo_dir):
        raise click.UsageError(
            "Your working tree has uncommitted changes. Commit or stash them "
            "before switching branches, then re-run `fix`."
        )


def _create_fix_branch(name: str, source: str, repo_dir: Path | None) -> None:
    _require_clean(repo_dir)
    git_diff.create_branch(name, source, cwd=repo_dir)
    console.print(f"[green]Created and switched to[/] [bold]{name}[/] (from {source}).")


def _switch_to_source(source: str, current: str, repo_dir: Path | None) -> None:
    if current == source:
        console.print(f"[dim]Already on the PR source branch '{source}'.[/]")
        return
    _require_clean(repo_dir)
    git_diff.checkout_branch(source, cwd=repo_dir)
    console.print(f"[green]Switched to PR source branch[/] [bold]{source}[/].")


def _prepare_branch(
    new_branch: str | None,
    no_branch: bool,
    source: str,
    current: str,
    repo_dir: Path | None,
) -> None:
    """Put the working tree on the right branch before fixing.

    All fixes are anchored to the PR's *source* branch; the base branch is never
    a fix target. ``--branch`` starts a new branch from source, ``--no-branch``
    fixes on source directly, and with neither flag the user is asked. Switching
    or creating a branch requires a clean tree.
    """
    if new_branch:
        _create_fix_branch(new_branch, source, repo_dir)
    elif no_branch:
        _switch_to_source(source, current, repo_dir)
    else:
        console.print(
            f"[dim]PR source branch:[/] [cyan]{source}[/]   "
            f"[dim]you are on:[/] [cyan]{current}[/]"
        )
        if click.confirm(
            f"Create a new branch from '{source}' for these fixes? "
            f"(No = fix on '{source}' directly)",
            default=False,
        ):
            name = click.prompt("New branch name").strip()
            if not name:
                raise click.UsageError("No branch name given.")
            _create_fix_branch(name, source, repo_dir)
        else:
            _switch_to_source(source, current, repo_dir)


@click.command(name="fix")
@click.option("--pr", "pr_id", type=int, required=True, help="Azure DevOps pull request ID whose comments are fixed.")
@click.option(
    "--branch",
    "new_branch",
    default=None,
    help="Create a new branch from the PR's source branch and apply the fixes there.",
)
@click.option(
    "--no-branch",
    "no_branch",
    is_flag=True,
    default=False,
    help="Apply fixes directly on the PR's source branch (no new branch, no prompt).",
)
@click.option(
    "--provider",
    type=click.Choice(sorted(VALID_PROVIDERS)),
    default=DEFAULT_PROVIDER,
    show_default=True,
    help=(
        "AI CLI to run the interactive fix session through. "
        "'claude' launches `claude`; 'copilot' launches `copilot -i`. "
        "Both prompt you before each file edit."
    ),
)
@click.option(
    "--model",
    default=None,
    help="Model forwarded to the selected provider (defaults to the provider's own default).",
)
@click.option("--org", default=None, help="Azure DevOps organization (overrides remote detection).")
@click.option("--project", default=None, help="Azure DevOps project (overrides remote detection).")
@click.option("--repo", default=None, help="Azure DevOps repository (overrides remote detection).")
@click.option(
    "--remote",
    default=DEFAULT_REMOTE,
    show_default=True,
    help=(
        "Git remote whose URL is parsed to detect org/project/repo. "
        f"Default controlled by DEFAULT_REMOTE in config.py (currently {DEFAULT_REMOTE!r})."
    ),
)
@click.option(
    "--repo-dir",
    "repo_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="The git repository to fix. Defaults to the current working directory.",
)
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_OUT_DIR,
    show_default=True,
    help="Where to write the fix-instructions file handed to the session.",
)
def fix(
    pr_id: int,
    new_branch: str | None,
    no_branch: bool,
    provider: str,
    model: str | None,
    org: str | None,
    project: str | None,
    repo: str | None,
    remote: str,
    repo_dir: Path | None,
    out_dir: Path,
) -> None:
    """Fix a PR's review comments in an interactive Claude/Copilot session.

    Reads the still-open PR Sentinel comments from PR --pr (the PR is the source
    of truth — no report file needed), puts the working tree on the PR's source
    branch (or a new branch from it), then launches an interactive coding session
    seeded with those comments. The CLI asks before each edit; you approve, it
    writes the change. When you exit, the touched files are summarized so you can
    review, commit, and push to update the PR.
    """
    if new_branch and no_branch:
        raise click.UsageError("--branch and --no-branch cannot be combined.")

    model = model or default_model_for(provider)

    # The session writes to files on disk, so we need a real work tree.
    repo_root = git_diff.get_repo_root(cwd=repo_dir)
    if not repo_root:
        raise click.UsageError("`fix` must run inside a git repository.")

    # Resolve Azure and the PR up front so a missing PAT / unreachable PR fails
    # fast. The PR's source branch anchors every fix; the base branch is never
    # touched.
    pat = _resolve_azure_pat("Code: read & write")
    client, _org, _project, _repo = _resolve_azure_client(
        org, project, repo, remote, repo_dir, pat
    )
    pr = _fetch_pr(client, pr_id)
    source = pr.source_branch
    if not source:
        raise click.UsageError(f"PR #{pr_id} has no source branch to fix on.")

    # Work list = the PR's still-open PR Sentinel comments (skips resolved).
    threads = client.list_finding_threads(pr_id)
    if not threads:
        console.print(
            f"[yellow]No open PR Sentinel comments to fix on PR #{pr_id}.[/]"
        )
        return

    current = git_diff.get_current_branch(cwd=repo_dir)
    _prepare_branch(new_branch, no_branch, source, current, repo_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
    instructions_path = (out_dir / FIX_INSTRUCTIONS_FILENAME).resolve()
    instructions_path.write_text(_build_instructions(threads), encoding="utf-8")

    prompt = _launch_prompt(instructions_path, len(threads))
    argv = providers.get_runner(provider).interactive_argv(prompt, model)

    console.print(
        Panel(
            f"[bold]PR[/]        #{pr_id}\n"
            f"[bold]Findings[/]  {len(threads)} comment(s) to fix\n"
            f"[bold]Provider[/]  {provider}" + (f" ({model})" if model else "") + "\n"
            f"[bold]Branch[/]    {git_diff.get_current_branch(cwd=repo_dir)}\n"
            f"[bold]Guide[/]     {instructions_path}\n\n"
            f"Launching an interactive [bold]{provider}[/] session. It will ask before "
            "each edit — approve to apply. Exit the session when you're done.",
            title="[bold]PR Sentinel — fix[/]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    try:
        subprocess.run(argv, cwd=repo_root)
    except FileNotFoundError as e:
        raise click.UsageError(f"Could not launch '{provider}': {e}")

    status = git_diff.get_short_status(cwd=repo_dir)
    if status:
        console.print(
            Panel(
                status,
                title="[bold]Working tree changes[/]",
                border_style="green",
                padding=(1, 2),
            )
        )
        console.print(
            "[dim]Review the changes, then commit and push to update the PR.[/]"
        )
    else:
        console.print("[yellow]No file changes detected after the session.[/]")
