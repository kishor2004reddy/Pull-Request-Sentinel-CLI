from pathlib import Path

import click

from pr_sentinel import git_diff, diff_parser

DEFAULT_AGENTS = ["security", "quality", "performance", "testing"]
VALID_AGENTS = {"security", "quality", "performance", "testing"}
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
def review(
    base: str,
    diff_path: Path | None,
    staged: bool,
    agents: str,
    out_dir: Path,
    out_format: str,
    max_file_size: int,
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
    click.echo(f"Agents: {', '.join(agent_list)}")
    click.echo(f"Files after filter: {len(files)}")
    for f in files:
        click.echo(f"  {f['changeType']:8} {f['filePath']}  (+{f['addedLines']}/-{f['removedLines']})")
    click.echo(f"Output dir: {out_dir} (format={out_format})")
    click.echo("[phase 1 stub] agent execution + report generation not yet wired.")


@main.command(name="agents")
def list_agents() -> None:
    """List available review agents."""
    for a in sorted(VALID_AGENTS):
        click.echo(a)


if __name__ == "__main__":
    main()
