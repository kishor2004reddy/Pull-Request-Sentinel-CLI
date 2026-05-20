from pathlib import Path

import click

from pr_sentinel import git_diff, diff_parser, orchestrator, report_generator
from pr_sentinel.agents import AGENT_REGISTRY
from pr_sentinel.chunker import DEFAULT_CHUNK_BUDGET

DEFAULT_AGENTS = ["security", "quality", "performance", "testing"]
VALID_AGENTS = set(DEFAULT_AGENTS)
VALID_FORMATS = {"json", "markdown", "both"}


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
    "--agents",
    default=",".join(DEFAULT_AGENTS),
    help="Comma-separated agents to run. Default: all.",
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
def review(
    base: str,
    diff_path: Path | None,
    staged: bool,
    agents: str,
    out_dir: Path,
    out_format: str,
    max_file_size: int,
    chunk_budget: int,
) -> None:
    """Review changes and write a structured report."""
    agent_list = _parse_agents(agents)

    if diff_path and staged:
        raise click.UsageError("--diff and --staged cannot be combined.")

    if diff_path:
        raw_diff = diff_path.read_text(encoding="utf-8", errors="replace")
        source = f"file:{diff_path}"
    elif staged:
        raw_diff = git_diff.get_staged_diff()
        source = "staged"
    else:
        raw_diff = git_diff.get_branch_diff(base)
        source = f"branch:{base}"

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

    click.echo(f"Running {len(available)} agent(s) in parallel: {', '.join(available)}")

    def _on_finish(name: str, result_or_err: dict | Exception) -> None:
        if isinstance(result_or_err, Exception):
            click.echo(f"  {name}: FAILED ({result_or_err})", err=True)
        else:
            click.echo(f"  {name}: {len(result_or_err['findings'])} finding(s)")

    agent_results = orchestrator.run_agents(
        agent_keys=available,
        files=files,
        chunk_budget=chunk_budget,
        on_finish=_on_finish,
    )

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
