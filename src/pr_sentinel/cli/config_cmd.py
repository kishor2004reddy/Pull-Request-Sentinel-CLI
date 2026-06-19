import click
from rich.table import Table

from pr_sentinel.config import (
    DEFAULT_AGENTS,
    DEFAULT_BASE_BRANCH,
    DEFAULT_CHUNK_BUDGET,
    DEFAULT_COPILOT_MODEL,
    DEFAULT_FETCH,
    DEFAULT_MAX_FILE_SIZE,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_MODEL,
    DEFAULT_OUT_DIR,
    DEFAULT_PROVIDER,
    DEFAULT_REMOTE,
    DEFAULT_REPORT_FORMAT,
    DEFAULT_TIMEOUT,
    USER_CONFIG_FILE,
    VALID_FORMATS,
    VALID_PROVIDERS,
    load_user_config,
    save_user_config,
)
from pr_sentinel.cli.shared import console, _parse_agents


@click.group(name="config")
def config_group() -> None:
    """Manage personal defaults for review flags."""


# Maps each configurable key to (type, choices_or_None, built-in-default-string).
_CONFIG_SPEC: dict[str, tuple] = {
    "provider":      ("choice", VALID_PROVIDERS,  DEFAULT_PROVIDER),
    "model":         ("str",    None,             f"{DEFAULT_MODEL} (claude) / {DEFAULT_COPILOT_MODEL} (copilot)"),
    "agents":        ("agents", None,             ",".join(DEFAULT_AGENTS)),
    "base":          ("str",    None,             DEFAULT_BASE_BRANCH),
    "remote":        ("str",    None,             DEFAULT_REMOTE),
    "fetch":         ("bool",   None,             str(DEFAULT_FETCH).lower()),
    "format":        ("choice", VALID_FORMATS,    DEFAULT_REPORT_FORMAT),
    "out":           ("str",    None,             str(DEFAULT_OUT_DIR)),
    "max_parallel":  ("int",    None,             str(DEFAULT_MAX_PARALLEL)),
    "timeout":       ("int",    None,             str(DEFAULT_TIMEOUT)),
    "max_file_size": ("int",    None,             str(DEFAULT_MAX_FILE_SIZE)),
    "chunk_budget":  ("int",    None,             str(DEFAULT_CHUNK_BUDGET)),
}


def _coerce_config_value(key: str, raw: str):
    """Validate and coerce *raw* for *key*. Raises click.BadParameter on failure."""
    kind, choices, _ = _CONFIG_SPEC[key]
    if kind == "choice":
        if raw not in choices:
            raise click.BadParameter(
                f"'{raw}' is not valid for '{key}'. Choose from: {', '.join(sorted(choices))}"
            )
        return raw
    if kind == "agents":
        _parse_agents(raw)  # raises click.BadParameter on unknown agent names
        return raw
    if kind == "bool":
        if raw.lower() in ("true", "1", "yes"):
            return True
        if raw.lower() in ("false", "0", "no"):
            return False
        raise click.BadParameter(
            f"'{raw}' is not a valid boolean for '{key}'. Use true or false."
        )
    if kind == "int":
        try:
            v = int(raw)
        except ValueError:
            raise click.BadParameter(f"'{raw}' must be an integer.")
        if v < 1:
            raise click.BadParameter(f"'{key}' must be at least 1.")
        return v
    return raw  # str


@config_group.command("set")
@click.argument("key")
@click.argument("value")
def config_set_cmd(key: str, value: str) -> None:
    """Set a default value for a review flag.

    KEY is the flag name without leading dashes (e.g. provider, max-parallel).
    VALUE is the new default (e.g. copilot, 6).
    """
    key = key.replace("-", "_")
    if key not in _CONFIG_SPEC:
        raise click.UsageError(
            f"Unknown key '{key}'. Configurable keys: {', '.join(sorted(_CONFIG_SPEC))}"
        )
    coerced = _coerce_config_value(key, value)
    data = load_user_config()
    data[key] = coerced
    save_user_config(data)
    console.print(f"[green]Set[/] [bold]{key}[/] = [cyan]{coerced}[/]")


@config_group.command("unset")
@click.argument("key")
def config_unset_cmd(key: str) -> None:
    """Remove a default value, reverting to the built-in default."""
    key = key.replace("-", "_")
    if key not in _CONFIG_SPEC:
        raise click.UsageError(
            f"Unknown key '{key}'. Configurable keys: {', '.join(sorted(_CONFIG_SPEC))}"
        )
    data = load_user_config()
    if key not in data:
        console.print(f"[yellow]'{key}' is not set in your config.[/]")
        return
    del data[key]
    save_user_config(data)
    console.print(f"[green]Unset[/] [bold]{key}[/].")


@config_group.command("reset")
@click.confirmation_option(prompt="Remove all config defaults and revert to built-in values?")
def config_reset_cmd() -> None:
    """Remove all defaults, reverting every key to its built-in value."""
    data = load_user_config()
    if not data:
        console.print("[yellow]No config set — nothing to reset.[/]")
        return
    save_user_config({})
    console.print("[green]Reset[/] all config defaults.")


@config_group.command("list")
def config_list_cmd() -> None:
    """Show all configurable keys, your overrides, and the built-in defaults."""
    data = load_user_config()
    table = Table(
        title="PR Sentinel Config",
        title_style="bold",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Key", style="bold")
    table.add_column("Your Default")
    table.add_column("Built-in Default", style="dim")
    for key in sorted(_CONFIG_SPEC):
        _, _, builtin = _CONFIG_SPEC[key]
        user_val = f"[cyan]{data[key]}[/]" if key in data else "[dim]—[/]"
        table.add_row(key, user_val, builtin)
    console.print(table)
    if data:
        console.print(f"[dim]Config: {USER_CONFIG_FILE}[/]")
    else:
        console.print(f"[dim]No overrides set. Config will be created at: {USER_CONFIG_FILE}[/]")
