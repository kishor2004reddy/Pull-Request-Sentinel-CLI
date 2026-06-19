import time
from pathlib import Path

import click

from pr_sentinel import cache, report_generator, runstats, ui
from pr_sentinel.diff import diff_parser, git_diff
from pr_sentinel.agents.alignment_agent import AlignmentAgent
from pr_sentinel.integrations import azure_devops
from pr_sentinel.config import (
    DEFAULT_BASE_BRANCH,
    DEFAULT_CHUNK_BUDGET,
    DEFAULT_FETCH,
    DEFAULT_HEAD_REF,
    DEFAULT_OUT_DIR,
    DEFAULT_PROVIDER,
    DEFAULT_REMOTE,
    DEFAULT_TIMEOUT,
    VALID_PROVIDERS,
    default_model_for,
)
from pr_sentinel.cli.shared import (
    console,
    _alignment_diff_block,
    _apply_pr_branches,
    _fetch_pr,
    _pr_meta,
    _resolve_azure_client,
    _resolve_azure_pat,
    _run_alignment_pass,
)


@click.command(name="review-alignment")
@click.option("--pr", "pr_id", type=int, default=None, help="Azure DevOps pull request ID whose linked work items are reviewed.")
@click.option(
    "--work-item",
    "work_item_id",
    type=int,
    default=None,
    help="Review a specific work item ID directly, instead of the ones linked to a PR.",
)
@click.option("--base", default=DEFAULT_BASE_BRANCH, show_default=True, help="Base branch to diff against.")
@click.option(
    "--head",
    default=DEFAULT_HEAD_REF,
    show_default=True,
    help="Source branch/ref to review. Defaults to HEAD (currently checked-out branch).",
)
@click.option(
    "--fetch",
    "do_fetch",
    is_flag=True,
    default=DEFAULT_FETCH,
    help=(
        "Fetch from the named remote before diffing so the review matches what "
        "Azure DevOps shows for the PR. Rewrites base → {remote}/{base} and "
        "head → {remote}/{current_branch}."
    ),
)
@click.option("--org", default=None, help="Azure DevOps organization (overrides remote detection).")
@click.option("--project", default=None, help="Azure DevOps project (overrides remote detection).")
@click.option("--repo", default=None, help="Azure DevOps repository (overrides remote detection).")
@click.option(
    "--remote",
    default=DEFAULT_REMOTE,
    show_default=True,
    help=(
        "Git remote used to detect org/project/repo and (with --fetch) to fetch from. "
        "Use when your Azure DevOps remote is not named 'origin'. "
        f"Default controlled by DEFAULT_REMOTE in config.py (currently {DEFAULT_REMOTE!r})."
    ),
)
@click.option(
    "--repo-dir",
    "repo_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Repository to diff and whose remote is parsed for org/project/repo. Defaults to cwd.",
)
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_OUT_DIR,
    show_default=True,
    help="Output directory for the alignment report.",
)
@click.option(
    "--provider",
    type=click.Choice(sorted(VALID_PROVIDERS)),
    default=DEFAULT_PROVIDER,
    show_default=True,
    help="AI CLI to run the alignment agent through.",
)
@click.option("--model", default=None, help="Model forwarded to the selected provider.")
@click.option(
    "--timeout",
    type=click.IntRange(min=1),
    default=DEFAULT_TIMEOUT,
    show_default=True,
    help="Per-call timeout in seconds for the provider subprocess.",
)
@click.option("--no-cache", is_flag=True, default=False, help="Bypass the response cache for this run.")
def review_alignment(
    pr_id: int | None,
    work_item_id: int | None,
    base: str,
    head: str,
    do_fetch: bool,
    org: str | None,
    project: str | None,
    repo: str | None,
    remote: str,
    repo_dir: Path | None,
    out_dir: Path,
    provider: str,
    model: str | None,
    timeout: int,
    no_cache: bool,
) -> None:
    """Review whether a PR's code satisfies its linked Azure DevOps work item.

    Fetches the work item(s) linked to --pr (or the explicit --work-item),
    diffs --base...--head, and asks the Alignment Agent whether the change
    satisfies each acceptance criterion. Writes an alignment report whose
    `findings` are push-azure compatible (post the gaps with
    `push-azure --report reports/alignment-report.json`).
    """
    if not pr_id and not work_item_id:
        raise click.UsageError("Pass --pr <id> or --work-item <id>.")

    model = model or default_model_for(provider)
    use_cache = not no_cache

    pat = _resolve_azure_pat("Work Items: read, Code: read scope")
    client, org_, project_, repo_ = _resolve_azure_client(
        org, project, repo, remote, repo_dir, pat
    )

    try:
        ids = [work_item_id] if work_item_id else client.get_pr_work_items(pr_id)
        work_items = client.get_work_items(ids)
    except azure_devops.AzureDevOpsError as e:
        raise click.UsageError(str(e))

    if not work_items:
        console.print(
            f"[yellow]No linked work items found for PR #{pr_id}.[/]"
            if pr_id
            else f"[yellow]Work item {work_item_id} not found.[/]"
        )
        return

    # When reviewing a PR, default base/head to the PR's branches and fetch them
    # so the diff matches what Azure DevOps shows (a --work-item run has no PR to
    # derive from, so it keeps --base/--head).
    pr_meta: dict | None = None
    if pr_id:
        pr = _fetch_pr(client, pr_id)
        pr_meta = _pr_meta(pr, org_, project_, repo_)
        base, head, derived = _apply_pr_branches(pr, base, head)
        if derived:
            do_fetch = True
            console.print(
                f"[dim]PR #{pr_id}: diffing [/][cyan]{base}[/][dim] ← [/]"
                f"[cyan]{head}[/][dim] (fetching from {remote}).[/]"
            )

    # Build the diff (mirrors `review`: optional fetch, then base...head).
    if do_fetch:
        prefix = f"{remote}/"
        base_branch = base[len(prefix):] if base.startswith(prefix) else base
        if head == "HEAD":
            current = git_diff.get_current_branch(cwd=repo_dir).strip()
            head_branch = current or "HEAD"
        else:
            head_branch = head[len(prefix):] if head.startswith(prefix) else head
        console.print(f"[dim]Fetching {base_branch}, {head_branch} from {remote}…[/dim]")
        git_diff.fetch_remote(remote=remote, refs=[base_branch, head_branch], cwd=repo_dir)
        base = f"{prefix}{base_branch}"
        head = f"{prefix}{head_branch}"

    raw_diff = git_diff.get_branch_diff(base, head=head, cwd=repo_dir)
    files = diff_parser.parse(raw_diff)
    if not files:
        console.print("[yellow]No reviewable files in the diff — nothing to align against.[/]")
        return

    # Holistic judgment needs the whole diff in one call; if it exceeds the chunk
    # budget we truncate (with a visible note) rather than silently splitting and
    # fracturing the verdict.
    diff_block, truncated = _alignment_diff_block(files)
    if truncated:
        console.print(
            f"[yellow]Diff is large ({len(raw_diff):,} chars) — truncated to "
            f"{DEFAULT_CHUNK_BUDGET:,} chars for the alignment check. "
            "The verdict is partial.[/]"
        )

    cache.reset_stats()
    runstats.reset()
    _t_start = time.perf_counter()

    console.print()
    alignment_sections, all_findings, any_failed = _run_alignment_pass(
        client,
        work_items,
        diff_block,
        truncated,
        model=model,
        timeout=timeout,
        use_cache=use_cache,
        provider=provider,
    )

    # Reuse build_report for the standard finding envelope (risk level, stable
    # finding ids, push-azure shape), then attach the alignment-specific sections.
    try:
        repo_root = git_diff.get_repo_root(cwd=repo_dir)
    except Exception:
        repo_root = None
    agent_result = {"agent": AlignmentAgent.display_name, "findings": all_findings}
    # Only mark the agent failed when every run failed *and* produced nothing —
    # if some work items yielded gaps, the report is still useful.
    if any_failed and not all_findings:
        agent_result["failed"] = True
        agent_result["error"] = "alignment run failed"
    report = report_generator.build_report(
        agent_results=[agent_result],
        base_branch=base,
        source=f"alignment:PR#{pr_id}" if pr_id else f"alignment:WI#{work_item_id}",
        repo_root=repo_root,
    )
    report["alignment"] = alignment_sections
    if pr_meta is not None:
        report["pr"] = pr_meta

    report_path = report_generator.write_alignment_json(report, out_dir)
    html_path = report_generator.write_alignment_html(report, out_dir)

    console.print(
        ui.runstats_panel(
            runstats.summary(),
            time.perf_counter() - _t_start,
            cache.stats(),
            cache_enabled=use_cache,
        )
    )
    console.print(ui.reports_panel([report_path, html_path]))

    if pr_id:
        console.print(
            "[dim]Select verdicts and gaps to post to the PR with:[/] "
            f"[cyan]pr-sentinel push-azure --pr {pr_id} --report {report_path}[/]"
        )
