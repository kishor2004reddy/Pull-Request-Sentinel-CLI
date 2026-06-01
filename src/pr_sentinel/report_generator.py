import json
from datetime import datetime, timezone
from pathlib import Path

from pr_sentinel.config import (
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
) -> dict:
    raw_findings: list[dict] = []
    failed_agents: list[str] = []
    for r in agent_results:
        raw_findings.extend(r.get("findings", []))
        if r.get("failed"):
            failed_agents.append(r["agent"])

    all_findings = list(cleaned_findings) if cleaned_findings is not None else raw_findings
    all_findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 99), f["file"]))

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


def _md_cell(s: str) -> str:
    """Make a string safe for a markdown table cell."""
    return s.replace("|", "\\|").replace("\n", " ").strip()


def _findings_summary_rows(report: dict) -> list[str]:
    findings = report["findings"]
    agents_executed = report["agentsExecuted"]
    failed_agents = set(report.get("failedAgents") or [])

    by_agent: dict[str, list[dict]] = {a: [] for a in agents_executed}
    for f in findings:
        by_agent.setdefault(f["agent"], []).append(f)

    rows: list[str] = []
    rows.append("| Agent | Status | Total | High | Medium | Low |")
    rows.append("|---|---|---:|---:|---:|---:|")

    total = high = medium = low = 0
    for agent in agents_executed:
        if agent in failed_agents:
            rows.append(f"| {agent} | FAILED | — | — | — | — |")
            continue
        af = by_agent.get(agent, [])
        f_high = sum(1 for f in af if f["severity"] == "High")
        f_med = sum(1 for f in af if f["severity"] == "Medium")
        f_low = sum(1 for f in af if f["severity"] == "Low")
        total += len(af)
        high += f_high
        medium += f_med
        low += f_low
        rows.append(
            f"| {agent} | OK | {len(af)} | {f_high} | {f_med} | {f_low} |"
        )

    rows.append(
        f"| **TOTAL** | | **{total}** | **{high}** | **{medium}** | **{low}** |"
    )
    return rows


def _render_markdown(report: dict) -> str:
    lines: list[str] = []
    findings = report["findings"]
    risk = report["riskLevel"]

    counts = {"High": 0, "Medium": 0, "Low": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    raw_count = report.get("rawFindingCount")
    breakdown = (
        f"{len(findings)} total · {counts['High']} High · "
        f"{counts['Medium']} Medium · {counts['Low']} Low"
        if findings
        else "0 findings"
    )
    if raw_count is not None and raw_count != len(findings):
        breakdown += f" (cleaned from {raw_count} raw)"

    lines.append("# PR Sentinel Review Report")
    lines.append("")
    lines.append(f"> **Risk Level: {risk}** — {report['summary']}")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    coverage = "Yes" if report.get("coverageComplete", True) else "No — see failed agents"
    lines.append(f"| Risk Level | **{risk}** |")
    lines.append(f"| Coverage complete | {coverage} |")
    lines.append(f"| Source | `{report['source']}` |")
    lines.append(f"| Base branch | `{report['baseBranch']}` |")
    lines.append(f"| Reviewed at | {report['reviewedAt']} |")
    lines.append(f"| Agents | {', '.join(report['agentsExecuted'])} |")
    failed = report.get("failedAgents") or []
    if failed:
        lines.append(f"| Failed agents | {', '.join(failed)} |")
    lines.append(f"| Findings | {breakdown} |")
    lines.append("")

    lines.append("## Merge Verdict")
    lines.append("")
    verdict_text = _merge_verdict(report).replace("\n", "\n> ")
    lines.append(f"> {verdict_text}")
    lines.append("")

    lines.append("## Key Findings")
    lines.append("")
    key = _key_findings(report)
    if not key:
        lines.append("_No findings._")
        lines.append("")
    else:
        lines.append("| # | Severity | File | Location | Issue | Agent |")
        lines.append("|---|---|---|---|---|---|")
        for i, f in enumerate(key, 1):
            loc = f.get("lineHint") or ""
            loc_cell = f"`{_md_cell(loc)}`" if loc else "—"
            lines.append(
                f"| {i} | **{f['severity']}** | `{_md_cell(f['file'])}` | "
                f"{loc_cell} | {_md_cell(f['issue'])} | {_md_cell(f['agent'])} |"
            )
        lines.append("")

    lines.append("## Key Recommendations")
    lines.append("")
    recs = _key_recommendations(report)
    if not recs:
        lines.append("_No recommendations._")
        lines.append("")
    else:
        for i, f in enumerate(recs, 1):
            lines.append(f"{i}. **`{f['file']}`** — {f['recommendation']}")
        lines.append("")

    lines.append("## All Findings")
    lines.append("")
    lines.extend(_findings_summary_rows(report))
    lines.append("")
    if not findings:
        lines.append("_No findings._")
        return "\n".join(lines) + "\n"

    SEVERITY_LABELS = [
        ("High",   "🔴 High Severity"),
        ("Medium", "🟡 Medium Severity"),
        ("Low",    "🔵 Low Severity"),
    ]

    for severity, heading in SEVERITY_LABELS:
        sev_findings = [f for f in findings if f["severity"] == severity]
        if not sev_findings:
            continue

        lines.append(f"### {heading}  _({len(sev_findings)} finding(s))_")
        lines.append("")

        # Group by agent, preserving declared agent order
        by_agent: dict[str, list[dict]] = {}
        for f in sev_findings:
            by_agent.setdefault(f["agent"], []).append(f)

        for agent_name in report["agentsExecuted"]:
            agent_findings = by_agent.get(agent_name, [])
            if not agent_findings:
                continue

            lines.append(f"#### {agent_name}  _({len(agent_findings)} finding(s))_")
            lines.append("")

            for i, f in enumerate(agent_findings, start=1):
                location = f" · line `{f['lineHint']}`" if f.get("lineHint") else ""
                lines.append(f"##### {i}. `{f['file']}`{location}")
                lines.append("")
                lines.append(f"**Issue.** {f['issue']}")
                lines.append("")
                if f.get("reasoning"):
                    reasoning = f["reasoning"]
                    if "\n" in reasoning:
                        lines.append("**Reasoning.**")
                        lines.append("")
                        lines.append(reasoning)
                    else:
                        lines.append(f"**Reasoning.** {reasoning}")
                    lines.append("")
                if f.get("recommendation"):
                    rec = f["recommendation"]
                    if "\n" in rec:
                        lines.append("**Recommendation.**")
                        lines.append("")
                        lines.append(rec)
                    else:
                        lines.append(f"**Recommendation.** {rec}")
                    lines.append("")

    return "\n".join(lines) + "\n"
