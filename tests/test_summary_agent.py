from pr_sentinel.agents.summary_agent import (
    SummaryAgent,
    _collapse_exact_duplicates,
)


def _finding(**kw):
    base = {
        "agent": "Security Agent",
        "severity": "Low",
        "file": "a.py",
        "lineHint": "1",
        "issue": "issue",
        "reasoning": "reason",
        "recommendation": "fix it",
    }
    base.update(kw)
    return base


def test_collapse_drops_byte_identical_findings():
    findings = [_finding(), _finding(), _finding(file="b.py")]
    reduced, removed = _collapse_exact_duplicates(findings)
    assert removed == 1
    assert len(reduced) == 2
    assert {f["file"] for f in reduced} == {"a.py", "b.py"}


def test_collapse_ignores_agent_and_keeps_highest_severity():
    # Same defect reported by two agents with different severities.
    findings = [
        _finding(agent="Security Agent", severity="Low"),
        _finding(agent="Code Quality Agent", severity="High"),
    ]
    reduced, removed = _collapse_exact_duplicates(findings)
    assert removed == 1
    assert len(reduced) == 1
    assert reduced[0]["severity"] == "High"


def test_collapse_keeps_distinct_findings():
    findings = [_finding(issue="x"), _finding(issue="y")]
    reduced, removed = _collapse_exact_duplicates(findings)
    assert removed == 0
    assert len(reduced) == 2


def test_run_skips_provider_when_collapse_leaves_one(monkeypatch):
    # Two byte-identical findings collapse to one — no provider call should fire.
    def _boom(*a, **k):
        raise AssertionError("provider must not be invoked")

    monkeypatch.setattr("pr_sentinel.agents.summary_agent.get_runner", _boom)
    cleaned, removed = SummaryAgent().run([_finding(), _finding()])
    assert removed == 1
    assert len(cleaned) == 1


def test_run_skips_provider_for_single_finding(monkeypatch):
    monkeypatch.setattr(
        "pr_sentinel.agents.summary_agent.get_runner",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no provider")),
    )
    cleaned, removed = SummaryAgent().run([_finding()])
    assert removed == 0
    assert len(cleaned) == 1