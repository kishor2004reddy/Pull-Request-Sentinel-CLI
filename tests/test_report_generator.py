from pr_sentinel import report_generator


def _finding(severity: str, file: str = "a.py", recommendation: str = "fix it") -> dict:
    return {
        "agent": "Security Agent",
        "severity": severity,
        "file": file,
        "lineHint": "+10",
        "issue": "something is wrong",
        "reasoning": "because reasons",
        "recommendation": recommendation,
    }


def _result(findings: list[dict]) -> dict:
    return {"agent": "Security Agent", "findings": findings}


def test_empty_findings_yields_none_risk_and_safe_verdict():
    report = report_generator.build_report(
        agent_results=[_result([])],
        base_branch="main",
        source="branch:main",
    )
    assert report["riskLevel"] == "None"
    md = report_generator._render_markdown(report)
    assert "## Summary" in md
    assert "## Merge Verdict" in md
    assert "safe to" in md.lower()


def test_one_high_finding_blocks_with_high_risk():
    report = report_generator.build_report(
        agent_results=[_result([_finding("High")])],
        base_branch="main",
        source="branch:main",
    )
    assert report["riskLevel"] == "High"
    md = report_generator._render_markdown(report)
    assert "Do not raise" in md


def test_two_mediums_yield_medium_risk():
    report = report_generator.build_report(
        agent_results=[_result([_finding("Medium"), _finding("Medium")])],
        base_branch="main",
        source="branch:main",
    )
    assert report["riskLevel"] == "Medium"
    md = report_generator._render_markdown(report)
    assert "not yet ready" in md


def test_only_low_yields_low_risk_and_safe_to_raise():
    report = report_generator.build_report(
        agent_results=[_result([_finding("Low")])],
        base_branch="main",
        source="branch:main",
    )
    assert report["riskLevel"] == "Low"
    md = report_generator._render_markdown(report)
    assert "safe to raise" in md


def test_markdown_always_emits_all_five_sections_in_order():
    report = report_generator.build_report(
        agent_results=[_result([])],
        base_branch="main",
        source="branch:main",
    )
    md = report_generator._render_markdown(report)
    sections = ["## Summary", "## Merge Verdict", "## Key Findings", "## Key Recommendations", "## All Findings"]
    positions = [md.index(s) for s in sections]
    assert positions == sorted(positions), "Sections must appear in fixed order"


def test_findings_sorted_high_first_then_by_file():
    findings = [
        _finding("Low", "z.py"),
        _finding("High", "b.py"),
        _finding("Medium", "a.py"),
        _finding("High", "a.py"),
    ]
    report = report_generator.build_report(
        agent_results=[_result(findings)],
        base_branch="main",
        source="branch:main",
    )
    severities = [f["severity"] for f in report["findings"]]
    assert severities == ["High", "High", "Medium", "Low"]
    # Within High, sorted by file path
    high_files = [f["file"] for f in report["findings"] if f["severity"] == "High"]
    assert high_files == ["a.py", "b.py"]


def test_key_recommendations_deduplicated():
    findings = [
        _finding("High", "a.py", recommendation="same fix"),
        _finding("Medium", "b.py", recommendation="same fix"),
        _finding("Medium", "c.py", recommendation="other fix"),
    ]
    report = report_generator.build_report(
        agent_results=[_result(findings)],
        base_branch="main",
        source="branch:main",
    )
    recs = report_generator._key_recommendations(report)
    rec_texts = [r["recommendation"] for r in recs]
    assert rec_texts == ["same fix", "other fix"]
