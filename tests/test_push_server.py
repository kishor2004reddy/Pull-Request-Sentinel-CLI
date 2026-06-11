from pr_sentinel import push_server
from pr_sentinel.integrations.azure_devops import AzureDevOpsError


class FakeClient:
    def __init__(self, already=None, fail_ids=None):
        self._already = set(already or [])
        self._fail_ids = set(fail_ids or [])
        self.created = []

    def list_thread_finding_ids(self, pr_id):
        return set(self._already)

    def create_pr_thread(self, pr_id, content, finding_id=None,
                         file_path=None, line=None):
        if finding_id in self._fail_ids:
            raise AzureDevOpsError("boom")
        self.created.append((pr_id, finding_id, content, file_path, line))
        return {"id": 1}


def _findings():
    return {
        "a": {"id": "a", "agent": "Sec", "severity": "High", "file": "f.py",
              "lineHint": "+42", "issue": "x"},
        "b": {"id": "b", "agent": "Q", "severity": "Low", "file": "g.py", "issue": "y"},
    }


def test_push_creates_thread_per_finding():
    client = FakeClient()
    results = push_server._push_findings(client, 7, _findings(), ["a", "b"])
    assert all(r["ok"] for r in results)
    assert {fid for _pr, fid, *_ in client.created} == {"a", "b"}


def test_push_passes_line_and_path_when_hint_present():
    client = FakeClient()
    push_server._push_findings(client, 7, _findings(), ["a", "b"])
    by_id = {fid: (path, line) for _pr, fid, _c, path, line in client.created}
    # "a" has lineHint "+42" -> pinned to the line; "b" has none -> PR-level.
    assert by_id["a"] == ("f.py", 42)
    assert by_id["b"] == ("g.py", None)


def test_push_skips_already_posted_findings():
    client = FakeClient(already=["a"])
    results = push_server._push_findings(client, 7, _findings(), ["a", "b"])
    by_id = {r["id"]: r for r in results}
    assert by_id["a"]["ok"] and by_id["a"].get("skipped")
    assert by_id["b"]["ok"] and not by_id["b"].get("skipped")
    # Only the not-yet-posted finding is actually created.
    assert [fid for _pr, fid, *_ in client.created] == ["b"]


def test_push_reports_unknown_and_failed_findings():
    client = FakeClient(fail_ids=["a"])
    results = push_server._push_findings(client, 7, _findings(), ["a", "missing"])
    by_id = {r["id"]: r for r in results}
    assert by_id["a"]["ok"] is False and "boom" in by_id["a"]["error"]
    assert by_id["missing"]["ok"] is False and "unknown" in by_id["missing"]["error"]
