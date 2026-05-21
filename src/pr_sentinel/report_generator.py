import json
from datetime import datetime, timezone
from pathlib import Path

SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}


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
) -> dict:
    all_findings: list[dict] = []
    failed_agents: list[str] = []
    for r in agent_results:
        all_findings.extend(r.get("findings", []))
        if r.get("failed"):
            failed_agents.append(r["agent"])

    all_findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 99), f["file"]))

    agents_executed = [r["agent"] for r in agent_results]

    risk = _risk_level(all_findings)
    if failed_agents and len(failed_agents) == len(agent_results):
        risk = "Unknown"

    return {
        "tool": "PR Sentinel",
        "baseBranch": base_branch,
        "source": source,
        "reviewedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "riskLevel": risk,
        "summary": _summary_text(all_findings, agents_executed, failed_agents),
        "agentsExecuted": agents_executed,
        "failedAgents": failed_agents,
        "findings": all_findings,
    }


def write_json(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def write_markdown(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "review-report.md"
    path.write_text(_render_markdown(report), encoding="utf-8")
    return path


def _merge_verdict(report: dict) -> str:
    risk = report["riskLevel"]
    findings = report["findings"]
    high = sum(1 for f in findings if f["severity"] == "High")
    medium = sum(1 for f in findings if f["severity"] == "Medium")

    if risk == "Unknown":
        failed = report.get("failedAgents", [])
        return (
            f"Risk level could not be determined: all {len(failed)} review agent(s) failed to complete "
            f"({', '.join(failed)}). Inspect the error output from the CLI run, fix the underlying cause "
            f"(e.g. invalid --model name, missing Claude Code auth), and re-run the review."
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


def _render_markdown(report: dict) -> str:
    lines: list[str] = []
    lines.append("# PR Sentinel Review Report")
    lines.append("")

    lines.append("## Summary")
    lines.append(f"- Risk Level: **{report['riskLevel']}**")
    lines.append(f"- Source: `{report['source']}`")
    lines.append(f"- Base branch: `{report['baseBranch']}`")
    lines.append(f"- Reviewed at: {report['reviewedAt']}")
    lines.append(f"- Agents: {', '.join(report['agentsExecuted'])}")
    failed = report.get("failedAgents") or []
    if failed:
        lines.append(f"- Failed agents: {', '.join(failed)}")
    lines.append(f"- {report['summary']}")
    lines.append("")

    lines.append("## Merge Verdict")
    lines.append("")
    lines.append(_merge_verdict(report))
    lines.append("")

    lines.append("## Key Findings")
    lines.append("")
    key = _key_findings(report)
    if not key:
        lines.append("_No findings._")
        lines.append("")
    else:
        for f in key:
            location = f" (`{f['lineHint']}`)" if f.get("lineHint") else ""
            lines.append(
                f"- **{f['severity']}** — `{f['file']}`{location} — {f['issue']} "
                f"_(from {f['agent']})_"
            )
        lines.append("")

    lines.append("## Key Recommendations")
    lines.append("")
    recs = _key_recommendations(report)
    if not recs:
        lines.append("_No recommendations._")
        lines.append("")
    else:
        for f in recs:
            lines.append(f"- `{f['file']}` — {f['recommendation']}")
        lines.append("")

    lines.append("## All Findings")
    lines.append("")
    findings = report["findings"]
    if not findings:
        lines.append("_No findings._")
        return "\n".join(lines) + "\n"

    for f in findings:
        lines.append(f"### {f['severity']} — {f['agent']}")
        lines.append(f"- File: `{f['file']}`")
        if f.get("lineHint"):
            lines.append(f"- Location: `{f['lineHint']}`")
        lines.append("")
        lines.append(f"**Issue:** {f['issue']}")
        lines.append("")
        if f.get("reasoning"):
            lines.append(f"**Reasoning:** {f['reasoning']}")
            lines.append("")
        if f.get("recommendation"):
            lines.append(f"**Recommendation:** {f['recommendation']}")
            lines.append("")

    return "\n".join(lines) + "\n"
