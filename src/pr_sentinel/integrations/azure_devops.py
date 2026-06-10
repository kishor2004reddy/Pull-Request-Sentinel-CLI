"""Azure DevOps pull-request integration.

Two responsibilities, both dependency-free (stdlib ``urllib`` only):

- :func:`parse_remote` turns an ``origin`` remote URL into an
  ``(org, project, repo)`` triple, covering the HTTPS, ``visualstudio.com``,
  and SSH remote formats Azure DevOps hands out.
- :class:`AzureDevOpsClient` creates pull-request *comment threads* via the
  REST API. PR Sentinel only ever creates PR-level threads (no ``threadContext``),
  one per finding, tagged with a thread property so re-pushing is idempotent.

Authentication is a Personal Access Token (PAT) sent as HTTP Basic
(``base64(":" + pat)``). No token is ever written to disk or rendered into HTML.
"""
import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote

from pr_sentinel.config import AZURE_API_VERSION

# Marker stored on every thread PR Sentinel creates, so a re-push can detect
# findings that were already posted and skip them. Azure DevOps thread
# properties are a typed dict: {"name": {"$type": "...", "$value": ...}}.
THREAD_PROPERTY_KEY = "PRSentinelFindingId"


class AzureDevOpsError(Exception):
    """Remote parsing failed, or an Azure DevOps API call returned an error."""


# Remote URL shapes Azure DevOps produces:
#   https://dev.azure.com/{org}/{project}/_git/{repo}
#   https://{org}@dev.azure.com/{org}/{project}/_git/{repo}
#   https://{org}.visualstudio.com/{project}/_git/{repo}
#   https://{org}.visualstudio.com/DefaultCollection/{project}/_git/{repo}
#   git@ssh.dev.azure.com:v3/{org}/{project}/{repo}
#   org@vs-ssh.visualstudio.com:v3/{org}/{project}/{repo}
_HTTPS_DEV_AZURE = re.compile(
    r"https?://(?:[^@/]+@)?dev\.azure\.com/(?P<org>[^/]+)/(?P<project>[^/]+)/_git/(?P<repo>[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_HTTPS_VSTS = re.compile(
    r"https?://(?P<org>[^.]+)\.visualstudio\.com/(?:DefaultCollection/)?(?P<project>[^/]+)/_git/(?P<repo>[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_SSH_AZURE = re.compile(
    r"git@ssh\.dev\.azure\.com:v3/(?P<org>[^/]+)/(?P<project>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_SSH_VSTS = re.compile(
    r"[^@]+@vs-ssh\.visualstudio\.com:v3/(?P<org>[^/]+)/(?P<project>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def parse_remote(url: str) -> tuple[str, str, str]:
    """Parse an Azure DevOps remote URL into ``(org, project, repo)``.

    Raises :class:`AzureDevOpsError` if the URL isn't a recognised Azure DevOps
    remote (e.g. a GitHub remote), so the caller can ask for explicit
    ``--org/--project/--repo``.
    """
    url = (url or "").strip()
    for pattern in (_HTTPS_DEV_AZURE, _HTTPS_VSTS, _SSH_AZURE, _SSH_VSTS):
        m = pattern.match(url)
        if m:
            return m.group("org"), m.group("project"), m.group("repo")
    raise AzureDevOpsError(
        f"Could not parse an Azure DevOps repository from remote URL {url!r}. "
        "Pass --org, --project and --repo explicitly."
    )


@dataclass
class AzureDevOpsClient:
    """Minimal Azure DevOps REST client scoped to one repository."""

    org: str
    project: str
    repo: str
    pat: str
    api_version: str = AZURE_API_VERSION
    base_url: str = "https://dev.azure.com"

    def _auth_header(self) -> str:
        token = base64.b64encode(f":{self.pat}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    def _threads_url(self, pr_id: int) -> str:
        return (
            f"{self.base_url}/{quote(self.org)}/{quote(self.project)}"
            f"/_apis/git/repositories/{quote(self.repo)}"
            f"/pullRequests/{pr_id}/threads?api-version={self.api_version}"
        )

    def _request(self, method: str, url: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self._auth_header())
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:400]
            if e.code in (401, 203):
                raise AzureDevOpsError(
                    "Azure DevOps rejected the credentials (HTTP "
                    f"{e.code}). Check the PAT and that it has Code (read & "
                    "write) scope."
                ) from e
            raise AzureDevOpsError(
                f"Azure DevOps {method} failed (HTTP {e.code}): {detail}"
            ) from e
        except urllib.error.URLError as e:
            raise AzureDevOpsError(f"Could not reach Azure DevOps: {e.reason}") from e
        return json.loads(raw) if raw else {}

    def list_thread_finding_ids(self, pr_id: int) -> set[str]:
        """Finding ids already posted to this PR by PR Sentinel.

        Used to skip findings that were pushed on an earlier run. Best-effort:
        returns an empty set if threads can't be listed.
        """
        try:
            data = self._request("GET", self._threads_url(pr_id))
        except AzureDevOpsError:
            return set()
        ids: set[str] = set()
        for thread in data.get("value", []):
            prop = (thread.get("properties") or {}).get(THREAD_PROPERTY_KEY)
            if isinstance(prop, dict) and prop.get("$value"):
                ids.add(str(prop["$value"]))
        return ids

    def create_pr_thread(
        self, pr_id: int, content: str, finding_id: str | None = None
    ) -> dict:
        """Create a PR-level (overview) comment thread and return the response."""
        body: dict = {
            "comments": [
                {"parentCommentId": 0, "content": content, "commentType": 1}
            ],
            "status": 1,  # active
        }
        if finding_id:
            body["properties"] = {
                THREAD_PROPERTY_KEY: {
                    "$type": "System.String",
                    "$value": finding_id,
                }
            }
        return self._request("POST", self._threads_url(pr_id), body)


_SEVERITY_EMOJI = {"High": "🔴", "Medium": "🟠", "Low": "🔵"}


def format_finding_comment(finding: dict) -> str:
    """Render one finding as the markdown body of a PR comment thread."""
    severity = finding.get("severity", "Low")
    emoji = _SEVERITY_EMOJI.get(severity, "")
    agent = finding.get("agent", "PR Sentinel")
    file = finding.get("file", "")
    line_hint = str(finding.get("lineHint", "")).strip()
    location = f"`{file}`" + (f" — {line_hint}" if line_hint else "")

    lines = [
        f"{emoji} **PR Sentinel — {severity}** · _{agent}_",
        "",
        f"**Location:** {location}",
    ]
    for label, key in (("Issue", "issue"), ("Reasoning", "reasoning"),
                        ("Recommendation", "recommendation")):
        value = str(finding.get(key, "")).strip()
        if value:
            lines += ["", f"**{label}:** {value}"]
    return "\n".join(lines)
