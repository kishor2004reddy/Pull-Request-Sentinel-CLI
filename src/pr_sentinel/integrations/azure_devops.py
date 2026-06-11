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
import html
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import quote

from pr_sentinel.config import AZURE_API_VERSION, AZURE_WORK_ITEM_FIELDS

# Marker stored on every thread PR Sentinel creates, so a re-push can detect
# findings that were already posted and skip them. Azure DevOps thread
# properties are a typed dict: {"name": {"$type": "...", "$value": ...}}.
THREAD_PROPERTY_KEY = "PRSentinelFindingId"

# Marker on the single alignment-summary thread per work item. Keyed by work
# item id so a re-run updates the existing summary comment instead of stacking a
# new one each time.
ALIGNMENT_THREAD_PROPERTY_KEY = "PRSentinelAlignmentWorkItem"


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

    def _pr_work_items_url(self, pr_id: int) -> str:
        return (
            f"{self.base_url}/{quote(self.org)}/{quote(self.project)}"
            f"/_apis/git/repositories/{quote(self.repo)}"
            f"/pullRequests/{pr_id}/workitems?api-version={self.api_version}"
        )

    def _work_items_url(self, ids: list[int], fields: tuple[str, ...]) -> str:
        ids_csv = ",".join(str(i) for i in ids)
        fields_csv = quote(",".join(fields))
        return (
            f"{self.base_url}/{quote(self.org)}/{quote(self.project)}"
            f"/_apis/wit/workitems?ids={ids_csv}&fields={fields_csv}"
            f"&api-version={self.api_version}"
        )

    def _request(
        self,
        method: str,
        url: str,
        body: dict | None = None,
        scope_hint: str = "Code (read & write)",
    ) -> dict:
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
                    f"{e.code}). Check the PAT and that it has {scope_hint} "
                    "scope."
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
        self,
        pr_id: int,
        content: str,
        finding_id: str | None = None,
        file_path: str | None = None,
        line: int | None = None,
    ) -> dict:
        """Create a comment thread and return the response.

        When both ``file_path`` and ``line`` are given the thread is pinned to
        that line on the new ("right") side of the diff, so it appears in the
        Files tab. Otherwise — or when either is missing — it falls back to a
        PR-level (overview) thread, so vaguely-located findings still post.
        """
        body: dict = {
            "comments": [
                {"parentCommentId": 0, "content": content, "commentType": 1}
            ],
            "status": 1,  # active
        }
        if file_path and line:
            # Azure requires a repo-relative path with a leading slash, and
            # 1-based line/offset; offset 1 anchors at the start of the line.
            norm_path = "/" + str(file_path).lstrip("/")
            body["threadContext"] = {
                "filePath": norm_path,
                "rightFileStart": {"line": line, "offset": 1},
                "rightFileEnd": {"line": line, "offset": 1},
            }
        if finding_id:
            body["properties"] = {
                THREAD_PROPERTY_KEY: {
                    "$type": "System.String",
                    "$value": finding_id,
                }
            }
        return self._request("POST", self._threads_url(pr_id), body)

    def _thread_comment_url(self, pr_id: int, thread_id: int, comment_id: int) -> str:
        return (
            f"{self.base_url}/{quote(self.org)}/{quote(self.project)}"
            f"/_apis/git/repositories/{quote(self.repo)}"
            f"/pullRequests/{pr_id}/threads/{thread_id}/comments/{comment_id}"
            f"?api-version={self.api_version}"
        )

    def _find_alignment_thread(self, pr_id: int, marker: str) -> tuple[int, int] | None:
        """Locate this work item's existing alignment-summary thread.

        Returns ``(thread_id, first_comment_id)`` when a prior summary thread for
        ``marker`` (the work item id) is found, else ``None``. Best-effort: returns
        ``None`` if threads can't be listed.
        """
        try:
            data = self._request("GET", self._threads_url(pr_id))
        except AzureDevOpsError:
            return None
        for thread in data.get("value", []):
            prop = (thread.get("properties") or {}).get(ALIGNMENT_THREAD_PROPERTY_KEY)
            if isinstance(prop, dict) and str(prop.get("$value")) == marker:
                comments = thread.get("comments") or []
                if thread.get("id") and comments and comments[0].get("id"):
                    return int(thread["id"]), int(comments[0]["id"])
        return None

    def upsert_alignment_comment(
        self, pr_id: int, work_item_id: int, content: str
    ) -> dict:
        """Post (or refresh) the alignment-summary comment for one work item.

        If a prior summary thread for this work item exists, its comment is
        updated in place; otherwise a new PR-level thread is created, tagged with
        the work-item marker so the next run finds it. Falls back to creating a
        fresh thread if the in-place update fails.
        """
        marker = str(work_item_id)
        existing = self._find_alignment_thread(pr_id, marker)
        if existing:
            thread_id, comment_id = existing
            try:
                return self._request(
                    "PATCH",
                    self._thread_comment_url(pr_id, thread_id, comment_id),
                    {"content": content},
                )
            except AzureDevOpsError:
                pass  # fall through to creating a new thread
        body = {
            "comments": [{"parentCommentId": 0, "content": content, "commentType": 1}],
            "status": 1,
            "properties": {
                ALIGNMENT_THREAD_PROPERTY_KEY: {
                    "$type": "System.String",
                    "$value": marker,
                }
            },
        }
        return self._request("POST", self._threads_url(pr_id), body)

    def get_pr_work_items(self, pr_id: int) -> list[int]:
        """Return the IDs of the work items linked to a pull request.

        Azure returns ``{"value": [{"id": "123", "url": "..."}]}`` where ``id``
        is the work item id as a string. Returns an empty list when the PR has
        no linked work items.
        """
        data = self._request(
            "GET", self._pr_work_items_url(pr_id), scope_hint="Code (read)"
        )
        ids: list[int] = []
        for ref in data.get("value", []):
            raw_id = ref.get("id")
            if raw_id is None:
                continue
            try:
                ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue
        return ids

    def get_work_items(
        self, ids: list[int], fields: tuple[str, ...] = AZURE_WORK_ITEM_FIELDS
    ) -> list["WorkItem"]:
        """Fetch and normalize the given work items.

        Reading work items needs the Work Items (read) scope on the PAT, which
        is *separate* from the Code scope the rest of the integration uses — the
        401 message names it so the user knows what to add. Returns one
        :class:`WorkItem` per id, preserving input order.
        """
        if not ids:
            return []
        data = self._request(
            "GET",
            self._work_items_url(ids, fields),
            scope_hint="Work Items (read)",
        )
        return [normalize_work_item(raw) for raw in data.get("value", [])]


@dataclass
class WorkItem:
    """A normalized Azure DevOps work item, ready for the alignment review.

    ``criteria`` is the acceptance-criteria text split into individual checkable
    points (bullets, or Given/When/Then clauses). ``repro_steps`` carries a
    Bug's reproduction text. Both ``description``/``repro_steps``/``criteria``
    are plain text — HTML from Azure has already been stripped.
    """

    id: int
    type: str
    state: str
    title: str
    description: str = ""
    criteria: list[str] = field(default_factory=list)
    repro_steps: str = ""


# Block-level tags whose boundaries should become line breaks when we flatten
# Azure's HTML rich-text fields to plain text.
_BLOCK_TAG_RE = re.compile(
    r"</?(?:p|div|br|li|ul|ol|tr|table|h[1-6])[^>]*>", re.IGNORECASE
)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(value: str | None) -> str:
    """Flatten an Azure rich-text (HTML) field to plain text.

    Description, AcceptanceCriteria and ReproSteps come back as HTML. Block
    tags become newlines so list items and paragraphs stay on separate lines;
    remaining tags are dropped and entities unescaped. Whitespace within each
    line is collapsed, blank lines removed.
    """
    if not value:
        return ""
    text = _BLOCK_TAG_RE.sub("\n", value)
    text = _ANY_TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _split_criteria(text: str) -> list[str]:
    """Split acceptance-criteria text into individual checkable points.

    ``_html_to_text`` already put each bullet / paragraph on its own line, so we
    split on newlines and strip common list markers (-, *, •, '1.', etc.).
    """
    items: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if cleaned:
            items.append(cleaned)
    return items


def normalize_work_item(raw: dict) -> WorkItem:
    """Turn a raw Azure work item (``{"id":..., "fields": {...}}``) into a WorkItem."""
    fields = raw.get("fields", {}) or {}
    description = _html_to_text(fields.get("System.Description"))
    criteria_text = _html_to_text(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria"))
    return WorkItem(
        id=int(raw.get("id", 0) or 0),
        type=str(fields.get("System.WorkItemType", "") or "").strip(),
        state=str(fields.get("System.State", "") or "").strip(),
        title=str(fields.get("System.Title", "") or "").strip(),
        description=description,
        criteria=_split_criteria(criteria_text),
        repro_steps=_html_to_text(fields.get("Microsoft.VSTS.TCM.ReproSteps")),
    )


def line_from_hint(line_hint) -> int | None:
    """Extract a 1-based line number from a hint like '+42', '42', or '+42,7'.

    Returns ``None`` when no number is present, so the caller can fall back to a
    PR-level thread.
    """
    if not line_hint:
        return None
    m = re.search(r"\d+", str(line_hint))
    return int(m.group()) if m else None


_ALIGNMENT_VERDICT_EMOJI = {
    "Satisfied": "✅",
    "Partial": "🟠",
    "Not satisfied": "🔴",
    "Unknown": "⚪",
}
_CRITERION_EMOJI = {
    "Met": "✅",
    "Partial": "🟠",
    "Not met": "❌",
    "Unverifiable": "❔",
}


def format_alignment_comment(work_item, result: dict) -> str:
    """Render an alignment verdict + criteria checklist as a PR comment body.

    ``work_item`` is a :class:`WorkItem`; ``result`` is the Alignment Agent's
    output (verdict/confidence/summary/criteria). Returns Azure-flavoured
    markdown (it renders comment tables), kept idempotent so re-posting the same
    review produces the same body.
    """
    verdict = result.get("verdict", "Unknown")
    emoji = _ALIGNMENT_VERDICT_EMOJI.get(verdict, "⚪")
    confidence = result.get("confidence", "Low")
    conf_note = " _(low confidence)_" if confidence == "Low" else ""

    wi_type = work_item.type or "Work Item"
    lines = [
        "🛡️ **PR Sentinel — Requirement Alignment**",
        "",
        f"**#{work_item.id} · {wi_type} · {work_item.title}**",
        "",
        f"{emoji} **Alignment: {verdict}**{conf_note}",
    ]
    summary = str(result.get("summary", "")).strip()
    if summary:
        lines += ["", summary]

    criteria = result.get("criteria") or []
    if criteria:
        lines += ["", "| | Criterion | Status |", "|--|--|--|"]
        for c in criteria:
            status = c.get("status", "Unverifiable")
            cicon = _CRITERION_EMOJI.get(status, "❔")
            text = str(c.get("criterion", "")).strip().replace("|", "\\|") or "—"
            lines.append(f"| {cicon} | {text} | {status} |")

        met = sum(1 for c in criteria if c.get("status") == "Met")
        checkable = sum(1 for c in criteria if c.get("status") != "Unverifiable")
        if checkable:
            lines += ["", f"_{met}/{checkable} checkable criteria met._"]

    return "\n".join(lines)


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
