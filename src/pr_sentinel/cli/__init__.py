import sys  # noqa: F401  -- re-exported so tests can patch cli.sys

import click

from pr_sentinel.config import CONFIG_KEY_TO_PARAM, load_user_config
# Re-exported for back-compat and tests that reach in via `pr_sentinel.cli.X`.
from pr_sentinel.config import (  # noqa: F401
    ALIGNMENT_DIFF_BUDGET,
    DEFAULT_CHUNK_BUDGET,
)
from pr_sentinel.cli import shared  # noqa: F401  -- exposes cli.shared for tests
from pr_sentinel.cli.shared import (  # noqa: F401
    console,
    _alignment_diff_block,
    _apply_pr_branches,
    _fetch_pr,
    _parse_agents,
    _pr_meta,
    _resolve_azure_client,
    _resolve_azure_pat,
    _run_alignment_pass,
    _serve_push,
)
from pr_sentinel.cli.review import review
from pr_sentinel.cli.push_azure import push_azure
from pr_sentinel.cli.review_alignment import review_alignment
from pr_sentinel.cli.agents_cmd import list_agents
from pr_sentinel.cli.cache_cmd import cache_group
from pr_sentinel.cli.config_cmd import config_group


@click.group()
@click.version_option(package_name="pr-sentinel")
@click.pass_context
def main(ctx: click.Context) -> None:
    """PR Sentinel — local PR review via GitHub Copilot or Claude Code CLI."""
    user_cfg = load_user_config()
    if user_cfg:
        param_defaults = {
            CONFIG_KEY_TO_PARAM[k]: v
            for k, v in user_cfg.items()
            if k in CONFIG_KEY_TO_PARAM
        }
        ctx.default_map = {"review": param_defaults}


main.add_command(review)
main.add_command(push_azure)
main.add_command(review_alignment)
main.add_command(list_agents)
main.add_command(cache_group)
main.add_command(config_group)


if __name__ == "__main__":
    main()
