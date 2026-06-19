import click
from rich.table import Table

from pr_sentinel.agents import AGENT_REGISTRY
from pr_sentinel.config import VALID_AGENTS
from pr_sentinel.cli.shared import console


@click.command(name="agents")
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
