import pytest

from pr_sentinel.integrations import azure_devops
from pr_sentinel.integrations.azure_devops import (
    AzureDevOpsClient,
    AzureDevOpsError,
    format_finding_comment,
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
