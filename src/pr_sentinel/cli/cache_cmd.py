import re

import click
from rich.panel import Panel
from rich.table import Table

from pr_sentinel import cache
from pr_sentinel.config import DEFAULT_PRUNE_AGE
from pr_sentinel.cli.shared import console


@click.group(name="cache")
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
