"""PR Sentinel report building and rendering.

`build_report()` assembles the report dict; the JSON/Markdown/HTML writers
render and persist it. The two human-readable renderers live in sibling
modules — :mod:`markdown` and :mod:`html` — while the report-level helpers they
share stay here. The renderers are imported at the bottom of this module to
keep the shared helpers defined first and avoid a circular import.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pr_sentinel.config import (
    ALIGNMENT_REPORT_HTML_FILENAME,
    ALIGNMENT_REPORT_JSON_FILENAME,
    REPORT_HTML_FILENAME,
    REPORT_JSON_FILENAME,
    REPORT_MARKDOWN_FILENAME,
    SEVERITY_ORDER,
)


def _risk_level(findings: list[dict]) -> str:
    severities = [f["severity"] for f in findings]
    if "High" in severities:
        return "High"
    if severities.count("Medium") >= 5:
        return "High"
    if "Medium" in severities:
        return "Medium"
    if severities:
        return "Low"
    return "None"


def _assign_finding_ids(findings: list[dict]) -> None:
    """Give each finding a stable short `id`, in place.

    The id is derived from the finding's content (agent + file + issue) so the
    HTML report and report.json agree on it, and so the push server can map a
    browser selection back to the exact finding regardless of list order. A
    suffix disambiguates the rare case of two identical findings.
    """
    seen: dict[str, int] = {}
    for f in findings:
        seed = f"{f.get('agent')}|{f.get('file')}|{f.get('issue')}"
        base = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
        n = seen.get(base, 0)
        seen[base] = n + 1
        f["id"] = base if n == 0 else f"{base}-{n}"


def _summary_text(
    findings: list[dict],
    agents_executed: list[str],
    failed_agents: list[str] | None = None,
) -> str:
    failed_agents = failed_agents or []
    successful = len(agents_executed) - len(failed_agents)
    if not findings:
        base = f"No issues found across {successful} agent(s)."
    else:
        counts: dict[str, int] = {}
        for f in findings:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        parts = [f"{counts[s]} {s}" for s in ("High", "Medium", "Low") if s in counts]
        base = f"{len(findings)} finding(s): {', '.join(parts)}."
    if failed_agents:
        base += f" Failed: {', '.join(failed_agents)}."
    return base


def build_report(
    agent_results: list[dict],
    base_branch: str,
    source: str,
    cleaned_findings: list[dict] | None = None,
    repo_root: str | None = None,
) -> dict:
    raw_findings: list[dict] = []
    failed_agents: list[str] = []
    for r in agent_results:
        raw_findings.extend(r.get("findings", []))
        if r.get("failed"):
            failed_agents.append(r["agent"])

    all_findings = list(cleaned_findings) if cleaned_findings is not None else raw_findings
    all_findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 99), f["file"]))
    _assign_finding_ids(all_findings)

    agents_executed = [r["agent"] for r in agent_results]

    coverage_complete = len(failed_agents) == 0

    risk = _risk_level(all_findings)
    if failed_agents and len(failed_agents) == len(agent_results):
        risk = "Unknown"
    elif failed_agents and risk == "None":
        # Some agents ran but found nothing — can't call it clean with partial coverage.
        risk = "Unknown"

    report = {
        "tool": "PR Sentinel",
        "baseBranch": base_branch,
        "source": source,
        "reviewedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "riskLevel": risk,
        "coverageComplete": coverage_complete,
        "summary": _summary_text(all_findings, agents_executed, failed_agents),
        "agentsExecuted": agents_executed,
        "failedAgents": failed_agents,
        "repoRoot": repo_root,
        "findings": all_findings,
    }
    if cleaned_findings is not None:
        report["rawFindingCount"] = len(raw_findings)
    return report


def write_json(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / REPORT_JSON_FILENAME
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def write_markdown(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / REPORT_MARKDOWN_FILENAME
    path.write_text(_render_markdown(report), encoding="utf-8")
    return path


def write_html(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / REPORT_HTML_FILENAME
    path.write_text(_render_html(report), encoding="utf-8")
    return path


def write_alignment_json(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ALIGNMENT_REPORT_JSON_FILENAME
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def write_alignment_html(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ALIGNMENT_REPORT_HTML_FILENAME
    path.write_text(_render_alignment_html(report), encoding="utf-8")
    return path


# --- Report-level helpers shared by the renderers ---------------------------

def _merge_verdict(report: dict) -> str:
    risk = report["riskLevel"]
    findings = report["findings"]
    high = sum(1 for f in findings if f["severity"] == "High")
    medium = sum(1 for f in findings if f["severity"] == "Medium")

    if risk == "Unknown":
        failed = report.get("failedAgents", [])
        all_failed = len(failed) == len(report.get("agentsExecuted", failed))
        if all_failed:
            return (
                f"Risk level could not be determined: all {len(failed)} review agent(s) failed to complete "
                f"({', '.join(failed)}). Inspect the error output from the CLI run, fix the underlying cause "
                f"(e.g. invalid --model name, missing Claude Code auth), and re-run the review."
            )
        return (
            f"Risk level could not be confirmed: {len(failed)} agent(s) failed to complete "
            f"({', '.join(failed)}) and the remaining agents found no issues — but coverage was incomplete. "
            f"Re-run the review after resolving the failure to confirm this PR is clean."
        )

    if risk == "High":
        return (
            f"Do not raise this PR or merge as-is. The review surfaced {high} High-severity "
            f"issue(s) that represent real risk (e.g. exposed secrets, auth gaps, or unsafe data handling). "
            f"Resolve every High finding and re-run the review before opening the PR."
        )
    if risk == "Medium":
        return (
            f"This PR is not yet ready to merge. The review found {medium} Medium-severity issue(s) "
            f"that should be addressed before review by teammates. It is acceptable to raise the PR for "
            f"discussion, but flag these items in the PR description and resolve them before requesting approval."
        )
    if risk == "Low":
        return (
            "This PR is safe to raise and is broadly mergeable. Only minor hygiene or defense-in-depth "
            "issues were found; address them when convenient or note them as follow-ups. No blockers."
        )
    return (
        "No issues were found across the executed agents. The PR appears safe to raise and merge "
        "from the perspective of this automated review. Human review is still recommended for design and intent."
    )


def _key_findings(report: dict, limit: int = 5) -> list[dict]:
    findings = report["findings"]
    blocking = [f for f in findings if f["severity"] in ("High", "Medium")]
    pool = blocking if blocking else findings
    return pool[:limit]


def _key_recommendations(report: dict, limit: int = 5) -> list[dict]:
    seen: set[str] = set()
    recs: list[dict] = []
    for f in _key_findings(report, limit=len(report["findings"])):
        rec = f.get("recommendation", "").strip()
        if not rec or rec in seen:
            continue
        seen.add(rec)
        recs.append(f)
        if len(recs) >= limit:
            break
    return recs


def _agent_summary_data(report: dict) -> tuple[list[dict], dict]:
    """Per-agent finding counts plus a TOTAL row, shared by all renderers.

    Returns (rows, totals) where each row is
    {agent, status, total, high, medium, low} (counts are None when FAILED).
    """
    findings = report["findings"]
    agents_executed = report["agentsExecuted"]
    failed_agents = set(report.get("failedAgents") or [])

    by_agent: dict[str, list[dict]] = {a: [] for a in agents_executed}
    for f in findings:
        by_agent.setdefault(f["agent"], []).append(f)

    rows: list[dict] = []
    total = high = medium = low = 0
    for agent in agents_executed:
        if agent in failed_agents:
            rows.append({"agent": agent, "status": "FAILED",
                         "total": None, "high": None, "medium": None, "low": None})
            continue
        af = by_agent.get(agent, [])
        f_high = sum(1 for f in af if f["severity"] == "High")
        f_med = sum(1 for f in af if f["severity"] == "Medium")
        f_low = sum(1 for f in af if f["severity"] == "Low")
        total += len(af)
        high += f_high
        medium += f_med
        low += f_low
        rows.append({"agent": agent, "status": "OK", "total": len(af),
                     "high": f_high, "medium": f_med, "low": f_low})

    totals = {"total": total, "high": high, "medium": medium, "low": low}
    return rows, totals


# The renderers depend on the helpers above, so import them last. This also
# re-exports the render functions on the package for the writers and tests.
from pr_sentinel.report_generator.markdown import _render_markdown  # noqa: E402
from pr_sentinel.report_generator.html import (  # noqa: E402
    _render_alignment_html,
    _render_html,
    _rich_text_html,
)

__all__ = [
    "build_report",
    "write_json",
    "write_markdown",
    "write_html",
    "write_alignment_json",
    "write_alignment_html",
]
