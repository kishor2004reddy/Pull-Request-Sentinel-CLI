import json
from pathlib import Path

import click

from pr_sentinel import report_generator
from pr_sentinel.config import (
    DEFAULT_OUT_DIR,
    DEFAULT_REMOTE,
    REPORT_JSON_FILENAME,
)
from pr_sentinel.cli.shared import (
    console,
    _resolve_azure_client,
    _resolve_azure_pat,
    _serve_push,
)


@click.command(name="push-azure")
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
    pat = _resolve_azure_pat("Code: read & write scope")

    if not report_path.exists():
        raise click.UsageError(
            f"Report not found at {report_path}. "
            "Run `pr-sentinel review --format all` (or `json`) first."
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    findings = report.get("findings", [])
    # An alignment report is pushable even with no gap findings — its per-work-item
    # verdicts can be posted as summary comments.
    if not findings and not report.get("alignment"):
        console.print("[yellow]No findings in the report — nothing to push.[/]")
        return
    # Backfill ids for reports written before findings carried a stable id.
    if findings and any("id" not in f for f in findings):
        report_generator._assign_finding_ids(findings)

    client, org_, project_, repo_ = _resolve_azure_client(
        org, project, repo, remote, repo_dir, pat
    )
    _serve_push(report, client, pr_id, org_, project_, repo_, port=port, no_browser=no_browser)
