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


# --- _validate: members-based consolidation (Rule 2/3 built in Python) -------

def test_validate_plain_survivor_restores_verbatim():
    original = [_finding(file="a.py", issue="x"), _finding(file="b.py", issue="y")]
    response = {"findings": [{"_id": 0}, {"_id": 1}]}
    out = SummaryAgent()._validate(response, original)
    assert [f["file"] for f in out] == ["a.py", "b.py"]
    assert out[0]["recommendation"] == "fix it"  # restored, not rebuilt


def test_validate_cross_file_consolidation_builds_multiple_files():
    original = [
        _finding(file="a.py", recommendation="validate a"),
        _finding(file="b.py", recommendation="validate b"),
        _finding(file="c.py", recommendation="validate c"),
    ]
    response = {"findings": [{"_id": 0, "members": [0, 1, 2]}]}
    out = SummaryAgent()._validate(response, original)
    assert len(out) == 1
    assert out[0]["file"] == "(multiple files)"
    assert out[0]["lineHint"] == ""
    rec = out[0]["recommendation"]
    assert rec.splitlines() == [
        "1. a.py — validate a",
        "2. b.py — validate b",
        "3. c.py — validate c",
    ]


def test_validate_same_file_consolidation_keys_by_line():
    original = [
        _finding(file="a.py", lineHint="10", recommendation="guard here"),
        _finding(file="a.py", lineHint="42", recommendation="guard there"),
    ]
    response = {"findings": [{"_id": 0, "members": [0, 1]}]}
    out = SummaryAgent()._validate(response, original)
    assert len(out) == 1
    assert out[0]["file"] == "a.py"
    assert out[0]["lineHint"] == "10"  # winner's line
    assert out[0]["recommendation"].splitlines() == [
        "1. 10 — guard here",
        "2. 42 — guard there",
    ]


def test_validate_winner_added_when_missing_from_members():
    original = [_finding(file="a.py"), _finding(file="b.py")]
    # model lists only member 1, but winner is 0 — winner must still be included
    response = {"findings": [{"_id": 0, "members": [1]}]}
    out = SummaryAgent()._validate(response, original)
    assert out[0]["file"] == "(multiple files)"
    assert len(out[0]["recommendation"].splitlines()) == 2