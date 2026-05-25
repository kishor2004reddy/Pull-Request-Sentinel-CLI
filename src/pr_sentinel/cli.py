import re
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table

from pr_sentinel import __version__, cache, chunker, git_diff, diff_parser, orchestrator, report_generator
from pr_sentinel.agents import AGENT_REGISTRY
from pr_sentinel.config import (
    DEFAULT_AGENTS,
    DEFAULT_BASE_BRANCH,
    DEFAULT_CHUNK_BUDGET,
    DEFAULT_HEAD_REF,
    DEFAULT_MAX_FILE_SIZE,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_MODEL,
    DEFAULT_OUT_DIR,
    DEFAULT_PRUNE_AGE,
    DEFAULT_REPORT_FORMAT,
    DEFAULT_TIMEOUT,
    RISK_STYLE,
    SOURCE_DIFF_FILENAME,
    VALID_AGENTS,
    VALID_FORMATS,
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


def _header_panel(
    source: str,
    repo_dir,
    model,
    diff_save_path: Path,
    base_branch: str | None = None,
    head_branch: str | None = None,
) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column()
    grid.add_row("Source", source)
    if base_branch and head_branch:
        grid.add_row("Base", base_branch)
        grid.add_row("Head", head_branch)
    grid.add_row("Repo", str(repo_dir) if repo_dir else "(current directory)")
    grid.add_row("Model", model or "Claude Code default")
    grid.add_row("Saved diff", str(diff_save_path))
    return Panel(
        grid,
        title="[bold cyan]PR Sentinel[/]",
        subtitle=f"[dim]v{__version__}[/]",
        border_style="cyan",
        padding=(1, 2),
    )


def _skipped_panel(paths: list[str]) -> Panel:
    body = "\n".join(f"[dim]{p}[/]" for p in paths)
    return Panel(
        body,
        title=f"[yellow]Skipped {len(paths)} noise file(s)[/]",
        border_style="yellow",
        padding=(0, 1),
    )


def _files_table(files: list[dict]) -> Table:
    table = Table(
        title="Files to review",
        title_style="bold",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Change", style="magenta")
    table.add_column("Path")
    table.add_column("+", justify="right", style="green")
    table.add_column("-", justify="right", style="red")
    for f in files:
        table.add_row(
            f["changeType"],
            f["filePath"],
            str(f["addedLines"]),
            str(f["removedLines"]),
        )
    return table


def _run_plan_panel(
    total_diff_size: int,
    chunk_count: int,
    chunk_budget: int,
    available: list[str],
    max_parallel: int,
    timeout: int,
) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column()
    grid.add_row("Diff size", f"{total_diff_size:,} chars")
    grid.add_row("Chunks", f"{chunk_count}  [dim](budget {chunk_budget:,} chars)[/]")
    grid.add_row("Agents", ", ".join(available))
    grid.add_row("Max parallel", str(max_parallel))
    grid.add_row("Timeout", f"{timeout}s")
    return Panel(
        grid,
        title="[bold]Run plan[/]",
        border_style="cyan",
        padding=(1, 2),
    )


def _findings_table(agent_results: list[dict]) -> Table:
    table = Table(
        title="Findings",
        title_style="bold",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Agent")
    table.add_column("Status")
    table.add_column("Total", justify="right")
    table.add_column("High", justify="right", style="red")
    table.add_column("Medium", justify="right", style="yellow")
    table.add_column("Low", justify="right", style="blue")

    total = high = medium = low = 0
    for r in agent_results:
        if r.get("failed"):
            table.add_row(r["agent"], "[red]FAILED[/]", "-", "-", "-", "-")
            continue
        findings = r["findings"]
        f_high = sum(1 for f in findings if f["severity"] == "High")
        f_med = sum(1 for f in findings if f["severity"] == "Medium")
        f_low = sum(1 for f in findings if f["severity"] == "Low")
        total += len(findings)
        high += f_high
        medium += f_med
        low += f_low
        table.add_row(
            r["agent"],
            "[green]OK[/]",
            str(len(findings)),
            str(f_high),
            str(f_med),
            str(f_low),
        )
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/]",
        "",
        f"[bold]{total}[/]",
        f"[bold red]{high}[/]",
        f"[bold yellow]{medium}[/]",
        f"[bold blue]{low}[/]",
    )
    return table


def _errors_panel(errors: list[tuple[str, str]]) -> Panel:
    body = "\n\n".join(f"[bold red]{name}[/]\n[dim]{err}[/]" for name, err in errors)
    return Panel(
        body,
        title=f"[bold red]Errors ({len(errors)})[/]",
        border_style="red",
        padding=(1, 2),
    )


def _verdict_panel(report: dict, agent_results: list[dict]) -> Panel:
    risk = report["riskLevel"]
    style = RISK_STYLE.get(risk, "white")
    failed_count = sum(1 for r in agent_results if r.get("failed"))

    if failed_count == len(agent_results):
        body = "[bold]All agents failed[/] — see errors above and re-run."
    elif failed_count:
        body = (
            f"{report['summary']}\n\n"
            f"[yellow]Warning: {failed_count} agent(s) failed — "
            f"risk level reflects only successful agents.[/]"
        )
    else:
        body = report["summary"]

    return Panel(
        f"[bold {style}]Risk Level: {risk}[/]\n\n{body}",
        title="[bold]Verdict[/]",
        border_style=style,
        padding=(1, 2),
    )


def _reports_panel(written: list[Path]) -> Panel:
    body = "\n".join(f"[dim]{p}[/]" for p in written)
    return Panel(
        body,
        title="[bold]Reports written[/]",
        border_style="dim",
        padding=(0, 1),
    )


@click.group()
@click.version_option(package_name="pr-sentinel")
def main() -> None:
    """PR Sentinel — local PR review via Claude Code CLI."""


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
    help="Max combined diff chars per Claude call before chunking.",
)
@click.option(
    "--model",
    default=DEFAULT_MODEL,
    help=(
        "Claude model to use. "
        "Available shortcuts: sonnet, opus, haiku. "
        "Or pass a full model ID such as claude-opus-4-7, claude-sonnet-4-6, "
        "claude-haiku-4-5-20251001. "
        f"Forwarded to `claude --model`. Default: {DEFAULT_MODEL}"
    ),
)
@click.option(
    "--max-parallel",
    type=click.IntRange(min=1),
    default=DEFAULT_MAX_PARALLEL,
    show_default=True,
    help=(
        "Max concurrent claude calls across all (agent, chunk) pairs. "
        "Default 8 covers 1-2 chunk runs fully and gives ~2x speedup on large diffs. "
        "Lower (4-6) if you're rate-limited; higher (12-16) on CI boxes with headroom."
    ),
)
@click.option(
    "--timeout",
    type=click.IntRange(min=1),
    default=DEFAULT_TIMEOUT,
    show_default=True,
    help=(
        "Per-call timeout in seconds for each claude subprocess. "
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
        "combination will hit Claude even if a cached response exists. "
        "Successful responses still get written to the cache."
    ),
)
def review(
    base: str,
    head: str,
    diff_path: Path | None,
    staged: bool,
    repo_dir: Path | None,
    agents: str,
    out_dir: Path,
    out_format: str,
    max_file_size: int,
    chunk_budget: int,
    model: str | None,
    max_parallel: int,
    timeout: int,
    no_cache: bool,
) -> None:
    """Review changes and write a structured report."""
    agent_list = _parse_agents(agents)

    if diff_path and staged:
        raise click.UsageError("--diff and --staged cannot be combined.")

    base_display: str | None = None
    head_display: str | None = None

    if diff_path:
        raw_diff = diff_path.read_text(encoding="utf-8", errors="replace")
        source = f"file:{diff_path}"
    elif staged:
        raw_diff = git_diff.get_staged_diff(cwd=repo_dir)
        source = f"staged@{repo_dir}" if repo_dir else "staged"
    else:
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
        _header_panel(
            source, repo_dir, model, diff_save_path, base_display, head_display
        )
    )

    files = diff_parser.parse(raw_diff, max_file_size=max_file_size)
    kept_paths = {f["filePath"] for f in files}
    skipped_noise = [
        p for p in diff_parser.all_paths(raw_diff) if p not in kept_paths
    ]

    if skipped_noise:
        console.print(_skipped_panel(skipped_noise))

    if not files:
        console.print("[yellow]No reviewable files. Exiting.[/]")
        return

    console.print(_files_table(files))

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
    chunks = chunker.chunk_files(files, budget=chunk_budget)
    chunk_count = len(chunks)

    console.print(
        _run_plan_panel(
            total_diff_size, chunk_count, chunk_budget, available, max_parallel, timeout
        )
    )

    agent_displays = [AGENT_REGISTRY[k].display_name for k in available]
    errors: list[tuple[str, str]] = []
    use_cache = not no_cache
    cache.reset_stats()
    cache.auto_prune()

    with Progress(
        TextColumn("[bold]{task.fields[name]:<22}[/]"),
        BarColumn(bar_width=30),
        TextColumn("[cyan]{task.completed}/{task.total}[/]"),
        TextColumn("{task.fields[status]}"),
        console=console,
    ) as progress:
        task_ids = {
            name: progress.add_task("", total=chunk_count, name=name, status="")
            for name in agent_displays
        }

        def _on_chunk_done(name: str, _idx: int, _total: int) -> None:
            progress.update(task_ids[name], advance=1)

        def _on_finish(name: str, result_or_err) -> None:
            if isinstance(result_or_err, Exception):
                errors.append((name, str(result_or_err)))
                progress.update(task_ids[name], status="[bold red]FAILED[/]")

        agent_results = orchestrator.run_agents(
            agent_keys=available,
            chunks=chunks,
            on_finish=_on_finish,
            on_chunk_done=_on_chunk_done,
            model=model,
            max_parallel=max_parallel,
            timeout=timeout,
            use_cache=use_cache,
        )

        for r in agent_results:
            if not r.get("failed"):
                progress.update(task_ids[r["agent"]], status="[bold green]OK[/]")

    report = report_generator.build_report(
        agent_results=agent_results,
        base_branch=base,
        source=source,
    )

    written: list[Path] = []
    if out_format in ("json", "both"):
        written.append(report_generator.write_json(report, out_dir))
    if out_format in ("markdown", "both"):
        written.append(report_generator.write_markdown(report, out_dir))

    console.print(_findings_table(agent_results))

    if errors:
        console.print(_errors_panel(errors))

    console.print(_verdict_panel(report, agent_results))

    if use_cache:
        s = cache.stats()
        total = s["hits"] + s["misses"]
        if total > 0:
            saved_pct = (s["hits"] * 100) // total
            console.print(
                f"[dim]Cache:[/] [green]{s['hits']} hits[/] / "
                f"[yellow]{s['misses']} misses[/]  "
                f"[dim]({saved_pct}% saved)[/]"
            )
    else:
        console.print("[dim]Cache: disabled (--no-cache)[/]")

    console.print(_reports_panel(written))


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
