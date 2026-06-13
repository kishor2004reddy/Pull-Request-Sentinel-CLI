import pytest

from pr_sentinel.integrations import azure_devops
from pr_sentinel.integrations.azure_devops import (
    AzureDevOpsClient,
    AzureDevOpsError,
    WorkItem,
    _html_to_text,
    format_alignment_comment,
    format_finding_comment,
    line_from_hint,
    normalize_work_item,
    parse_remote,
)


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://dev.azure.com/myorg/myproj/_git/myrepo",
         ("myorg", "myproj", "myrepo")),
        ("https://myorg@dev.azure.com/myorg/myproj/_git/myrepo",
         ("myorg", "myproj", "myrepo")),
        ("https://dev.azure.com/myorg/myproj/_git/myrepo.git",
         ("myorg", "myproj", "myrepo")),
        ("https://myorg.visualstudio.com/myproj/_git/myrepo",
         ("myorg", "myproj", "myrepo")),
        ("https://myorg.visualstudio.com/DefaultCollection/myproj/_git/myrepo",
         ("myorg", "myproj", "myrepo")),
        ("git@ssh.dev.azure.com:v3/myorg/myproj/myrepo",
         ("myorg", "myproj", "myrepo")),
        ("myorg@vs-ssh.visualstudio.com:v3/myorg/myproj/myrepo",
         ("myorg", "myproj", "myrepo")),
    ],
)
def test_parse_remote_recognises_azure_formats(url, expected):
    assert parse_remote(url) == expected


def test_parse_remote_rejects_non_azure_remote():
    with pytest.raises(AzureDevOpsError):
        parse_remote("https://github.com/owner/repo.git")


def test_threads_url_is_well_formed():
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    url = client._threads_url(42)
    assert "/o/p/_apis/git/repositories/r/pullRequests/42/threads" in url
    assert "api-version=" in url


@pytest.mark.parametrize(
    "hint, expected",
    [("+42", 42), ("42", 42), ("+42,7", 42), ("", None), (None, None),
     ("no-number", None)],
)
def test_line_from_hint(hint, expected):
    assert line_from_hint(hint) == expected


def _capture_body(client):
    """Patch ``_request`` to record the POST body instead of calling Azure."""
    captured = {}

    def fake_request(method, url, body=None):
        captured["method"] = method
        captured["body"] = body
        return {"id": 1}

    client._request = fake_request
    return captured


def test_create_pr_thread_pins_to_line_when_file_and_line_given():
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    captured = _capture_body(client)
    client.create_pr_thread(7, "hi", finding_id="a", file_path="src/app.py", line=42)
    ctx = captured["body"]["threadContext"]
    # Azure needs a leading slash and 1-based right-side line/offset.
    assert ctx["filePath"] == "/src/app.py"
    assert ctx["rightFileStart"] == {"line": 42, "offset": 1}
    assert ctx["rightFileEnd"] == {"line": 42, "offset": 1}


def test_create_pr_thread_leading_slash_not_doubled():
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    captured = _capture_body(client)
    client.create_pr_thread(7, "hi", file_path="/src/app.py", line=1)
    assert captured["body"]["threadContext"]["filePath"] == "/src/app.py"


def test_create_pr_thread_falls_back_to_pr_level_without_line():
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    captured = _capture_body(client)
    client.create_pr_thread(7, "hi", finding_id="a", file_path="src/app.py", line=None)
    assert "threadContext" not in captured["body"]


def test_format_finding_comment_includes_all_sections():
    finding = {
        "agent": "Security Agent",
        "severity": "High",
        "file": "src/app.py",
        "lineHint": "+42",
        "issue": "Hard-coded secret",
        "reasoning": "Token committed to source",
        "recommendation": "Move to a secret store",
    }
    md = format_finding_comment(finding)
    assert "PR Sentinel — High" in md
    assert "Security Agent" in md
    assert "src/app.py" in md
    assert "Hard-coded secret" in md
    assert "Move to a secret store" in md


def test_format_finding_comment_omits_blank_fields():
    finding = {"agent": "A", "severity": "Low", "file": "f.py",
               "issue": "x", "reasoning": "", "recommendation": ""}
    md = format_finding_comment(finding)
    assert "Reasoning" not in md
    assert "Recommendation" not in md


# --- Work item fetching / normalization -------------------------------------

def test_html_to_text_strips_tags_and_keeps_block_breaks():
    html = "<div>First line.</div><p>Second &amp; third.</p><br>Fourth"
    text = _html_to_text(html)
    assert text == "First line.\nSecond & third.\nFourth"


def test_html_to_text_empty():
    assert _html_to_text(None) == ""
    assert _html_to_text("") == ""


def test_normalize_work_item_splits_criteria_and_strips_markers():
    raw = {
        "id": 99,
        "fields": {
            "System.Title": "Add export",
            "System.WorkItemType": "User Story",
            "System.State": "Active",
            "System.Description": "<p>Export orders.</p>",
            "Microsoft.VSTS.Common.AcceptanceCriteria":
                "<ul><li>Button works</li><li>CSV has headers</li></ul>",
        },
    }
    wi = normalize_work_item(raw)
    assert isinstance(wi, WorkItem)
    assert wi.id == 99
    assert wi.type == "User Story"
    assert wi.title == "Add export"
    assert wi.description == "Export orders."
    assert wi.criteria == ["Button works", "CSV has headers"]


def test_normalize_work_item_bug_carries_repro_steps():
    raw = {
        "id": 7,
        "fields": {
            "System.Title": "Crash on save",
            "System.WorkItemType": "Bug",
            "Microsoft.VSTS.TCM.ReproSteps": "<div>Open X, click Save, crash.</div>",
        },
    }
    wi = normalize_work_item(raw)
    assert wi.repro_steps == "Open X, click Save, crash."
    assert wi.criteria == []


def test_pr_work_items_and_work_items_urls_well_formed():
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    assert "/pullRequests/42/workitems" in client._pr_work_items_url(42)
    wi_url = client._work_items_url([1, 2], ("System.Title", "System.State"))
    assert "/_apis/wit/workitems?ids=1,2" in wi_url
    assert "fields=System.Title%2CSystem.State" in wi_url


def test_get_pr_work_items_parses_ids(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    monkeypatch.setattr(
        client, "_request",
        lambda *a, **k: {"value": [{"id": "10"}, {"id": "11"}, {"url": "no-id"}]},
    )
    assert client.get_pr_work_items(5) == [10, 11]


def test_get_work_items_normalizes(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    monkeypatch.setattr(
        client, "_request",
        lambda *a, **k: {"value": [
            {"id": 1, "fields": {"System.Title": "T", "System.WorkItemType": "Bug"}}
        ]},
    )
    items = client.get_work_items([1])
    assert len(items) == 1 and items[0].title == "T" and items[0].type == "Bug"


def test_get_work_items_empty_ids_skips_request():
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    # Should not raise even though _request would fail without a network.
    assert client.get_work_items([]) == []


# --- Pull request branch resolution -----------------------------------------

@pytest.mark.parametrize(
    "ref, expected",
    [("refs/heads/main", "main"),
     ("refs/heads/feature/azure-comment", "feature/azure-comment"),
     ("main", "main"), ("", ""), (None, "")],
)
def test_strip_ref(ref, expected):
    assert azure_devops._strip_ref(ref) == expected


def test_pull_request_url_well_formed():
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    url = client._pull_request_url(124)
    assert "/o/p/_apis/git/repositories/r/pullRequests/124" in url
    assert "/workitems" not in url
    assert "api-version=" in url


def test_get_pull_request_strips_branch_refs(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    monkeypatch.setattr(
        client, "_request",
        lambda *a, **k: {
            "sourceRefName": "refs/heads/feature/azure-comment",
            "targetRefName": "refs/heads/main",
            "title": "Add azure comment",
        },
    )
    pr = client.get_pull_request(124)
    assert pr.id == 124
    assert pr.source_branch == "feature/azure-comment"
    assert pr.target_branch == "main"
    assert pr.title == "Add azure comment"


# --- Alignment summary comment ----------------------------------------------

def test_format_alignment_comment_has_verdict_table_and_escapes_pipes():
    wi = WorkItem(id=1234, type="User Story", state="Active", title="Add export")
    result = {
        "verdict": "Partial",
        "confidence": "High",
        "summary": "Mostly there.",
        "criteria": [
            {"criterion": "Button | downloads CSV", "status": "Met"},
            {"criterion": "Empty list message", "status": "Not met"},
            {"criterion": "French i18n", "status": "Unverifiable"},
        ],
    }
    md = format_alignment_comment(wi, result)
    assert "Requirement Alignment" in md
    assert "Alignment: Partial" in md
    assert "| Criterion | Status |" in md
    assert "Button \\| downloads CSV" in md          # pipe escaped for the table
    assert "1/2 checkable criteria met." in md       # Unverifiable excluded


def test_format_alignment_comment_low_confidence_note():
    wi = WorkItem(id=1, type="Epic", state="New", title="Big thing")
    md = format_alignment_comment(wi, {"verdict": "Partial", "confidence": "Low", "criteria": []})
    assert "_(low confidence)_" in md


def test_upsert_alignment_comment_updates_existing(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    monkeypatch.setattr(client, "_find_alignment_thread", lambda pr, marker: (55, 99))
    calls = []
    monkeypatch.setattr(
        client, "_request",
        lambda method, url, body=None, **k: (calls.append((method, url)), {"id": 1})[1],
    )
    client.upsert_alignment_comment(7, 1234, "hi")
    assert calls[-1][0] == "PATCH"
    assert "/threads/55/comments/99" in calls[-1][1]


def test_upsert_alignment_comment_creates_when_absent(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    monkeypatch.setattr(client, "_find_alignment_thread", lambda pr, marker: None)
    calls = []

    def fake(method, url, body=None, **k):
        calls.append((method, url, body))
        return {"id": 1}

    monkeypatch.setattr(client, "_request", fake)
    client.upsert_alignment_comment(7, 1234, "hi")
    method, url, body = calls[-1]
    assert method == "POST"
    # New thread is tagged with the work-item marker so the next run finds it.
    prop = body["properties"][azure_devops.ALIGNMENT_THREAD_PROPERTY_KEY]
    assert prop["$value"] == "1234"


def _threads_response(threads):
    return {"value": threads}


def test_list_thread_finding_ids_counts_live_threads(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    threads = _threads_response([
        {
            "comments": [{"id": 1, "isDeleted": False}],
            "properties": {azure_devops.THREAD_PROPERTY_KEY: {"$value": "abc123"}},
        },
    ])
    monkeypatch.setattr(client, "_request", lambda *a, **k: threads)
    assert client.list_thread_finding_ids(5) == {"abc123"}


def test_list_thread_finding_ids_skips_soft_deleted_comment(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    # Azure keeps the thread + its PRSentinelFindingId property but marks the
    # comment isDeleted — the finding should NOT be reported as already pushed.
    threads = _threads_response([
        {
            "comments": [{"id": 1, "isDeleted": True}],
            "properties": {azure_devops.THREAD_PROPERTY_KEY: {"$value": "abc123"}},
        },
    ])
    monkeypatch.setattr(client, "_request", lambda *a, **k: threads)
    assert client.list_thread_finding_ids(5) == set()


def test_list_thread_finding_ids_keeps_thread_with_one_live_comment(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    # Finding comment deleted but a teammate's reply remains → thread still live.
    threads = _threads_response([
        {
            "comments": [{"id": 1, "isDeleted": True}, {"id": 2, "isDeleted": False}],
            "properties": {azure_devops.THREAD_PROPERTY_KEY: {"$value": "abc123"}},
        },
    ])
    monkeypatch.setattr(client, "_request", lambda *a, **k: threads)
    assert client.list_thread_finding_ids(5) == {"abc123"}


def test_list_alignment_work_item_ids_reads_alignment_marker(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    threads = _threads_response([
        {  # a finding thread — must NOT be returned here
            "comments": [{"id": 1, "isDeleted": False}],
            "properties": {azure_devops.THREAD_PROPERTY_KEY: {"$value": "abc123"}},
        },
        {  # an alignment verdict thread, still live
            "comments": [{"id": 2, "isDeleted": False}],
            "properties": {azure_devops.ALIGNMENT_THREAD_PROPERTY_KEY: {"$value": "17"}},
        },
    ])
    monkeypatch.setattr(client, "_request", lambda *a, **k: threads)
    assert client.list_alignment_work_item_ids(5) == {"17"}
    # The finding-id lister sees only the finding thread, not the alignment one.
    assert client.list_thread_finding_ids(5) == {"abc123"}


def test_list_alignment_work_item_ids_skips_soft_deleted(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    threads = _threads_response([
        {
            "comments": [{"id": 2, "isDeleted": True}],
            "properties": {azure_devops.ALIGNMENT_THREAD_PROPERTY_KEY: {"$value": "17"}},
        },
    ])
    monkeypatch.setattr(client, "_request", lambda *a, **k: threads)
    assert client.list_alignment_work_item_ids(5) == set()


def test_find_alignment_thread_matches_marker(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    threads = {
        "value": [
            {"id": 10, "comments": [{"id": 1}], "properties": {}},
            {
                "id": 20,
                "comments": [{"id": 2}],
                "properties": {
                    azure_devops.ALIGNMENT_THREAD_PROPERTY_KEY: {"$value": "1234"}
                },
            },
        ]
    }
    monkeypatch.setattr(client, "_request", lambda *a, **k: threads)
    assert client._find_alignment_thread(7, "1234") == (20, 2)
    assert client._find_alignment_thread(7, "9999") is None


def test_find_alignment_thread_skips_soft_deleted_comment(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    # User deleted the verdict comment on the PR: Azure keeps the thread + marker
    # but the comment is isDeleted. We must NOT return it (patching it stays
    # invisible) — return None so upsert creates a fresh thread.
    threads = {
        "value": [{
            "id": 20,
            "comments": [{"id": 2, "isDeleted": True}],
            "properties": {azure_devops.ALIGNMENT_THREAD_PROPERTY_KEY: {"$value": "1234"}},
        }]
    }
    monkeypatch.setattr(client, "_request", lambda *a, **k: threads)
    assert client._find_alignment_thread(7, "1234") is None


def test_upsert_creates_new_thread_when_only_comment_deleted(monkeypatch):
    client = AzureDevOpsClient(org="o", project="p", repo="r", pat="x")
    threads = {
        "value": [{
            "id": 20,
            "comments": [{"id": 2, "isDeleted": True}],
            "properties": {azure_devops.ALIGNMENT_THREAD_PROPERTY_KEY: {"$value": "1234"}},
        }]
    }
    calls = []

    def fake(method, url, body=None, **k):
        calls.append((method, url, body))
        return threads if method == "GET" else {"id": 99}

    monkeypatch.setattr(client, "_request", fake)
    client.upsert_alignment_comment(7, 1234, "refreshed verdict")
    methods = [m for m, *_ in calls]
    # The dead comment is never PATCHed; a new (visible) thread is POSTed instead.
    assert "PATCH" not in methods
    assert methods[-1] == "POST"
    body = calls[-1][2]
    assert body["properties"][azure_devops.ALIGNMENT_THREAD_PROPERTY_KEY]["$value"] == "1234"
