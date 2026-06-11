from pr_sentinel.agents.alignment_agent import AlignmentAgent, format_work_item
from pr_sentinel.integrations.azure_devops import WorkItem


def _wi(**kw):
    base = dict(
        id=1234,
        type="User Story",
        state="Active",
        title="Add CSV export",
        description="Users need CSV export.",
        criteria=["Button downloads a CSV", "CSV has headers"],
        repro_steps="",
    )
    base.update(kw)
    return WorkItem(**base)


def test_format_work_item_includes_type_title_and_criteria():
    text = format_work_item(_wi())
    assert "#1234" in text
    assert "User Story" in text
    assert "Add CSV export" in text
    assert "1. Button downloads a CSV" in text
    assert "2. CSV has headers" in text


def test_format_work_item_includes_repro_steps_for_bug():
    text = format_work_item(_wi(type="Bug", criteria=[], repro_steps="Click X, see crash"))
    assert "Repro Steps:" in text
    assert "Click X, see crash" in text


def test_validate_happy_path():
    agent = AlignmentAgent()
    result = agent._validate(
        {
            "verdict": "Partial",
            "confidence": "High",
            "summary": "Mostly there.",
            "criteria": [
                {"criterion": "Button downloads a CSV", "status": "Met", "evidence": "Export()"},
                {"criterion": "CSV has headers", "status": "Not met", "evidence": "no header row"},
            ],
            "findings": [
                {
                    "severity": "High",
                    "file": "a.cs",
                    "lineHint": "Export",
                    "issue": "headers missing",
                    "reasoning": "criterion unmet",
                    "recommendation": "write a header row",
                }
            ],
        }
    )
    assert result["verdict"] == "Partial"
    assert result["confidence"] == "High"
    assert len(result["criteria"]) == 2
    assert result["criteria"][1]["status"] == "Not met"
    assert len(result["findings"]) == 1
    assert result["findings"][0]["agent"] == "Alignment Agent"


def test_validate_clamps_unknown_enums():
    agent = AlignmentAgent()
    result = agent._validate(
        {
            "verdict": "totally-aligned",       # not a valid verdict
            "confidence": "medium",              # not a valid confidence
            "criteria": [{"criterion": "x", "status": "great"}],  # bad status
            "findings": [{"severity": "Critical", "issue": "y"}],  # bad severity
        }
    )
    assert result["verdict"] == "Unknown"
    assert result["confidence"] == "Low"
    assert result["criteria"][0]["status"] == "Unverifiable"
    assert result["findings"][0]["severity"] == "Medium"
    # No file given → default placeholder.
    assert result["findings"][0]["file"] == "(requirement)"


def test_validate_handles_non_dict_and_missing_lists():
    agent = AlignmentAgent()
    result = agent._validate({"verdict": "Satisfied"})
    assert result["verdict"] == "Satisfied"
    assert result["criteria"] == []
    assert result["findings"] == []


def test_validate_non_object_returns_unknown():
    agent = AlignmentAgent()
    result = agent._validate("not a dict")  # type: ignore[arg-type]
    assert result["verdict"] == "Unknown"
    assert result["failed"] is True
