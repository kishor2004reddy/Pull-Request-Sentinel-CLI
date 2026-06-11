"""Rich presentation builders for the `review` command.

Pure functions: each builds and returns a Rich `Panel`/`Table` from plain data
and does no printing. Keeping them here separates *what the command does* (in
cli.py) from *how it is drawn*.
"""
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from pr_sentinel import __version__
from pr_sentinel.config import DEFAULT_PROVIDER, RISK_STYLE


def header_panel(
    source: str,
    repo_dir,
    model,
    diff_save_path: Path,
    base_branch: str | None = None,
    head_branch: str | None = None,
    provider: str = DEFAULT_PROVIDER,
) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column()
    grid.add_row("Source", source)
    if base_branch and head_branch:
        grid.add_row("Base", base_branch)
        grid.add_row("Head", head_branch)
    grid.add_row("Repo", str(repo_dir) if repo_dir else "(current directory)")
    grid.add_row("Provider", provider)
    grid.add_row("Model", model or f"{provider} default")
    grid.add_row("Saved diff", str(diff_save_path))
    return Panel(
        grid,
        title="[bold cyan]PR Sentinel[/]",
        subtitle=f"[dim]v{__version__}[/]",
        border_style="cyan",
        padding=(1, 2),
    )


def skipped_panel(paths: list[str]) -> Panel:
    body = "\n".join(f"[dim]{p}[/]" for p in paths)
    return Panel(
        body,
        title=f"[yellow]Skipped {len(paths)} noise file(s)[/]",
        border_style="yellow",
        padding=(0, 1),
    )


def files_table(files: list[dict]) -> Table:
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


def run_plan_panel(
    total_diff_size: int,
    per_agent_plan: list[tuple[str, int, int]],
    chunk_budget: int,
    max_parallel: int,
    timeout: int,
) -> Panel:
    """per_agent_plan: list of (display_name, file_count, chunk_count)."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column()
    grid.add_row("Diff size", f"{total_diff_size:,} chars")
    grid.add_row("Chunk budget", f"{chunk_budget:,} chars")
    for name, file_count, chunk_count in per_agent_plan:
        if chunk_count == 0:
            detail = "[dim]no relevant files — skipped[/]"
        else:
            detail = (
                f"{file_count} file(s) → {chunk_count} chunk"
                f"{'s' if chunk_count != 1 else ''}"
            )
        grid.add_row(name, detail)
    grid.add_row("Max parallel", str(max_parallel))
    grid.add_row("Timeout", f"{timeout}s")
    return Panel(
        grid,
        title="[bold]Run plan[/]",
        border_style="cyan",
        padding=(1, 2),
    )


def findings_table(agent_results: list[dict]) -> Table:
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


def errors_panel(errors: list[tuple[str, str]]) -> Panel:
    body = "\n\n".join(f"[bold red]{name}[/]\n[dim]{err}[/]" for name, err in errors)
    return Panel(
        body,
        title=f"[bold red]Errors ({len(errors)})[/]",
        border_style="red",
        padding=(1, 2),
    )


def verdict_panel(report: dict, agent_results: list[dict]) -> Panel:
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


_ALIGNMENT_VERDICT_STYLE = {
    "Satisfied": "green",
    "Partial": "yellow",
    "Not satisfied": "red",
    "Unknown": "red",
}
_CRITERION_ICON = {
    "Met": "[green]✓[/]",
    "Partial": "[yellow]~[/]",
    "Not met": "[red]✗[/]",
    "Unverifiable": "[dim]?[/]",
}


def alignment_panel(work_item, result: dict) -> Panel:
    """Render one work item's alignment verdict + per-criterion checklist.

    `work_item` is an azure_devops.WorkItem; `result` is AlignmentAgent.run()'s
    output (verdict/confidence/summary/criteria/findings).
    """
    verdict = result.get("verdict", "Unknown")
    style = _ALIGNMENT_VERDICT_STYLE.get(verdict, "white")
    confidence = result.get("confidence", "Low")

    header = (
        f"[bold]#{work_item.id}[/] [cyan]{work_item.type or 'Work Item'}[/] — "
        f"{work_item.title}"
    )
    conf_note = "  [dim](low confidence)[/]" if confidence == "Low" else ""
    lines = [
        header,
        "",
        f"[bold {style}]Alignment: {verdict}[/]{conf_note}",
    ]
    summary = str(result.get("summary", "")).strip()
    if summary:
        lines += ["", summary]

    criteria = result.get("criteria") or []
    if criteria:
        lines += ["", "[bold]Criteria[/]"]
        for c in criteria:
            icon = _CRITERION_ICON.get(c.get("status", ""), "[dim]?[/]")
            text = c.get("criterion", "") or "(unspecified)"
            evidence = str(c.get("evidence", "")).strip()
            lines.append(f"  {icon} {text}")
            if evidence:
                lines.append(f"      [dim]{evidence}[/]")

    return Panel(
        "\n".join(lines),
        title="[bold]Requirement Alignment[/]",
        border_style=style,
        padding=(1, 2),
    )


def reports_panel(written: list[Path]) -> Panel:
    body = "\n".join(f"[dim]{p}[/]" for p in written)
    return Panel(
        body,
        title="[bold]Reports written[/]",
        border_style="dim",
        padding=(0, 1),
    )


def runstats_panel(
    stats: dict, wall_seconds: float, cache_stats: dict, cache_enabled: bool = True
) -> Panel:
    """Render the end-of-run metrics: time, provider calls, cache, tokens, cost.

    Token/cost/premium rows appear only when the active provider actually
    reported them (claude reports tokens + cost; copilot reports output tokens +
    premium requests but no input tokens or cost).
    """
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column()

    minutes, seconds = divmod(wall_seconds, 60)
    grid.add_row("Total time", f"{int(minutes)}m {seconds:.1f}s")
    grid.add_row("Provider calls", str(stats.get("calls", 0)))

    if cache_enabled:
        hits = cache_stats.get("hits", 0)
        misses = cache_stats.get("misses", 0)
        total = hits + misses
        if total:
            saved = hits * 100 // total
            grid.add_row(
                "Cache", f"[green]{hits} hits[/] / [yellow]{misses} misses[/] ({saved}% saved)"
            )
    else:
        grid.add_row("Cache", "[dim]disabled (--no-cache)[/]")

    if stats.get("has_input") or stats.get("has_output"):
        parts = []
        if stats.get("has_input"):
            parts.append(f"{stats['input_tokens']:,} in")
        if stats.get("has_output"):
            parts.append(f"{stats['output_tokens']:,} out")
        grid.add_row("Tokens", " · ".join(parts))

    if stats.get("has_cost"):
        grid.add_row("Cost", f"${stats['cost_usd']:.4f}")

    if stats.get("has_premium"):
        grid.add_row("Premium requests", f"{stats['premium_requests']:.2f}")

    return Panel(grid, title="[bold]Run Stats[/]", border_style="cyan", padding=(1, 2))
