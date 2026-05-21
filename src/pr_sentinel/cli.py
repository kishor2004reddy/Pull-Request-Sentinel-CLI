import sys
import threading
from pathlib import Path

import click

from pr_sentinel import chunker, git_diff, diff_parser, orchestrator, report_generator
from pr_sentinel.agents import AGENT_REGISTRY
from pr_sentinel.chunker import DEFAULT_CHUNK_BUDGET
from pr_sentinel.orchestrator import DEFAULT_MAX_PARALLEL, DEFAULT_TIMEOUT

DEFAULT_AGENTS = ["security", "quality", "performance", "testing"]
VALID_AGENTS = set(DEFAULT_AGENTS)
VALID_FORMATS = {"json", "markdown", "both"}


def _enable_ansi() -> bool:
    """Enable ANSI escape codes on the current stdout. Returns True if usable."""
    if not sys.stdout.isatty():
        return False
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


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
    """PR Sentinel — local PR review via Claude Code CLI."""


@main.command()
@click.option("--base", default="main", help="Base branch to diff against.")
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
    default=Path("./reports"),
    show_default=True,
    help="Output directory for reports.",
)
@click.option(
    "--format",
    "out_format",
    type=click.Choice(sorted(VALID_FORMATS)),
    default="both",
    show_default=True,
    help="Report format(s) to emit.",
)
@click.option(
    "--max-file-size",
    type=int,
    default=20000,
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
    default=None,
    help=(
        "Claude model to use. "
        "Available shortcuts: sonnet, opus, haiku. "
        "Or pass a full model ID such as claude-opus-4-7, claude-sonnet-4-6, "
        "claude-haiku-4-5-20251001. "
        "Forwarded to `claude --model`. Default: Claude Code's configured model."
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
def review(
    base: str,
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
) -> None:
    """Review changes and write a structured report."""
    agent_list = _parse_agents(agents)

    if diff_path and staged:
        raise click.UsageError("--diff and --staged cannot be combined.")

    if diff_path:
        raw_diff = diff_path.read_text(encoding="utf-8", errors="replace")
        source = f"file:{diff_path}"
    elif staged:
        raw_diff = git_diff.get_staged_diff(cwd=repo_dir)
        source = f"staged@{repo_dir}" if repo_dir else "staged"
    else:
        raw_diff = git_diff.get_branch_diff(base, cwd=repo_dir)
        source = f"branch:{base}@{repo_dir}" if repo_dir else f"branch:{base}"

    out_dir.mkdir(parents=True, exist_ok=True)
    diff_save_path = out_dir / "source.diff"
    diff_save_path.write_text(raw_diff, encoding="utf-8")
    click.echo(f"Saved diff: {diff_save_path}")

    files = diff_parser.parse(raw_diff, max_file_size=max_file_size)

    click.echo(f"Source: {source}")
    click.echo(f"Files after filter: {len(files)}")
    for f in files:
        click.echo(
            f"  {f['changeType']:8} {f['filePath']}  "
            f"(+{f['addedLines']}/-{f['removedLines']})"
        )

    if not files:
        click.echo("No reviewable files. Exiting.")
        return

    available = [a for a in agent_list if a in AGENT_REGISTRY]
    skipped = [a for a in agent_list if a not in AGENT_REGISTRY]
    if skipped:
        click.echo(f"Skipping unimplemented agents: {', '.join(skipped)}")
    if not available:
        raise click.UsageError(
            "None of the requested agents are implemented yet. "
            f"Available: {', '.join(sorted(AGENT_REGISTRY))}"
        )

    total_diff_size = sum(len(f["diff"]) for f in files)
    chunks = chunker.chunk_files(files, budget=chunk_budget)
    chunk_count = len(chunks)
    if chunk_count > 1:
        click.echo(
            f"Diff size: {total_diff_size:,} chars -> {chunk_count} chunks "
            f"(budget {chunk_budget:,})"
        )

    click.echo(f"Model: {model or 'Claude Code default'}")
    click.echo(f"Running {len(available)} agent(s) in parallel: {', '.join(available)}")

    agent_displays = [AGENT_REGISTRY[k].display_name for k in available]
    line_index = {name: i for i, name in enumerate(agent_displays)}
    n_lines = len(agent_displays)
    ansi_ok = chunk_count > 1 and _enable_ansi()

    if ansi_ok:
        for name in agent_displays:
            click.echo(f"  {name:20} waiting...")

    print_lock = threading.Lock()
    errors: list[tuple[str, str]] = []

    def _replace_line(name: str, text: str) -> None:
        lines_up = n_lines - line_index[name]
        sys.stdout.write(
            f"\033[{lines_up}A\r\033[2K  {name:20} {text}\033[{lines_up}B\r"
        )
        sys.stdout.flush()

    def _on_chunk_done(name: str, idx: int, total: int) -> None:
        if total <= 1:
            return
        with print_lock:
            if ansi_ok:
                _replace_line(name, f"chunk {idx}/{total} done")
            else:
                click.echo(f"  {name:20} chunk {idx}/{total} done")

    def _on_finish(name: str, result_or_err: dict | Exception) -> None:
        if isinstance(result_or_err, Exception):
            with print_lock:
                errors.append((name, str(result_or_err)))
                if ansi_ok:
                    _replace_line(name, "FAILED")

    agent_results = orchestrator.run_agents(
        agent_keys=available,
        chunks=chunks,
        on_finish=_on_finish,
        on_chunk_done=_on_chunk_done,
        model=model,
        max_parallel=max_parallel,
        timeout=timeout,
    )

    click.echo("")
    click.echo("Findings:")
    for r in agent_results:
        if r.get("failed"):
            continue
        click.echo(f"  {r['agent']:20} {len(r['findings'])} finding(s)")
    for name, err in errors:
        click.echo(f"  {name:20} FAILED ({err})", err=True)

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

    click.echo("")
    failed_count = sum(1 for r in agent_results if r.get("failed"))
    if failed_count == len(agent_results):
        click.echo("Risk Level: Unknown (all agents failed)")
    elif failed_count:
        click.echo(
            f"Risk Level: {report['riskLevel']} "
            f"(warning: {failed_count} agent(s) failed)"
        )
    else:
        click.echo(f"Risk Level: {report['riskLevel']}")
    click.echo(report["summary"])
    for p in written:
        click.echo(f"Wrote: {p}")


@main.command(name="agents")
def list_agents() -> None:
    """List available review agents."""
    for a in sorted(VALID_AGENTS):
        status = "ready" if a in AGENT_REGISTRY else "not implemented"
        click.echo(f"{a:12} [{status}]")


if __name__ == "__main__":
    main()
