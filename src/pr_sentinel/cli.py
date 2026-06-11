import json
import os
import re
import threading
import time
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table

from pr_sentinel import cache, orchestrator, report_generator, router, runstats, ui
from pr_sentinel.diff import chunker, diff_parser, git_diff
from pr_sentinel.agents.summary_agent import SummaryAgent
from pr_sentinel.agents import AGENT_REGISTRY
from pr_sentinel import push_server
from pr_sentinel.integrations import azure_devops
from pr_sentinel.config import (
    AZURE_PAT_ENV_VARS,
    DEFAULT_AGENTS,
    DEFAULT_BASE_BRANCH,
    DEFAULT_CHUNK_BUDGET,
    DEFAULT_FETCH,
    DEFAULT_HEAD_REF,
    DEFAULT_REMOTE,
    DEFAULT_MAX_FILE_SIZE,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_OUT_DIR,
    DEFAULT_PROVIDER,
    DEFAULT_PRUNE_AGE,
    DEFAULT_REPORT_FORMAT,
    DEFAULT_SUMMARY_TIMEOUT,
    DEFAULT_TIMEOUT,
    IGNORE_FILE_NAME,
    REPORT_JSON_FILENAME,
    SOURCE_DIFF_FILENAME,
    VALID_AGENTS,
    VALID_FORMATS,
    VALID_PROVIDERS,
    default_model_for,
    default_summary_model_for,
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


@click.group()
@click.version_option(package_name="pr-sentinel")
def main() -> None:
    """PR Sentinel — local PR review via GitHub Copilot or Claude Code CLI."""


@main.command()
@click.option("--base", default=DEFAULT_BASE_BRANCH, help="Base branch to diff against.")
@click.option(
    "--head",
    default=DEFAULT_HEAD_REF,
    show_default=True,
    help=(
        "Source branch/ref to review. Defaults to HEAD (currently checked-out branch). "
        "Use with --base to diff arbitrary refs without checking them out, "
        "e.g. --base main --head feature/foo or --base release/2024 --head release/2025."
    ),
)
@click.option(
    "--diff",
    "diff_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Review a saved diff file instead of running git.",
)
@click.option("--staged", is_flag=True, help="Review staged changes (git diff --cached).")
@click.option(
    "--fetch",
    "do_fetch",
    is_flag=True,
    default=DEFAULT_FETCH,
    help=(
        "Fetch from the named remote before diffing so the review matches what Azure DevOps shows for the PR. "
        "Rewrites base → {remote}/{base} and head → {remote}/{current_branch}, "
        "capturing all teammate pushes to both sides of the PR. "
        f"Default controlled by DEFAULT_FETCH in config.py (currently {DEFAULT_FETCH})."
    ),
)
@click.option(
    "--remote",
    default=DEFAULT_REMOTE,
    show_default=True,
    help=(
        "Git remote to fetch from and diff against when --fetch is used. "
        "Use this when your Azure DevOps remote is not named 'origin' "
        "(e.g. --remote azure when you have both a GitHub 'origin' and an 'azure' remote). "
        f"Default controlled by DEFAULT_REMOTE in config.py (currently {DEFAULT_REMOTE!r})."
    ),
)
@click.option(
    "--repo",
    "repo_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "Path to the git repository to review. "
        "Defaults to the current working directory. "
        "Ignored when --diff is used."
    ),
)
@click.option(
    "--agents",
    default=",".join(DEFAULT_AGENTS),
    help=(
        "Comma-separated agents to run. "
        f"Available: {', '.join(DEFAULT_AGENTS)}. "
        "Pick any subset (e.g. --agents security,quality). Default: all."
    ),
)
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_OUT_DIR,
    show_default=True,
    help="Output directory for reports.",
)
@click.option(
    "--format",
    "out_format",
    type=click.Choice(sorted(VALID_FORMATS)),
    default=DEFAULT_REPORT_FORMAT,
    show_default=True,
    help="Report format(s) to emit.",
)
@click.option(
    "--max-file-size",
    type=int,
    default=DEFAULT_MAX_FILE_SIZE,
    show_default=True,
    help="Per-file diff size cap (chars). Larger files are truncated.",
)
@click.option(
    "--chunk-budget",
    type=int,
    default=DEFAULT_CHUNK_BUDGET,
    show_default=True,
    help="Max combined diff chars per provider call before chunking.",
)
@click.option(
    "--provider",
    type=click.Choice(sorted(VALID_PROVIDERS)),
    default=DEFAULT_PROVIDER,
    show_default=True,
    help=(
        "AI CLI to run the agents through. "
        "'copilot' (default) shells out to the GitHub Copilot CLI; "
        "'claude' shells out to `claude -p`. "
        "The two use different model namespaces, so "
        "--model is interpreted by whichever provider is selected."
    ),
)
@click.option(
    "--model",
    default=None,
    help=(
        "Model to use, forwarded verbatim to the selected provider. "
        f"claude: shortcuts sonnet, opus, haiku or a full ID like claude-sonnet-4-6 (default: {default_model_for('claude')}). "
        f"copilot: a Copilot model ID like claude-sonnet-4.6, gpt-5 (default: claude-sonnet-4.6). "
        "Override with --model if your plan does not include the default."
    ),
)
@click.option(
    "--max-parallel",
    type=click.IntRange(min=1),
    default=DEFAULT_MAX_PARALLEL,
    show_default=True,
    help=(
        "Max concurrent provider calls across all (agent, chunk) pairs. "
        f"Default {DEFAULT_MAX_PARALLEL} covers 1-2 chunk runs fully and gives ~2x speedup on large diffs. "
        "Lower (4-6) if you're rate-limited; higher (12-16) on CI boxes with headroom."
    ),
)
@click.option(
    "--timeout",
    type=click.IntRange(min=1),
    default=DEFAULT_TIMEOUT,
    show_default=True,
    help=(
        "Per-call timeout in seconds for each provider subprocess. "
        "Default 600 (10 min) is generous; lower for fail-fast CI runs, "
        "raise if you see timeouts with opus on large chunks."
    ),
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help=(
        "Bypass the response cache for this run. Every (agent, chunk, model) "
        "combination will hit the provider even if a cached response exists. "
        "Successful responses still get written to the cache."
    ),
)
@click.option(
    "--skip-files",
    "skip_files",
    default="",
    help=(
        "Comma-separated glob patterns of files to skip on top of built-in noise filters "
        "(e.g. --skip-files \"*.lock,vendor/**,fixtures/*.json\"). "
        f"A `{IGNORE_FILE_NAME}` file at the repo root is also read if present "
        "(one pattern per line, # for comments)."
    ),
)
def review(
    base: str,
    head: str,
    diff_path: Path | None,
    staged: bool,
    do_fetch: bool,
    remote: str,
    repo_dir: Path | None,
    agents: str,
    out_dir: Path,
    out_format: str,
    max_file_size: int,
    chunk_budget: int,
    provider: str,
    model: str | None,
    max_parallel: int,
    timeout: int,
    no_cache: bool,
    skip_files: str,
) -> None:
    """Review changes and write a structured report."""
    agent_list = _parse_agents(agents)

    if diff_path and staged:
        raise click.UsageError("--diff and --staged cannot be combined.")
    if do_fetch and (diff_path or staged):
        raise click.UsageError("--fetch cannot be combined with --diff or --staged.")

    # Resolve the model per provider: a user-supplied --model is forwarded
    # verbatim; otherwise fall back to the selected provider's own default.
    # The two providers have separate namespaces, so there is no translation.
    model = model or default_model_for(provider)
    summary_model = default_summary_model_for(provider)

    base_display: str | None = None
    head_display: str | None = None

    if diff_path:
        raw_diff = diff_path.read_text(encoding="utf-8", errors="replace")
        source = f"file:{diff_path}"
    elif staged:
        raw_diff = git_diff.get_staged_diff(cwd=repo_dir)
        source = f"staged@{repo_dir}" if repo_dir else "staged"
    else:
        if do_fetch:
            # Resolve the bare branch names we need *before* fetching so we can
            # fetch just those two refs (cheap on repos with many branches)
            # rather than every ref on the remote.
            prefix = f"{remote}/"
            base_branch = base[len(prefix):] if base.startswith(prefix) else base
            if head == "HEAD":
                current = git_diff.get_current_branch(cwd=repo_dir).strip()
                head_branch = current or "HEAD"
            else:
                head_branch = head[len(prefix):] if head.startswith(prefix) else head

            console.print(
                f"[dim]Fetching {base_branch}, {head_branch} from {remote}…[/dim]"
            )
            git_diff.fetch_remote(
                remote=remote, refs=[base_branch, head_branch], cwd=repo_dir
            )
            base = f"{prefix}{base_branch}"
            head = f"{prefix}{head_branch}"

        head_display = head
        if head == "HEAD":
            try:
                current = git_diff.get_current_branch(cwd=repo_dir).strip()
                if current:
                    head_display = current
            except Exception:
                pass
        base_display = base
        raw_diff = git_diff.get_branch_diff(base, head=head, cwd=repo_dir)
        repo_suffix = f"@{repo_dir}" if repo_dir else ""
        source = f"branch:{base}...{head_display}{repo_suffix}"

    out_dir.mkdir(parents=True, exist_ok=True)
    diff_save_path = out_dir / SOURCE_DIFF_FILENAME
    diff_save_path.write_text(raw_diff, encoding="utf-8")

    console.print()
    console.print(
        ui.header_panel(
            source,
            repo_dir,
            model,
            diff_save_path,
            base_display,
            head_display,
            provider=provider,
        )
    )

    ignore_root = repo_dir if repo_dir else Path.cwd()
    extra_skip_patterns = diff_parser.load_ignore_file(ignore_root / IGNORE_FILE_NAME)
    if skip_files:
        extra_skip_patterns.extend(
            p.strip() for p in skip_files.split(",") if p.strip()
        )

    files = diff_parser.parse(
        raw_diff,
        max_file_size=max_file_size,
        extra_skip_patterns=extra_skip_patterns,
    )
    kept_paths = {f["filePath"] for f in files}
    skipped_noise = [
        p for p in diff_parser.all_paths(raw_diff) if p not in kept_paths
    ]

    if skipped_noise:
        console.print(ui.skipped_panel(skipped_noise))

    if not files:
        console.print("[yellow]No reviewable files. Exiting.[/]")
        return

    console.print(ui.files_table(files))

    available = [a for a in agent_list if a in AGENT_REGISTRY]
    skipped_agents = [a for a in agent_list if a not in AGENT_REGISTRY]
    if skipped_agents:
        console.print(
            f"[yellow]Skipping unimplemented agents: {', '.join(skipped_agents)}[/]"
        )
    if not available:
        raise click.UsageError(
            "None of the requested agents are implemented yet. "
            f"Available: {', '.join(sorted(AGENT_REGISTRY))}"
        )

    total_diff_size = sum(len(f["diff"]) for f in files)

    # Per-agent diffs: each agent only sees files its routing table marks as
    # relevant. Each agent's file list is then chunked independently.
    files_by_agent: dict[str, list[dict]] = {
        k: router.files_for_agent(files, k) for k in available
    }
    chunks_by_agent: dict[str, list[list[dict]]] = {
        k: chunker.chunk_files(files_by_agent[k], budget=chunk_budget) for k in available
    }

    agent_displays = [AGENT_REGISTRY[k].display_name for k in available]
    per_agent_plan = [
        (
            AGENT_REGISTRY[k].display_name,
            len(files_by_agent[k]),
            len(chunks_by_agent[k]),
        )
        for k in available
    ]

    console.print(
        ui.run_plan_panel(
            total_diff_size, per_agent_plan, chunk_budget, max_parallel, timeout
        )
    )

    errors: list[tuple[str, str]] = []
    use_cache = not no_cache
    cache.reset_stats()
    cache.auto_prune()
    runstats.reset()
    _t_start = time.perf_counter()

    chunk_count_by_display = {
        AGENT_REGISTRY[k].display_name: len(chunks_by_agent[k]) for k in available
    }

    with Progress(
        TextColumn("[bold]{task.fields[name]:<22}[/]"),
        BarColumn(bar_width=30),
        TextColumn("[cyan]{task.completed}/{task.total}[/]"),
        TextColumn("{task.fields[status]}"),
        console=console,
    ) as progress:
        task_ids = {
            name: progress.add_task(
                "",
                total=max(chunk_count_by_display[name], 1),
                name=name,
                status="",
            )
            for name in agent_displays
        }
        # Mark skipped agents (0 chunks) as already complete.
        for name in agent_displays:
            if chunk_count_by_display[name] == 0:
                progress.update(task_ids[name], completed=1, status="[dim]skipped[/]")

        def _on_chunk_done(name: str, _idx: int, _total: int) -> None:
            progress.update(task_ids[name], advance=1)

        def _on_finish(name: str, result_or_err) -> None:
            if isinstance(result_or_err, Exception):
                errors.append((name, str(result_or_err)))
                progress.update(task_ids[name], status="[bold red]FAILED[/]")

        agent_results = orchestrator.run_agents(
            agent_keys=available,
            chunks_by_agent=chunks_by_agent,
            on_finish=_on_finish,
            on_chunk_done=_on_chunk_done,
            model=model,
            max_parallel=max_parallel,
            timeout=timeout,
            use_cache=use_cache,
            provider=provider,
        )

        for r in agent_results:
            if r.get("failed"):
                continue
            if chunk_count_by_display.get(r["agent"], 0) == 0:
                continue  # keep "skipped" status
            progress.update(task_ids[r["agent"]], status="[bold green]OK[/]")

    raw_findings = [
        f
        for r in agent_results
        if not r.get("failed")
        for f in r.get("findings", [])
    ]
    cleaned_findings: list[dict] | None = None
    removed_count = 0
    # Only run the summary pass when there are at least 2 findings — with 0 or 1
    # there is nothing to dedup/consolidate, and the call runs serially after all
    # agents, so skipping it is pure latency saved.
    if len(raw_findings) > 1:
        finding_count = len(raw_findings)
        file_count = len({f["file"] for f in raw_findings})
        _summary_messages = [
            f"[dim]Summary Agent: analysing {finding_count} findings across {file_count} file(s)...[/]",
            "[dim]Summary Agent: deduplicating cross-agent findings...[/]",
            "[dim]Summary Agent: consolidating same-pattern issues...[/]",
            "[dim]Summary Agent: finalising cleaned report...[/]",
        ]

        _stop_cycling = threading.Event()

        def _cycle_status(status):
            idx = 0
            while not _stop_cycling.wait(timeout=3):
                idx = (idx + 1) % len(_summary_messages)
                status.update(_summary_messages[idx])

        _summary_agent = SummaryAgent()
        with console.status(_summary_messages[0]) as _status:
            _t = threading.Thread(target=_cycle_status, args=(_status,), daemon=True)
            _t.start()
            try:
                cleaned_findings, removed_count = _summary_agent.run(
                    findings=raw_findings,
                    model=summary_model,
                    timeout=DEFAULT_SUMMARY_TIMEOUT,
                    use_cache=use_cache,
                    provider=provider,
                )
            except Exception as e:
                console.print(f"[yellow]Summary Agent skipped: {e}[/]")
            finally:
                _stop_cycling.set()
                _t.join()

    try:
        repo_root = git_diff.get_repo_root(cwd=repo_dir)
    except Exception:
        repo_root = None

    report = report_generator.build_report(
        agent_results=agent_results,
        base_branch=base,
        source=source,
        cleaned_findings=cleaned_findings,
        repo_root=repo_root,
    )

    written: list[Path] = []
    if out_format in ("json", "both", "all"):
        written.append(report_generator.write_json(report, out_dir))
    if out_format in ("markdown", "both", "all"):
        written.append(report_generator.write_markdown(report, out_dir))
    if out_format in ("html", "all"):
        written.append(report_generator.write_html(report, out_dir))

    console.print(ui.findings_table(agent_results))

    if cleaned_findings is not None:
        if removed_count > 0:
            console.print(
                f"[dim]Summary Agent:[/] examined [cyan]{len(raw_findings)}[/] findings, "
                f"removed [green]{removed_count}[/] duplicate/noise "
                f"→ [bold]{len(cleaned_findings)}[/] remain."
            )
        else:
            console.print(
                f"[dim]Summary Agent:[/] examined [cyan]{len(raw_findings)}[/] findings, "
                f"no duplicates found."
            )

    if errors:
        console.print(ui.errors_panel(errors))

    console.print(ui.verdict_panel(report, agent_results))

    console.print(
        ui.runstats_panel(
            runstats.summary(),
            time.perf_counter() - _t_start,
            cache.stats(),
            cache_enabled=use_cache,
        )
    )

    console.print(ui.reports_panel(written))


@main.group(name="cache")
def cache_group() -> None:
    """Inspect or clear the response cache."""


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


@cache_group.command("size")
def cache_size_cmd() -> None:
    """Show how many entries are cached and how much disk they use."""
    count, bytes_ = cache.size()
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column()
    grid.add_row("Location", str(cache.cache_dir()))
    grid.add_row("Entries", f"{count:,}")
    grid.add_row("Disk usage", _format_bytes(bytes_))
    console.print(Panel(grid, title="[bold]Cache[/]", border_style="cyan", padding=(1, 2)))


@cache_group.command("clear")
@click.confirmation_option(prompt="Delete the entire response cache?")
def cache_clear_cmd() -> None:
    """Wipe the response cache."""
    count = cache.clear()
    console.print(f"[green]Cleared {count} cached entr{'y' if count == 1 else 'ies'}.[/]")


_DURATION_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_duration(s: str) -> int:
    m = _DURATION_RE.match(s.strip())
    if not m:
        raise click.BadParameter(
            f"Invalid duration '{s}'. Use N followed by s/m/h/d "
            "(e.g. 30d, 12h, 60m, 300s)."
        )
    return int(m.group(1)) * _UNIT_SECONDS[m.group(2)]


@cache_group.command("prune")
@click.option(
    "--older-than",
    "older_than",
    default=DEFAULT_PRUNE_AGE,
    show_default=True,
    help=(
        "Delete entries older than this duration. "
        "Format: N followed by s/m/h/d (e.g. 30d, 12h, 7d, 300s). "
        "Age = time since the entry was first written."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be deleted without actually deleting anything.",
)
def cache_prune_cmd(older_than: str, dry_run: bool) -> None:
    """Delete cache entries older than a given age."""
    max_age = _parse_duration(older_than)
    count, bytes_ = cache.prune(max_age, dry_run=dry_run)
    if count == 0:
        console.print(
            f"[dim]No entries older than {older_than}.[/]"
        )
        return
    verb = "Would delete" if dry_run else "Deleted"
    style = "yellow" if dry_run else "green"
    console.print(
        f"[{style}]{verb} {count} entr{'y' if count == 1 else 'ies'} "
        f"({_format_bytes(bytes_)}) older than {older_than}.[/]"
    )


@main.command(name="push-azure")
@click.option("--pr", "pr_id", type=int, required=True, help="Azure DevOps pull request ID to comment on.")
@click.option(
    "--report",
    "report_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_OUT_DIR / REPORT_JSON_FILENAME,
    show_default=True,
    help="Path to the report.json produced by `pr-sentinel review`.",
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
        "Use when your Azure DevOps remote is not named 'origin' "
        "(e.g. --remote azure when you have both a GitHub 'origin' and an 'azure' remote). "
        f"Default controlled by DEFAULT_REMOTE in config.py (currently {DEFAULT_REMOTE!r})."
    ),
)
@click.option(
    "--repo-dir",
    "repo_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Repository whose remote is parsed for org/project/repo. Defaults to cwd.",
)
@click.option("--port", type=int, default=0, show_default=True, help="Local server port. 0 picks a free port.")
@click.option("--no-browser", is_flag=True, default=False, help="Don't auto-open the report in a browser.")
def push_azure(
    pr_id: int,
    report_path: Path,
    org: str | None,
    project: str | None,
    repo: str | None,
    remote: str,
    repo_dir: Path | None,
    port: int,
    no_browser: bool,
) -> None:
    """Open the HTML report and push selected findings to an Azure DevOps PR.

    Reads the findings from a prior `review` run, then serves the report locally
    so you can tick the findings you want and click "Push selected to PR". Each
    selected finding becomes a PR-level comment thread. The Azure DevOps PAT is
    read from $AZURE_DEVOPS_PAT (or $SYSTEM_ACCESSTOKEN) and stays server-side.
    """
    pat = next((os.environ[v] for v in AZURE_PAT_ENV_VARS if os.environ.get(v)), None)
    if not pat:
        raise click.UsageError(
            "No Azure DevOps PAT found. Set "
            f"{' or '.join(AZURE_PAT_ENV_VARS)} (Code: read & write scope) and retry."
        )

    if not report_path.exists():
        raise click.UsageError(
            f"Report not found at {report_path}. "
            "Run `pr-sentinel review --format all` (or `json`) first."
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    findings = report.get("findings", [])
    if not findings:
        console.print("[yellow]No findings in the report — nothing to push.[/]")
        return
    # Backfill ids for reports written before findings carried a stable id.
    if any("id" not in f for f in findings):
        report_generator._assign_finding_ids(findings)

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


@main.command(name="agents")
def list_agents() -> None:
    """List available review agents."""
    table = Table(
        title="Available Agents",
        title_style="bold",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Agent")
    table.add_column("Status")
    for a in sorted(VALID_AGENTS):
        ready = a in AGENT_REGISTRY
        status_text = "[green]ready[/]" if ready else "[yellow]not implemented[/]"
        table.add_row(a, status_text)
    console.print(table)


if __name__ == "__main__":
    main()
