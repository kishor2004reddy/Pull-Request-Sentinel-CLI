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


def test_write_html_creates_file(tmp_path):
    report = report_generator.build_report(
        agent_results=[_result([_finding("High")])],
        base_branch="main",
        source="branch:main",
    )
    path = report_generator.write_html(report, tmp_path)
    assert path.exists()
    assert path.name.endswith(".html")
    html = path.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "Risk: High" in html
    assert "Do not raise" in html


def test_html_escapes_finding_text():
    findings = [_finding("High", file="<script>x</script>.py")]
    findings[0]["issue"] = "broken <b>tag</b> & ampersand"
    report = report_generator.build_report(
        agent_results=[_result(findings)],
        base_branch="main",
        source="branch:main",
    )
    html = report_generator._render_html(report)
    assert "<script>x</script>.py" not in html
    assert "&lt;script&gt;" in html
    assert "&amp; ampersand" in html


def test_html_empty_findings_shows_no_findings():
    report = report_generator.build_report(
        agent_results=[_result([])],
        base_branch="main",
        source="branch:main",
    )
    html = report_generator._render_html(report)
    assert html.startswith("<!DOCTYPE html>")
    assert "No findings" in html
    assert "Merge Verdict" in html


def test_agent_summary_data_matches_markdown_totals():
    findings = [_finding("High"), _finding("Medium"), _finding("Low")]
    report = report_generator.build_report(
        agent_results=[_result(findings)],
        base_branch="main",
        source="branch:main",
    )
    _rows, totals = report_generator._agent_summary_data(report)
    assert totals == {"total": 3, "high": 1, "medium": 1, "low": 1}


def test_build_report_assigns_stable_finding_ids():
    findings = [_finding("High", "a.py"), _finding("High", "b.py")]
    report = report_generator.build_report(
        agent_results=[_result(findings)],
        base_branch="main",
        source="branch:main",
    )
    ids = [f["id"] for f in report["findings"]]
    assert all(ids)  # every finding has a non-empty id
    assert len(set(ids)) == len(ids)  # ids are unique


def test_identical_findings_get_disambiguated_ids():
    findings = [_finding("High", "a.py"), _finding("High", "a.py")]
    report = report_generator.build_report(
        agent_results=[_result(findings)],
        base_branch="main",
        source="branch:main",
    )
    ids = [f["id"] for f in report["findings"]]
    assert len(set(ids)) == 2


def test_html_includes_push_ui_and_config_placeholder():
    from pr_sentinel.config import PUSH_CONFIG_PLACEHOLDER

    report = report_generator.build_report(
        agent_results=[_result([_finding("High")])],
        base_branch="main",
        source="branch:main",
    )
    html = report_generator._render_html(report)
    assert PUSH_CONFIG_PLACEHOLDER in html
    assert 'class="pick"' in html
    assert 'id="push-btn"' in html
    fid = report["findings"][0]["id"]
    assert f'data-finding-id="{fid}"' in html


def _alignment_report(criteria: list[dict], findings: list[dict] | None = None) -> dict:
    findings = findings or []
    report = report_generator.build_report(
        agent_results=[{"agent": "Alignment Agent", "findings": findings}],
        base_branch="main",
        source="alignment:PR#42",
    )
    report["alignment"] = [
        {
            "workItem": {"id": 1234, "type": "User Story", "state": "Active",
                         "title": "Add CSV export"},
            "verdict": "Partial",
            "confidence": "High",
            "summary": "Export works but empty-list is missing.",
            "truncatedDiff": False,
            "criteria": criteria,
        }
    ]
    return report


def test_render_alignment_html_has_verdict_matrix_and_gaps():
    criteria = [
        {"criterion": "Export downloads a CSV", "status": "Met", "evidence": "Export()"},
        {"criterion": "Empty list shows a message", "status": "Not met", "evidence": "missing"},
        {"criterion": "Localized in French", "status": "Unverifiable", "evidence": "not in diff"},
    ]
    findings = [_finding("High", "a.cs", recommendation="handle empty list")]
    html = report_generator._render_alignment_html(_alignment_report(criteria, findings))

    assert "Requirement Alignment" in html
    assert "verdict-badge" in html and "Partial" in html
    assert 'class="trace"' in html              # traceability matrix
    assert "cov-bar" in html                    # coverage bar
    assert "Export downloads a CSV" in html
    assert "Empty list shows a message" in html
    assert "Gaps (1)" in html
    assert "handle empty list" in html


def test_render_alignment_html_no_criteria_notes_coarse_item():
    html = report_generator._render_alignment_html(_alignment_report([]))
    assert "No acceptance criteria" in html
    # No criteria → still renders, with an empty Gaps section.
    assert "Gaps (0)" in html


def _combined_report() -> dict:
    """A report shaped like `review --align` produces: code findings + alignment
    gap findings merged into `findings`, plus `alignment` verdict sections."""
    code = _finding("High", "svc.py", recommendation="sanitize input")
    gap = {
        "agent": "Alignment Agent",
        "severity": "Medium",
        "file": "(requirement)",
        "lineHint": "",
        "issue": "Empty-list message is missing",
        "reasoning": "criterion not satisfied",
        "recommendation": "show a message when the list is empty",
    }
    report = report_generator.build_report(
        agent_results=[
            {"agent": "Security Agent", "findings": [code]},
            {"agent": "Alignment Agent", "findings": [gap]},
        ],
        base_branch="main",
        source="branch:main...HEAD",
    )
    report["alignment"] = [
        {
            "workItem": {"id": 77, "type": "User Story", "state": "Active",
                         "title": "Add CSV export"},
            "verdict": "Partial",
            "confidence": "High",
            "summary": "Export works but empty-list is missing.",
            "truncatedDiff": False,
            "criteria": [
                {"criterion": "Export downloads a CSV", "status": "Met", "evidence": "Export()"},
                {"criterion": "Empty list shows a message", "status": "Not met", "evidence": "missing"},
            ],
        }
    ]
    return report


def test_render_combined_html_shows_review_and_alignment_once_each():
    report = _combined_report()
    html = report_generator._render_combined_html(report)

    assert html.startswith("<!DOCTYPE html>")
    assert "Review + Alignment" in html
    # Code review side
    assert "Code Review Findings" in html
    assert "sanitize input" in html
    # Alignment side
    assert "Requirement Alignment" in html
    assert "verdict-badge" in html and "Partial" in html
    assert 'class="trace"' in html
    assert "Empty list shows a message" in html
    assert "Gaps (1)" in html
    assert "show a message when the list is empty" in html

    # Exactly one push toolbar, and the gap finding renders once (not duplicated
    # between the code-review list and the gaps list). A single card carries its
    # id three times: on <details>, the checkbox, and the push-mark.
    assert html.count('id="push-bar"') == 1
    gap_id = next(f["id"] for f in report["findings"] if f["agent"] == "Alignment Agent")
    assert html.count(f'data-finding-id="{gap_id}"') == 3  # one card, not duplicated


def test_combined_html_has_a_checkbox_per_pushable_item():
    report = _combined_report()
    html = report_generator._render_combined_html(report)
    # One verdict checkbox (align:77) + one code finding + one gap finding.
    assert 'data-finding-id="align:77"' in html
    code_id = next(f["id"] for f in report["findings"] if f["agent"] == "Security Agent")
    assert f'data-finding-id="{code_id}"' in html


def test_overview_renders_pr_section_when_present():
    report = report_generator.build_report(
        agent_results=[_result([_finding("High")])],
        base_branch="main",
        source="branch:origin/main...origin/feature/x",
    )
    report["pr"] = {
        "id": 17, "org": "myorg", "project": "myproj", "repo": "myrepo",
        "title": "Add CSV export", "baseBranch": "main", "sourceBranch": "feature/x",
    }
    html = report_generator._render_html(report)
    assert "Pull Request" in html
    assert "#17" in html
    assert "myorg/myproj/myrepo" in html
    assert "feature/x" in html
    # PR number links to the Azure DevOps PR page.
    assert "dev.azure.com/myorg/myproj/_git/myrepo/pullrequest/17" in html


def test_overview_omits_pr_section_when_absent():
    report = report_generator.build_report(
        agent_results=[_result([_finding("Low")])],
        base_branch="main",
        source="branch:main",
    )
    assert "Pull Request" not in report_generator._render_html(report)


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
