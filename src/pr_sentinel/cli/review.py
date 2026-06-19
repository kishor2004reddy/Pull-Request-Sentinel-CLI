import threading
import time
from pathlib import Path

import click
from rich.progress import BarColumn, Progress, TextColumn

from pr_sentinel import cache, orchestrator, report_generator, router, runstats, ui
from pr_sentinel.diff import chunker, diff_parser, git_diff
from pr_sentinel.agents.summary_agent import SummaryAgent
from pr_sentinel.agents.alignment_agent import AlignmentAgent
from pr_sentinel.agents import AGENT_REGISTRY
from pr_sentinel.integrations import azure_devops
from pr_sentinel.config import (
    ALIGNMENT_DIFF_BUDGET,
    DEFAULT_AGENTS,
    DEFAULT_BASE_BRANCH,
    DEFAULT_CHUNK_BUDGET,
    DEFAULT_FETCH,
    DEFAULT_HEAD_REF,
    DEFAULT_REMOTE,
    DEFAULT_MAX_FILE_SIZE,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_OUT_DIR,
    DEFAULT_PROVIDER,
    DEFAULT_REPORT_FORMAT,
    DEFAULT_SUMMARY_TIMEOUT,
    DEFAULT_TIMEOUT,
    IGNORE_FILE_NAME,
    REPORT_JSON_FILENAME,
    SOURCE_DIFF_FILENAME,
    VALID_FORMATS,
    VALID_PROVIDERS,
    default_model_for,
    default_summary_model_for,
)
from pr_sentinel.cli.shared import (
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


@click.command(name="review")
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
    "--fetch",
    "do_fetch",
    is_flag=True,
    default=DEFAULT_FETCH,
    help=(
        "Fetch from the named remote before diffing so the review matches what Azure DevOps shows for the PR. "
        "Rewrites base → {remote}/{base} and head → {remote}/{current_branch}, "
        "capturing all teammate pushes to both sides of the PR. "
        f"Default controlled by DEFAULT_FETCH in config.py (currently {DEFAULT_FETCH})."
    ),
)
@click.option(
    "--remote",
    default=DEFAULT_REMOTE,
    show_default=True,
    help=(
        "Git remote to fetch from and diff against when --fetch is used. "
        "Use this when your Azure DevOps remote is not named 'origin' "
        "(e.g. --remote azure when you have both a GitHub 'origin' and an 'azure' remote). "
        f"Default controlled by DEFAULT_REMOTE in config.py (currently {DEFAULT_REMOTE!r})."
    ),
)
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
    help="Report format(s) to emit. 'both' (default) = json+html; 'all' = json+html+markdown.",
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
    help="Max combined diff chars per provider call before chunking.",
)
@click.option(
    "--provider",
    type=click.Choice(sorted(VALID_PROVIDERS)),
    default=DEFAULT_PROVIDER,
    show_default=True,
    help=(
        "AI CLI to run the agents through. "
        "'copilot' (default) shells out to the GitHub Copilot CLI; "
        "'claude' shells out to `claude -p`. "
        "The two use different model namespaces, so "
        "--model is interpreted by whichever provider is selected."
    ),
)
@click.option(
    "--model",
    default=None,
    help=(
        "Model to use, forwarded verbatim to the selected provider. "
        f"claude: shortcuts sonnet, opus, haiku or a full ID like claude-sonnet-4-6 (default: {default_model_for('claude')}). "
        f"copilot: a Copilot model ID like claude-sonnet-4.6, gpt-5 (default: claude-sonnet-4.6). "
        "Override with --model if your plan does not include the default."
    ),
)
@click.option(
    "--max-parallel",
    type=click.IntRange(min=1),
    default=DEFAULT_MAX_PARALLEL,
    show_default=True,
    help=(
        "Max concurrent provider calls across all (agent, chunk) pairs. "
        f"Default {DEFAULT_MAX_PARALLEL} covers 1-2 chunk runs fully and gives ~2x speedup on large diffs. "
        "Lower (4-6) if you're rate-limited; higher (12-16) on CI boxes with headroom."
    ),
)
@click.option(
    "--timeout",
    type=click.IntRange(min=1),
    default=DEFAULT_TIMEOUT,
    show_default=True,
    help=(
        "Per-call timeout in seconds for each provider subprocess. "
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
        "combination will hit the provider even if a cached response exists. "
        "Successful responses still get written to the cache."
    ),
)
@click.option(
    "--skip-files",
    "skip_files",
    default="",
    help=(
        "Comma-separated glob patterns of files to skip on top of built-in noise filters "
        "(e.g. --skip-files \"*.lock,vendor/**,fixtures/*.json\"). "
        f"A `{IGNORE_FILE_NAME}` file at the repo root is also read if present "
        "(one pattern per line, # for comments)."
    ),
)
@click.option(
    "--pr",
    "pr_id",
    type=int,
    default=None,
    help=(
        "Azure DevOps pull request ID. The review diffs the PR's own branches and ends "
        "in an HTML page where you select findings to push to this PR. Needs an Azure DevOps PAT."
    ),
)
@click.option(
    "--align",
    is_flag=True,
    default=False,
    help=(
        "Also review whether the change satisfies the PR's linked work item(s); the "
        "page then also lets you push alignment verdicts and gaps. Requires --pr."
    ),
)
@click.option("--org", default=None, help="Azure DevOps organization (overrides remote detection; used with --align).")
@click.option("--project", default=None, help="Azure DevOps project (overrides remote detection; used with --align).")
@click.option(
    "--azure-repo",
    "azure_repo",
    default=None,
    help="Azure DevOps repository name (overrides remote detection; used with --align). Distinct from --repo, which is the local path.",
)
@click.option("--port", type=int, default=0, show_default=True, help="Local push-server port (with --align). 0 picks a free port.")
@click.option("--no-browser", is_flag=True, default=False, help="Don't auto-open the combined report in a browser (with --align).")
def review(
    base: str,
    head: str,
    diff_path: Path | None,
    staged: bool,
    do_fetch: bool,
    remote: str,
    repo_dir: Path | None,
    agents: str,
    out_dir: Path,
    out_format: str,
    max_file_size: int,
    chunk_budget: int,
    provider: str,
    model: str | None,
    max_parallel: int,
    timeout: int,
    no_cache: bool,
    skip_files: str,
    align: bool,
    pr_id: int | None,
    org: str | None,
    project: str | None,
    azure_repo: str | None,
    port: int,
    no_browser: bool,
) -> None:
    """Review changes and write a structured report.

    With --pr, the review diffs the PR's own branches and ends in an HTML report
    served locally, where you select findings to push to the PR. Add --align to
    also check the change against its linked Azure DevOps work item(s); the
    combined page then also lets you push alignment verdicts and gaps.
    """
    agent_list = _parse_agents(agents)

    if diff_path and staged:
        raise click.UsageError("--diff and --staged cannot be combined.")
    if do_fetch and (diff_path or staged):
        raise click.UsageError("--fetch cannot be combined with --diff or --staged.")
    if align and not pr_id:
        raise click.UsageError("--align requires --pr <id> (its work items come from the PR).")
    if pr_id and (diff_path or staged):
        raise click.UsageError(
            "--pr reviews a live branch/PR; it can't combine with --diff or --staged."
        )

    # Resolve the model per provider: a user-supplied --model is forwarded
    # verbatim; otherwise fall back to the selected provider's own default.
    # The two providers have separate namespaces, so there is no translation.
    model = model or default_model_for(provider)
    summary_model = default_summary_model_for(provider)

    # When a PR is targeted, resolve Azure up front so a missing PAT, an
    # unparseable remote, or an unreachable PR fails fast — before the full
    # review runs. The PR drives the push page; --align additionally pulls the
    # PR's linked work items for the alignment pass.
    azure_client = None
    azure_org = azure_project = azure_repo_name = ""
    pr_meta: dict | None = None
    work_items: list = []
    if pr_id:
        scope = "Code: read & write" + (", Work Items: read" if align else "")
        pat = _resolve_azure_pat(scope)
        azure_client, azure_org, azure_project, azure_repo_name = _resolve_azure_client(
            org, project, azure_repo, remote, repo_dir, pat
        )
        # Diff exactly what the PR shows: default base/head to the PR's
        # target/source branches and fetch them from the remote.
        pr = _fetch_pr(azure_client, pr_id)
        pr_meta = _pr_meta(pr, azure_org, azure_project, azure_repo_name)
        base, head, derived = _apply_pr_branches(pr, base, head)
        if derived:
            do_fetch = True
            console.print(
                f"[dim]PR #{pr_id}: diffing [/][cyan]{base}[/][dim] ← [/]"
                f"[cyan]{head}[/][dim] (fetching from {remote}).[/]"
            )
        if align:
            try:
                ids = azure_client.get_pr_work_items(pr_id)
                work_items = azure_client.get_work_items(ids)
            except azure_devops.AzureDevOpsError as e:
                raise click.UsageError(str(e))
            if not work_items:
                console.print(
                    f"[yellow]No linked work items found for PR #{pr_id} — "
                    "alignment will be skipped, but code findings can still be pushed.[/]"
                )

    base_display: str | None = None
    head_display: str | None = None

    if diff_path:
        raw_diff = diff_path.read_text(encoding="utf-8", errors="replace")
        source = f"file:{diff_path}"
    elif staged:
        raw_diff = git_diff.get_staged_diff(cwd=repo_dir)
        source = f"staged@{repo_dir}" if repo_dir else "staged"
    else:
        if do_fetch:
            # Resolve the bare branch names we need *before* fetching so we can
            # fetch just those two refs (cheap on repos with many branches)
            # rather than every ref on the remote.
            prefix = f"{remote}/"
            base_branch = base[len(prefix):] if base.startswith(prefix) else base
            if head == "HEAD":
                current = git_diff.get_current_branch(cwd=repo_dir).strip()
                head_branch = current or "HEAD"
            else:
                head_branch = head[len(prefix):] if head.startswith(prefix) else head

            console.print(
                f"[dim]Fetching {base_branch}, {head_branch} from {remote}…[/dim]"
            )
            git_diff.fetch_remote(
                remote=remote, refs=[base_branch, head_branch], cwd=repo_dir
            )
            base = f"{prefix}{base_branch}"
            head = f"{prefix}{head_branch}"

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
        ui.header_panel(
            source,
            repo_dir,
            model,
            diff_save_path,
            base_display,
            head_display,
            provider=provider,
        )
    )

    ignore_root = repo_dir if repo_dir else Path.cwd()
    extra_skip_patterns = diff_parser.load_ignore_file(ignore_root / IGNORE_FILE_NAME)
    if skip_files:
        extra_skip_patterns.extend(
            p.strip() for p in skip_files.split(",") if p.strip()
        )

    files = diff_parser.parse(
        raw_diff,
        max_file_size=max_file_size,
        extra_skip_patterns=extra_skip_patterns,
    )
    kept_paths = {f["filePath"] for f in files}
    skipped_noise = [
        p for p in diff_parser.all_paths(raw_diff) if p not in kept_paths
    ]

    if skipped_noise:
        console.print(ui.skipped_panel(skipped_noise))

    if not files:
        console.print("[yellow]No reviewable files. Exiting.[/]")
        return

    console.print(ui.files_table(files))

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

    # Per-agent diffs: each agent only sees files its routing table marks as
    # relevant. Each agent's file list is then chunked independently.
    files_by_agent: dict[str, list[dict]] = {
        k: router.files_for_agent(files, k) for k in available
    }
    chunks_by_agent: dict[str, list[list[dict]]] = {
        k: chunker.chunk_files(files_by_agent[k], budget=chunk_budget) for k in available
    }

    agent_displays = [AGENT_REGISTRY[k].display_name for k in available]
    per_agent_plan = [
        (
            AGENT_REGISTRY[k].display_name,
            len(files_by_agent[k]),
            len(chunks_by_agent[k]),
        )
        for k in available
    ]

    console.print(
        ui.run_plan_panel(
            total_diff_size, per_agent_plan, chunk_budget, max_parallel, timeout
        )
    )

    errors: list[tuple[str, str]] = []
    use_cache = not no_cache
    cache.reset_stats()
    cache.auto_prune()
    runstats.reset()
    _t_start = time.perf_counter()

    chunk_count_by_display = {
        AGENT_REGISTRY[k].display_name: len(chunks_by_agent[k]) for k in available
    }

    with Progress(
        TextColumn("[bold]{task.fields[name]:<22}[/]"),
        BarColumn(bar_width=30),
        TextColumn("[cyan]{task.completed}/{task.total}[/]"),
        TextColumn("{task.fields[status]}"),
        console=console,
    ) as progress:
        task_ids = {
            name: progress.add_task(
                "",
                total=max(chunk_count_by_display[name], 1),
                name=name,
                status="",
            )
            for name in agent_displays
        }
        # Mark skipped agents (0 chunks) as already complete.
        for name in agent_displays:
            if chunk_count_by_display[name] == 0:
                progress.update(task_ids[name], completed=1, status="[dim]skipped[/]")

        def _on_chunk_done(name: str, _idx: int, _total: int) -> None:
            progress.update(task_ids[name], advance=1)

        def _on_finish(name: str, result_or_err) -> None:
            if isinstance(result_or_err, Exception):
                errors.append((name, str(result_or_err)))
                progress.update(task_ids[name], status="[bold red]FAILED[/]")

        agent_results = orchestrator.run_agents(
            agent_keys=available,
            chunks_by_agent=chunks_by_agent,
            on_finish=_on_finish,
            on_chunk_done=_on_chunk_done,
            model=model,
            max_parallel=max_parallel,
            timeout=timeout,
            use_cache=use_cache,
            provider=provider,
        )

        for r in agent_results:
            if r.get("failed"):
                continue
            if chunk_count_by_display.get(r["agent"], 0) == 0:
                continue  # keep "skipped" status
            progress.update(task_ids[r["agent"]], status="[bold green]OK[/]")

    raw_findings = [
        f
        for r in agent_results
        if not r.get("failed")
        for f in r.get("findings", [])
    ]
    cleaned_findings: list[dict] | None = None
    removed_count = 0
    # Only run the summary pass when there are at least 2 findings — with 0 or 1
    # there is nothing to dedup/consolidate, and the call runs serially after all
    # agents, so skipping it is pure latency saved.
    if len(raw_findings) > 1:
        finding_count = len(raw_findings)
        file_count = len({f["file"] for f in raw_findings})
        _summary_messages = [
            f"[dim]Summary Agent: analysing {finding_count} findings across {file_count} file(s)...[/]",
            "[dim]Summary Agent: deduplicating cross-agent findings...[/]",
            "[dim]Summary Agent: consolidating same-pattern issues...[/]",
            "[dim]Summary Agent: finalising cleaned report...[/]",
        ]

        _stop_cycling = threading.Event()

        def _cycle_status(status):
            idx = 0
            while not _stop_cycling.wait(timeout=3):
                idx = (idx + 1) % len(_summary_messages)
                status.update(_summary_messages[idx])

        _summary_agent = SummaryAgent()
        with console.status(_summary_messages[0]) as _status:
            _t = threading.Thread(target=_cycle_status, args=(_status,), daemon=True)
            _t.start()
            try:
                cleaned_findings, removed_count = _summary_agent.run(
                    findings=raw_findings,
                    model=summary_model,
                    timeout=DEFAULT_SUMMARY_TIMEOUT,
                    use_cache=use_cache,
                    provider=provider,
                )
            except Exception as e:
                console.print(f"[yellow]Summary Agent skipped: {e}[/]")
            finally:
                _stop_cycling.set()
                _t.join()

    # --- Optional alignment pass (--align) -----------------------------------
    # Runs after the code review so it can reuse the already-parsed diff. Its
    # verdict sections and gap findings are merged into the one report below.
    alignment_sections: list[dict] | None = None
    if align and work_items:
        diff_block, truncated = _alignment_diff_block(files)
        if truncated:
            console.print(
                f"[yellow]Diff is large ({len(raw_diff):,} chars) — truncated to "
                f"{ALIGNMENT_DIFF_BUDGET:,} chars for the alignment check. "
                "The verdict is partial.[/]"
            )
        console.print()
        alignment_sections, alignment_findings, any_failed = _run_alignment_pass(
            azure_client,
            work_items,
            diff_block,
            truncated,
            model=model,
            timeout=timeout,
            use_cache=use_cache,
            provider=provider,
        )

    try:
        repo_root = git_diff.get_repo_root(cwd=repo_dir)
    except Exception:
        repo_root = None

    # Merge the alignment pass into the report: its gap findings ride alongside
    # the code-review findings (pushable as threads), and its verdict sections
    # attach as report["alignment"] (pushable as summary comments).
    report_results = agent_results
    report_cleaned = cleaned_findings
    if alignment_sections is not None:
        alignment_result = {"agent": AlignmentAgent.display_name, "findings": alignment_findings}
        if any_failed and not alignment_findings:
            alignment_result["failed"] = True
            alignment_result["error"] = "alignment run failed"
        report_results = agent_results + [alignment_result]
        if cleaned_findings is not None:
            report_cleaned = cleaned_findings + alignment_findings

    report = report_generator.build_report(
        agent_results=report_results,
        base_branch=base,
        source=source,
        cleaned_findings=report_cleaned,
        repo_root=repo_root,
    )
    if alignment_sections is not None:
        report["alignment"] = alignment_sections
    if pr_meta is not None:
        report["pr"] = pr_meta

    written: list[Path] = []
    if out_format in ("json", "both", "all"):
        written.append(report_generator.write_json(report, out_dir))
    if out_format in ("markdown", "all"):
        written.append(report_generator.write_markdown(report, out_dir))
    if out_format in ("html", "both", "all"):
        written.append(report_generator.write_html(report, out_dir))

    console.print(ui.findings_table(report_results))

    if cleaned_findings is not None:
        if removed_count > 0:
            console.print(
                f"[dim]Summary Agent:[/] examined [cyan]{len(raw_findings)}[/] findings, "
                f"removed [green]{removed_count}[/] duplicate/noise "
                f"→ [bold]{len(cleaned_findings)}[/] remain."
            )
        else:
            console.print(
                f"[dim]Summary Agent:[/] examined [cyan]{len(raw_findings)}[/] findings, "
                f"no duplicates found."
            )

    if errors:
        console.print(ui.errors_panel(errors))

    console.print(ui.verdict_panel(report, agent_results))

    console.print(
        ui.runstats_panel(
            runstats.summary(),
            time.perf_counter() - _t_start,
            cache.stats(),
            cache_enabled=use_cache,
        )
    )

    console.print(ui.reports_panel(written))

    findings = report["findings"]

    # With --pr, end in the interactive push page: serve the report locally and
    # let the user select findings (and, with --align, gaps and verdicts) to post
    # to the PR. Without --pr, just hint how to push later.
    if pr_id:
        if not findings and not report.get("alignment"):
            console.print("[yellow]Nothing to push (no findings, no alignment).[/]")
        else:
            _serve_push(
                report, azure_client, pr_id, azure_org, azure_project, azure_repo_name,
                port=port, no_browser=no_browser,
            )
    elif findings and out_format in ("json", "both", "all"):
        # Pushing needs report.json, so only hint when JSON was written.
        report_json = out_dir / REPORT_JSON_FILENAME
        console.print(
            f"[dim]Post the {len(findings)} finding(s) as PR comments with:[/] "
            f"[cyan]pr-sentinel push-azure --pr <id> --report {report_json}[/]"
        )
