"""Markdown rendering of a PR Sentinel report.

The report is built (and shared helpers live) in the package ``__init__``;
this module only turns a report dict into the GitHub-flavoured Markdown that
gets pasted into PR descriptions.
"""
from pr_sentinel.report_generator import (
    _agent_summary_data,
    _key_findings,
    _key_recommendations,
    _merge_verdict,
)


def _md_cell(s: str) -> str:
    """Make a string safe for a markdown table cell."""
    return s.replace("|", "\\|").replace("\n", " ").strip()


def _findings_summary_rows(report: dict) -> list[str]:
    data_rows, totals = _agent_summary_data(report)

    rows: list[str] = []
    rows.append("| Agent | Status | Total | High | Medium | Low |")
    rows.append("|---|---|---:|---:|---:|---:|")

    for r in data_rows:
        if r["status"] == "FAILED":
            rows.append(f"| {r['agent']} | FAILED | — | — | — | — |")
            continue
        rows.append(
            f"| {r['agent']} | OK | {r['total']} | {r['high']} | {r['medium']} | {r['low']} |"
        )

    rows.append(
        f"| **TOTAL** | | **{totals['total']}** | **{totals['high']}** | "
        f"**{totals['medium']}** | **{totals['low']}** |"
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
