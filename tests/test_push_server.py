from pr_sentinel import push_server
from pr_sentinel.integrations.azure_devops import AzureDevOpsError


class FakeClient:
    def __init__(self, already=None, fail_ids=None, aligned=None):
        self._already = set(already or [])
        self._fail_ids = set(fail_ids or [])
        self._aligned = set(aligned or [])
        self.created = []
        self.upserted = []

    def list_thread_finding_ids(self, pr_id):
        return set(self._already)

    def list_alignment_work_item_ids(self, pr_id):
        return set(self._aligned)

    def create_pr_thread(self, pr_id, content, finding_id=None,
                         file_path=None, line=None):
        if finding_id in self._fail_ids:
            raise AzureDevOpsError("boom")
        self.created.append((pr_id, finding_id, content, file_path, line))
        return {"id": 1}

    def upsert_alignment_comment(self, pr_id, work_item_id, content):
        self.upserted.append((pr_id, work_item_id, content))
        return {"id": 1}


def _findings():
    return {
        "a": {"id": "a", "agent": "Sec", "severity": "High", "file": "f.py",
              "lineHint": "+42", "issue": "x"},
        "b": {"id": "b", "agent": "Q", "severity": "Low", "file": "g.py", "issue": "y"},
    }


def _alignment_by_id():
    return {
        "align:1234": {
            "workItem": {"id": 1234, "type": "User Story", "state": "Active",
                         "title": "Add export"},
            "verdict": "Partial",
            "confidence": "High",
            "summary": "gap",
            "criteria": [{"criterion": "Button", "status": "Met"}],
        }
    }


def test_push_creates_thread_per_finding():
    client = FakeClient()
    results = push_server._push_items(client, 7, _findings(), {}, ["a", "b"])
    assert all(r["ok"] for r in results)
    assert {fid for _pr, fid, *_ in client.created} == {"a", "b"}


def test_push_passes_line_and_path_when_hint_present():
    client = FakeClient()
    push_server._push_items(client, 7, _findings(), {}, ["a", "b"])
    by_id = {fid: (path, line) for _pr, fid, _c, path, line in client.created}
    # "a" has lineHint "+42" -> pinned to the line; "b" has none -> PR-level.
    assert by_id["a"] == ("f.py", 42)
    assert by_id["b"] == ("g.py", None)


def test_push_skips_already_posted_findings():
    client = FakeClient(already=["a"])
    results = push_server._push_items(client, 7, _findings(), {}, ["a", "b"])
    by_id = {r["id"]: r for r in results}
    assert by_id["a"]["ok"] and by_id["a"].get("skipped")
    assert by_id["b"]["ok"] and not by_id["b"].get("skipped")
    # Only the not-yet-posted finding is actually created.
    assert [fid for _pr, fid, *_ in client.created] == ["b"]


def test_push_reports_unknown_and_failed_findings():
    client = FakeClient(fail_ids=["a"])
    results = push_server._push_items(client, 7, _findings(), {}, ["a", "missing"])
    by_id = {r["id"]: r for r in results}
    assert by_id["a"]["ok"] is False and "boom" in by_id["a"]["error"]
    assert by_id["missing"]["ok"] is False and "unknown" in by_id["missing"]["error"]


def test_push_routes_alignment_ids_to_upsert():
    client = FakeClient()
    results = push_server._push_items(
        client, 7, _findings(), _alignment_by_id(), ["align:1234", "a"]
    )
    by_id = {r["id"]: r for r in results}
    # Verdict went to upsert (reported as updated), gap went to a thread.
    assert by_id["align:1234"]["ok"] and by_id["align:1234"].get("updated")
    assert client.upserted == [(7, 1234, client.upserted[0][2])]
    assert [fid for _pr, fid, *_ in client.created] == ["a"]


def test_push_alignment_content_is_markdown_verdict():
    client = FakeClient()
    push_server._push_items(client, 7, {}, _alignment_by_id(), ["align:1234"])
    content = client.upserted[0][2]
    assert "Requirement Alignment" in content
    assert "Alignment: Partial" in content


import urllib.request


def _served_html(report) -> str:
    client = FakeClient()
    url, httpd = push_server.start_server(
        report, client, 7, port=0, open_browser=False
    )
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.read().decode("utf-8")
    finally:
        httpd.shutdown()
        httpd.server_close()


def _alignment_section():
    return {
        "workItem": {"id": 1234, "type": "User Story", "state": "Active",
                     "title": "Add export"},
        "verdict": "Partial", "confidence": "High", "summary": "gap",
        "criteria": [{"criterion": "Button", "status": "Met", "evidence": "x"}],
    }


def test_server_uses_combined_renderer_when_findings_and_alignment():
    report = {
        "tool": "PR Sentinel", "baseBranch": "main", "source": "branch:main...HEAD",
        "reviewedAt": "2026-06-12T00:00:00+00:00", "riskLevel": "High",
        "coverageComplete": True, "summary": "1 finding(s): 1 High.",
        "agentsExecuted": ["Security Agent", "Alignment Agent"], "failedAgents": [],
        "repoRoot": None,
        "findings": [{"id": "x", "agent": "Security Agent", "severity": "High",
                      "file": "f.py", "lineHint": "+1", "issue": "bad"}],
        "alignment": [_alignment_section()],
    }
    html = _served_html(report)
    assert "Review + Alignment" in html          # combined renderer header
    assert "Code Review Findings" in html
    assert "verdict-badge" in html
    assert html.count('id="push-bar"') == 1


def test_server_uses_alignment_renderer_when_only_alignment():
    report = {
        "tool": "PR Sentinel", "baseBranch": "main", "source": "alignment:PR#7",
        "reviewedAt": "2026-06-12T00:00:00+00:00", "riskLevel": "None",
        "coverageComplete": True, "summary": "No issues.",
        "agentsExecuted": ["Alignment Agent"], "failedAgents": [], "repoRoot": None,
        "findings": [],
        "alignment": [_alignment_section()],
    }
    html = _served_html(report)
    assert "Requirement Alignment" in html
    assert "Review + Alignment" not in html      # not the combined page
