"""Shared state and helpers used across the CLI command modules.

`console` lives here so every command renders through the same Rich console, and
the cross-command helpers (Azure resolution, the alignment pass, the push server)
are defined here rather than duplicated per command.
"""
import os
import sys
import threading
from getpass import getpass
from pathlib import Path

import click
from click.core import ParameterSource
from rich.console import Console
from rich.panel import Panel

from pr_sentinel import push_server, ui
from pr_sentinel.diff import chunker, git_diff
from pr_sentinel.agents.alignment_agent import AlignmentAgent
from pr_sentinel.integrations import azure_devops
from pr_sentinel.config import (
    ALIGNMENT_DIFF_BUDGET,
    AZURE_PAT_ENV_VARS,
    VALID_AGENTS,
)

console = Console()


def _parse_agents(value: str) -> list[str]:
    requested = [a.strip().lower() for a in value.split(",") if a.strip()]
    invalid = [a for a in requested if a not in VALID_AGENTS]
    if invalid:
        raise click.BadParameter(
            f"Unknown agent(s): {', '.join(invalid)}. "
            f"Valid: {', '.join(sorted(VALID_AGENTS))}"
        )
    return requested


def _resolve_azure_pat(scope_hint: str) -> str:
    """Get the Azure DevOps PAT: environment first, then an interactive prompt.

    Order: a set ``AZURE_DEVOPS_PAT``/``SYSTEM_ACCESSTOKEN`` always wins. If
    neither is set *and* we're on an interactive terminal, prompt for it with
    hidden input — the value lives only in this process's memory for the run, is
    never written to disk, and is deliberately *not* pushed into ``os.environ``
    (which would leak it into the provider CLI subprocesses we spawn). In a
    non-interactive context (CI, piped input) we keep the hard error so the run
    can't hang waiting on a prompt.
    """
    pat = next((os.environ[v] for v in AZURE_PAT_ENV_VARS if os.environ.get(v)), None)
    if pat:
        return pat
    if sys.stdin.isatty():
        console.print(
            f"[dim]No Azure DevOps PAT in the environment. Paste one ({scope_hint}); "
            "it is used only for this run and never stored.[/]"
        )
        try:
            entered = getpass("Azure DevOps PAT (hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            entered = ""
        if entered:
            return entered
    raise click.UsageError(
        "No Azure DevOps PAT found. Set "
        f"{' or '.join(AZURE_PAT_ENV_VARS)} ({scope_hint}), "
        "or run in an interactive terminal to be prompted."
    )


def _resolve_azure_client(
    org: str | None,
    project: str | None,
    repo: str | None,
    remote: str,
    repo_dir: Path | None,
    pat: str,
) -> tuple[azure_devops.AzureDevOpsClient, str, str, str]:
    """Build an Azure client, detecting org/project/repo from the remote unless
    all three are supplied. Returns ``(client, org, project, repo)``."""
    if org and project and repo:
        org_, project_, repo_ = org, project, repo
    else:
        url = git_diff.get_remote_url(remote=remote, cwd=repo_dir)
        if not url:
            raise click.UsageError(
                f"No git '{remote}' remote found to detect the repository. "
                "Pass --org, --project and --repo explicitly, or use --remote to name the correct remote."
            )
        try:
            d_org, d_proj, d_repo = azure_devops.parse_remote(url)
        except azure_devops.AzureDevOpsError as e:
            raise click.UsageError(str(e))
        org_, project_, repo_ = org or d_org, project or d_proj, repo or d_repo
    client = azure_devops.AzureDevOpsClient(org=org_, project=project_, repo=repo_, pat=pat)
    return client, org_, project_, repo_


def _apply_pr_branches(
    pr: azure_devops.PullRequest, base: str, head: str
) -> tuple[str, str, bool]:
    """Default --base/--head to a fetched PR's target/source branches.

    Only the refs the user left at their defaults are replaced, so an explicit
    --base/--head still wins. Returns ``(base, head, derived)`` where ``derived``
    is True if either ref came from the PR (the caller then fetches so the local
    diff matches what Azure DevOps shows for the PR).
    """
    ctx = click.get_current_context()
    _non_explicit = (ParameterSource.DEFAULT, ParameterSource.DEFAULT_MAP)
    base_default = ctx.get_parameter_source("base") in _non_explicit
    head_default = ctx.get_parameter_source("head") in _non_explicit
    derived = False
    if base_default and pr.target_branch:
        base = pr.target_branch
        derived = True
    if head_default and pr.source_branch:
        head = pr.source_branch
        derived = True
    return base, head, derived


def _pr_meta(pr: azure_devops.PullRequest, org: str, project: str, repo: str) -> dict:
    """The PR context shown in the report's Overview (and persisted to JSON)."""
    return {
        "id": pr.id,
        "org": org,
        "project": project,
        "repo": repo,
        "title": pr.title,
        "baseBranch": pr.target_branch,
        "sourceBranch": pr.source_branch,
    }


def _fetch_pr(client: azure_devops.AzureDevOpsClient, pr_id: int) -> azure_devops.PullRequest:
    try:
        return client.get_pull_request(pr_id)
    except azure_devops.AzureDevOpsError as e:
        raise click.UsageError(str(e))


def _alignment_diff_block(files: list[dict]) -> tuple[str, bool]:
    """Pack the parsed diff into one block for the holistic alignment call,
    truncating (with a flag) when it exceeds the alignment diff budget.

    Alignment is one call per work item carrying the whole diff, so it uses its
    own ALIGNMENT_DIFF_BUDGET (much larger than the routed agents' chunk budget)
    rather than chunking.
    """
    diff_block = chunker.format_diff_block(files)
    truncated = len(diff_block) > ALIGNMENT_DIFF_BUDGET
    if truncated:
        diff_block = (
            diff_block[:ALIGNMENT_DIFF_BUDGET]
            + "\n\n===== DIFF TRUNCATED (exceeded alignment diff budget) ====="
        )
    return diff_block, truncated


def _run_alignment_pass(
    client: azure_devops.AzureDevOpsClient,
    work_items: list,
    diff_block: str,
    truncated: bool,
    *,
    model: str | None,
    timeout: int,
    use_cache: bool,
    provider: str,
) -> tuple[list[dict], list[dict], bool]:
    """Run the Alignment Agent over each work item, printing a panel per item.

    Returns ``(alignment_sections, gap_findings, any_failed)`` — the per-work-item
    verdict sections, the flattened gap findings, and whether any run failed.
    """
    agent = AlignmentAgent()
    sections: list[dict] = []
    findings: list[dict] = []
    any_failed = False
    for wi in work_items:
        with console.status(f"[dim]Alignment Agent: reviewing #{wi.id} {wi.title}…[/]"):
            result = agent.run(
                work_item=wi,
                diff_block=diff_block,
                model=model,
                timeout=timeout,
                use_cache=use_cache,
                provider=provider,
            )
        console.print(ui.alignment_panel(wi, result))
        if result.get("failed"):
            any_failed = True
        findings.extend(result.get("findings", []))
        sections.append(
            {
                "workItem": {
                    "id": wi.id,
                    "type": wi.type,
                    "state": wi.state,
                    "title": wi.title,
                },
                "verdict": result.get("verdict"),
                "confidence": result.get("confidence"),
                "summary": result.get("summary"),
                "criteria": result.get("criteria", []),
                "truncatedDiff": truncated,
            }
        )
    return sections, findings, any_failed


def _serve_push(
    report: dict,
    client: azure_devops.AzureDevOpsClient,
    pr_id: int,
    org_: str,
    project_: str,
    repo_: str,
    port: int = 0,
    no_browser: bool = False,
) -> None:
    """Serve the report locally so the user can select findings and push them to
    the PR, then block until Ctrl+C. Shared by `push-azure` and `review --align`.
    """
    def _on_event(results: list[dict]) -> None:
        ok = sum(1 for r in results if r.get("ok"))
        fail = len(results) - ok
        msg = f"[green]Pushed {ok} finding(s)[/]"
        if fail:
            msg += f", [red]{fail} failed[/]"
        console.print(f"{msg} to PR #{pr_id}.")
        for r in results:
            if not r.get("ok"):
                console.print(f"  [red]✗[/] {r['id']}: {r.get('error', 'failed')}")

    url, httpd = push_server.start_server(
        report, client, pr_id, port=port, open_browser=not no_browser, on_event=_on_event,
    )

    console.print(
        Panel(
            f"[bold]Repo[/]  {org_}/{project_}/{repo_}\n"
            f"[bold]PR[/]    #{pr_id}\n"
            f"[bold]URL[/]   [cyan]{url}[/]\n\n"
            "Select findings in the browser and click [bold]Push selected to PR[/].\n"
            "Press [bold]Ctrl+C[/] here when you're done.",
            title="[bold]PR Sentinel — push to Azure DevOps[/]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    # Poll in short intervals rather than blocking forever: on Windows a
    # no-timeout wait() prevents the interpreter from delivering Ctrl+C, so the
    # KeyboardInterrupt would never fire and the server wouldn't stop.
    stop = threading.Event()
    try:
        while not stop.wait(0.5):
            pass
    except KeyboardInterrupt:
        console.print("\n[dim]Shutting down push server.[/]")
    finally:
        httpd.shutdown()
        httpd.server_close()
