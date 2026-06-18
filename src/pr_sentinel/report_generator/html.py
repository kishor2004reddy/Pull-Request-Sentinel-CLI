"""HTML rendering of a PR Sentinel report.

Produces a single self-contained page (inline CSS + a sprinkle of vanilla JS,
no external assets) intended to be opened in a browser. Report building and
the shared report-level helpers live in the package ``__init__``.

Note: this module is named ``html`` but ``import html`` below resolves to the
standard library via absolute import, not to this module.
"""
import html
import re
from pathlib import Path
from urllib.parse import quote

from pr_sentinel.config import ALIGNMENT_AGENT_NAME, PUSH_CONFIG_PLACEHOLDER
from pr_sentinel.report_generator import _agent_summary_data, _merge_verdict

# Editor deep-link scheme. VS Code (and forks via the same handler) open
# vscode://file/<abs-path>:<line> in the current window.
_EDITOR_URI_SCHEME = "vscode"

# Risk banner palette (text colour, background, accent border).
_RISK_COLORS = {
    "High":    ("#86181d", "#ffebe9", "#d1242f"),
    "Unknown": ("#86181d", "#ffebe9", "#d1242f"),
    "Medium":  ("#7d4e00", "#fff8c5", "#bf8700"),
    "Low":     ("#0a3069", "#ddf4ff", "#0969da"),
    "None":    ("#1a7f37", "#dafbe1", "#1a7f37"),
}

# Severity badge palette (text, background).
_SEV_COLORS = {
    "High":   ("#86181d", "#ffebe9"),
    "Medium": ("#7d4e00", "#fff8c5"),
    "Low":    ("#0a3069", "#ddf4ff"),
}


def _e(value) -> str:
    """HTML-escape a value, rendering None/empty as an em dash."""
    text = "" if value is None else str(value)
    text = text.strip()
    return html.escape(text) if text else "—"


def _multiline_html(text: str) -> str:
    """Escape free text, preserving paragraph/line breaks."""
    return _e(text).replace("\n", "<br>")


def _first_line_number(line_hint) -> int | None:
    """Extract a line number from a hint like '+42', '42', or '+42,7'."""
    if not line_hint:
        return None
    m = re.search(r"\d+", str(line_hint))
    return int(m.group()) if m else None


def _editor_href(rel_path: str, repo_root: str, line: int | None = None) -> str:
    """Build a vscode://file/<abs-path>[:line] deep-link for a repo-relative path."""
    abs_path = (Path(repo_root) / str(rel_path)).as_posix()
    href = f"{_EDITOR_URI_SCHEME}://file/{quote(abs_path, safe='/:')}"
    if line is not None:
        href += f":{line}"
    return href


def _file_cell(file: str, line_hint, repo_root: str | None) -> str:
    """File label, wrapped in an editor deep-link when a repo root is known."""
    loc = str(line_hint).strip() if line_hint else ""
    loc_html = f' <span class="loc">{_e(loc)}</span>' if loc else ""
    label = f"{_e(file)}{loc_html}"

    name = str(file).strip()
    # Skip synthetic labels like "(multiple files)" / "<unknown>" — not real paths.
    if not repo_root or not name or name[0] in "(<":
        return label

    href = _editor_href(file, repo_root, _first_line_number(line_hint))
    return f'<a class="file-link" href="{html.escape(href, quote=True)}">{label}</a>'


_ORDERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_UNORDERED_RE = re.compile(r"^\s*[-*]\s+(.*)$")

# A repo-relative file path mentioned inside free text: at least one "/" and a
# slash-separated run of path-ish chars. The lookbehind keeps us from matching
# inside URLs (http://host/a/b) or longer tokens.
_PATH_RE = re.compile(r"(?<![\w./:@-])[\w.\-]+(?:/[\w.\-]+)+")
_TRAILING_PUNCT = ".,;:)]}>"


def _linkify_path(match: "re.Match[str]", repo_root: str) -> str:
    token = match.group(0)
    # Peel sentence punctuation that isn't part of the path.
    trail = ""
    while token and token[-1] in _TRAILING_PUNCT:
        trail = token[-1] + trail
        token = token[:-1]
    # Require a file extension in the last segment (skip bare directories).
    last = token.rsplit("/", 1)[-1]
    if "/" not in token or "." not in last:
        return match.group(0)
    href = _editor_href(token, repo_root)
    return f'<a class="file-link" href="{html.escape(href, quote=True)}">{token}</a>{trail}'


def _e_linkify(text: str, repo_root: str | None) -> str:
    """Escape free text, then turn any repo-relative file paths into links.

    Path chars (letters, digits, ``.`` ``-`` ``_`` ``/``) are untouched by HTML
    escaping, so matching the escaped string is safe.
    """
    escaped = _e(text)
    if not repo_root or escaped == "—":
        return escaped
    return _PATH_RE.sub(lambda m: _linkify_path(m, repo_root), escaped)


def _rich_text_html(text: str, repo_root: str | None = None) -> str:
    """Render free text as HTML, turning numbered/bulleted runs into real lists.

    Findings often pack multiple numbered steps into one field. A bare <br>
    between long, wrapping items reads as a run-on, so consecutive list items
    are grouped into <ol>/<ul> with their original markers stripped.
    """
    raw = "" if text is None else str(text).strip()
    if not raw:
        return "—"

    lines = raw.split("\n")
    # Some fields pack numbered steps onto a single line: "1. a 2. b 3. c".
    if len(lines) == 1:
        inline = re.split(r"\s+(?=\d+[.)]\s)", lines[0])
        if len(inline) > 1:
            lines = inline

    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag: str | None = None

    def flush_para() -> None:
        if para:
            out.append("<br>".join(_e_linkify(x, repo_root) for x in para))
            para.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if items:
            lis = "".join(f"<li>{_e_linkify(it, repo_root)}</li>" for it in items)
            out.append(f"<{list_tag}>{lis}</{list_tag}>")
            items.clear()
        list_tag = None

    for line in lines:
        if not line.strip():
            continue
        ordered = _ORDERED_RE.match(line)
        unordered = _UNORDERED_RE.match(line)
        if ordered:
            if list_tag == "ul":
                flush_list()
            flush_para()
            list_tag = "ol"
            items.append(ordered.group(2))
        elif unordered:
            if list_tag == "ol":
                flush_list()
            flush_para()
            list_tag = "ul"
            items.append(unordered.group(1))
        else:
            flush_list()
            para.append(line)

    flush_para()
    flush_list()
    return "".join(out)


def _finding_card_html(
    f: dict,
    repo_root: str | None,
    *,
    open_: bool = False,
    show_agent: bool = True,
    pick_label: str = "Select finding to push",
) -> str:
    """One collapsible finding card with a push checkbox.

    Shared by the review findings list, the alignment gaps list, and the
    combined report so all three stay byte-for-byte consistent. ``open_`` expands
    the card on load (gaps default to open); ``show_agent`` toggles the agent tag.
    """
    severity = f["severity"]
    sev_fg, sev_bg = _SEV_COLORS[severity]
    file_html = _file_cell(f["file"], f.get("lineHint"), repo_root)
    fid = html.escape(str(f.get("id", "")), quote=True)
    open_attr = " open" if open_ else ""
    agent_html = (
        f'<span class="agent-tag">{_e(f.get("agent"))}</span>' if show_agent else ""
    )
    agent_val = html.escape(str(f.get("agent", "")), quote=True)
    parts = [
        f'<details class="finding" data-sev="{severity}" data-agent="{agent_val}" data-finding-id="{fid}"{open_attr}>',
        "<summary>"
        f'<input type="checkbox" class="pick" data-finding-id="{fid}" '
        f'aria-label="{pick_label}">'
        f'<span class="badge" style="color:{sev_fg};background:{sev_bg}">{severity}</span>'
        f'<span class="file">{file_html}</span>'
        f"{agent_html}"
        f'<span class="push-mark" data-finding-id="{fid}"></span>'
        "</summary>",
        '<div class="finding-body">',
        f'<p><span class="lbl">Issue.</span> {_rich_text_html(f.get("issue", ""), repo_root)}</p>',
    ]
    if str(f.get("reasoning", "")).strip():
        parts.append(
            f'<p><span class="lbl">Reasoning.</span> {_rich_text_html(f["reasoning"], repo_root)}</p>'
        )
    if str(f.get("recommendation", "")).strip():
        parts.append(
            f'<p><span class="lbl">Recommendation.</span> {_rich_text_html(f["recommendation"], repo_root)}</p>'
        )
    parts.append("</div></details>")
    return "".join(parts)


def _severity_counts(findings: list[dict]) -> dict[str, int]:
    counts = {"High": 0, "Medium": 0, "Low": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return counts


def _chips_html(report: dict) -> str:
    """The always-visible summary strip under the top bar (counts + coverage)."""
    findings = report["findings"]
    counts = _severity_counts(findings)
    sections = report.get("alignment") or []
    coverage_ok = report.get("coverageComplete", True)
    chips = [f'<button class="chip" data-filter-reset type="button"><b>{len(findings)}</b> findings</button>']
    if counts["High"]:
        chips.append(f'<button class="chip hi" data-filter-sev="High" type="button"><b>{counts["High"]}</b> High</button>')
    if counts["Medium"]:
        chips.append(f'<button class="chip me" data-filter-sev="Medium" type="button"><b>{counts["Medium"]}</b> Medium</button>')
    if counts["Low"]:
        chips.append(f'<button class="chip lo" data-filter-sev="Low" type="button"><b>{counts["Low"]}</b> Low</button>')
    if sections:
        chips.append(f'<span class="chip"><b>{len(sections)}</b> work item(s)</span>')
    chips.append(
        '<span class="chip ok">full coverage</span>'
        if coverage_ok
        else '<span class="chip warn">⚠ partial coverage</span>'
    )
    return '<div class="chips">' + "".join(chips) + "</div>"


def _pr_web_url(pr: dict) -> str:
    """Azure DevOps web URL for a PR, or '' when org/project/repo are unknown."""
    org, project, repo, pid = (
        pr.get("org"), pr.get("project"), pr.get("repo"), pr.get("id")
    )
    if not (org and project and repo and pid):
        return ""
    return (
        f"https://dev.azure.com/{quote(str(org))}/{quote(str(project))}"
        f"/_git/{quote(str(repo))}/pullrequest/{pid}"
    )


def _pr_section_html(pr: dict) -> str:
    """The 'Pull Request' card in Overview: PR #, repo, base and source branch."""
    repo_full = "/".join(
        str(pr.get(k, "")) for k in ("org", "project", "repo") if pr.get(k)
    )
    url = _pr_web_url(pr)
    pid = f"#{_e(pr.get('id'))}"
    pid_html = (
        f'<a class="file-link" href="{html.escape(url, quote=True)}">{pid}</a>'
        if url else pid
    )
    if str(pr.get("title", "")).strip():
        pid_html += f' <span class="muted">· {_e(pr.get("title"))}</span>'

    def row(k: str, v: str) -> str:
        return f'<div class="kv-k">{k}</div><div class="kv-v">{v}</div>'

    return (
        '<div class="block"><h2>Pull Request</h2><div class="kv">'
        + row("PR", pid_html)
        + row("Repository", f"<code>{_e(repo_full)}</code>")
        + row("Base branch", f"<code>{_e(pr.get('baseBranch'))}</code>")
        + row("Source branch", f"<code>{_e(pr.get('sourceBranch'))}</code>")
        + "</div></div>"
    )


def _overview_panel_html(report: dict) -> str:
    """Risk hero + PR context + merge verdict + per-agent table. Landing tab."""
    risk = report["riskLevel"]
    raw_count = report.get("rawFindingCount")
    p: list[str] = []

    p.append(
        '<p class="meta">'
        f"base <code>{_e(report['baseBranch'])}</code> · "
        f"source <code>{_e(report['source'])}</code> · "
        f"{len(report['agentsExecuted'])} agent(s) · {_e(report['reviewedAt'])}"
        "</p>"
    )

    p.append(
        f'<div class="hero risk-{risk}">'
        f'<div class="hero-risk">Risk: {_e(risk)}</div>'
        f'<div class="hero-summary">{_e(report["summary"])}</div>'
        "</div>"
    )

    if report.get("pr"):
        p.append(_pr_section_html(report["pr"]))
    if raw_count is not None and raw_count != len(report["findings"]):
        p.append(
            f'<p class="note">Cleaned from {raw_count} raw finding(s) by the summary pass.</p>'
        )

    p.append('<div class="block">')
    p.append("<h2>Merge Verdict</h2>")
    p.append(f'<div class="verdict">{_multiline_html(_merge_verdict(report))}</div>')
    p.append("</div>")

    rows, totals = _agent_summary_data(report)
    p.append('<div class="block">')
    p.append("<h2>Findings by Agent</h2>")
    p.append('<table class="agents"><thead><tr>'
             "<th>Agent</th><th>Status</th><th>Total</th>"
             "<th>High</th><th>Medium</th><th>Low</th></tr></thead><tbody>")
    for r in rows:
        if r["status"] == "FAILED":
            p.append(
                f"<tr><td>{_e(r['agent'])}</td>"
                '<td><span class="pill fail">FAILED</span></td>'
                "<td>—</td><td>—</td><td>—</td><td>—</td></tr>"
            )
            continue
        p.append(
            f"<tr><td>{_e(r['agent'])}</td>"
            '<td><span class="pill ok">OK</span></td>'
            f"<td>{r['total']}</td><td>{r['high']}</td>"
            f"<td>{r['medium']}</td><td>{r['low']}</td></tr>"
        )
    p.append(
        f'<tr class="total"><td>TOTAL</td><td></td><td>{totals["total"]}</td>'
        f'<td>{totals["high"]}</td><td>{totals["medium"]}</td><td>{totals["low"]}</td></tr>'
    )
    p.append("</tbody></table>")
    p.append("</div>")
    return "".join(p)


def _findings_panel_html(
    findings: list[dict], repo_root: str | None, *, title: str, empty: str
) -> str:
    """A list of finding cards, sorted High→Low, with an empty state."""
    p = [f'<h2 class="panel-title">{title}</h2>']
    if not findings:
        p.append(f'<p class="empty">{empty}</p>')
    else:
        agents = list(dict.fromkeys(f.get("agent", "") for f in findings if f.get("agent")))
        if agents:
            p.append('<div class="filter-bar">')
            p.append('<span class="filter-label">Agent</span>')
            for agent in agents:
                p.append(
                    f'<button class="filter-btn" data-filter-agent="{html.escape(agent)}"'
                    f' type="button">{html.escape(agent)}</button>'
                )
            p.append('</div>')
        p.append('<p class="filter-no-results" style="display:none">No findings match the active filters.</p>')
        for severity in ("High", "Medium", "Low"):
            for f in (x for x in findings if x["severity"] == severity):
                p.append(_finding_card_html(f, repo_root))
    return "".join(p)


def _gaps_panel_html(findings: list[dict], repo_root: str | None) -> str:
    p = [f'<h2 class="panel-title">Gaps ({len(findings)})</h2>']
    if not findings:
        p.append('<p class="empty">No gaps — every checkable criterion is met. 🎉</p>')
    else:
        p.append('<p class="filter-no-results" style="display:none">No gaps match the active filters.</p>')
        for severity in ("High", "Medium", "Low"):
            for f in (x for x in findings if x["severity"] == severity):
                p.append(
                    _finding_card_html(
                        f, repo_root, open_=True, show_agent=False,
                        pick_label="Select gap to push",
                    )
                )
    return "".join(p)


def _alignment_panel_html(sections: list[dict], repo_root: str | None) -> str:
    p = ['<h2 class="panel-title">Requirement Alignment</h2>']
    if not sections:
        p.append('<p class="empty">No work items reviewed.</p>')
    for s in sections:
        p.append(_work_item_block_html(s, repo_root))
    return "".join(p)


def _page_shell(report: dict, *, subtitle: str, tabs: list[dict]) -> str:
    """Assemble the full page: sticky top bar (risk + push action), a summary
    chip strip, a tab nav, and one panel per tab (first is active).

    Each ``tabs`` entry is ``{"id", "label", "count" (int|None), "html"}``. The
    push UI lives once in the top bar; ``window.PRS_PUSH`` (injected by the push
    server) activates it, otherwise it stays inert.
    """
    risk = report["riskLevel"]
    p: list[str] = []
    p.append("<!DOCTYPE html>")
    p.append('<html lang="en"><head>')
    p.append('<meta charset="utf-8">')
    p.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    p.append(f"<title>PR Sentinel — {_e(subtitle)} ({_e(risk)} risk)</title>")
    p.append(f"<style>{_HTML_STYLE}{_ALIGNMENT_STYLE}</style>")
    # Replaced by the push server with a <script> defining window.PRS_PUSH.
    p.append(PUSH_CONFIG_PLACEHOLDER)
    p.append("</head><body>")

    # Sticky top bar: brand, risk badge, and the one push toolbar.
    p.append('<header class="topbar"><div class="topbar-inner">')
    p.append(
        '<div class="brand">🛡️ PR Sentinel '
        f'<span class="brand-sub">{_e(subtitle)}</span></div>'
    )
    p.append('<div class="topbar-right">')
    p.append(f'<span class="risk-badge risk-{risk}">{_e(risk)} risk</span>')
    p.append('<div class="push-area" id="push-bar">')
    p.append('<label class="select-all"><input type="checkbox" id="push-select-all"> All</label>')
    p.append('<button class="push-btn" id="push-btn" type="button" disabled>Push selected</button>')
    p.append("</div>")  # push-area
    p.append("</div>")  # topbar-right
    p.append("</div>")  # topbar-inner
    p.append('<div class="topbar-status"><span class="push-status" id="push-status"></span></div>')
    p.append("</header>")

    p.append(_chips_html(report))

    # Tab nav
    p.append('<nav class="tabs" role="tablist">')
    for i, t in enumerate(tabs):
        active = " active" if i == 0 else ""
        count = (
            f'<span class="tab-count">{t["count"]}</span>'
            if t.get("count")
            else ""
        )
        p.append(
            f'<button class="tab{active}" type="button" role="tab" '
            f'data-tab="{t["id"]}">{_e(t["label"])}{count}</button>'
        )
    p.append("</nav>")

    # Panels (first visible, rest hidden)
    p.append('<main class="wrap">')
    for i, t in enumerate(tabs):
        hidden = "" if i == 0 else " hidden"
        p.append(
            f'<section class="tabpanel{hidden}" role="tabpanel" '
            f'data-panel="{t["id"]}">{t["html"]}</section>'
        )
    p.append("</main>")

    p.append('<footer class="ftr">Generated by PR Sentinel.</footer>')
    p.append(f"<script>{_HTML_SCRIPT}</script>")
    p.append("</body></html>")
    return "\n".join(p) + "\n"


def _render_html(report: dict) -> str:
    findings = report["findings"]
    repo_root = report.get("repoRoot")
    tabs = [
        {"id": "overview", "label": "Overview", "count": None,
         "html": _overview_panel_html(report)},
        {"id": "code", "label": "Findings", "count": len(findings) or None,
         "html": _findings_panel_html(
             findings, repo_root, title="Findings", empty="No findings. 🎉")},
    ]
    return _page_shell(report, subtitle="Review Report", tabs=tabs)


# Alignment verdict badge palette (text, background, accent), reusing the risk
# colours: Satisfied≈clean, Partial≈medium, Not satisfied/Unknown≈high.
_VERDICT_COLORS = {
    "Satisfied": _RISK_COLORS["None"],
    "Partial": _RISK_COLORS["Medium"],
    "Not satisfied": _RISK_COLORS["High"],
    "Unknown": _RISK_COLORS["Unknown"],
}

# Per-criterion: (icon, label-prefix, css-class, coverage-bar-segment-class).
_CRITERION_HTML = {
    "Met": ("✓", "met", "cov-seg-met"),
    "Partial": ("~", "partial", "cov-seg-partial"),
    "Not met": ("✗", "notmet", "cov-seg-notmet"),
    "Unverifiable": ("?", "unverifiable", "cov-seg-unv"),
}
_STATUS_ORDER = ("Met", "Partial", "Not met", "Unverifiable")


def _coverage_bar(counts: dict[str, int], total: int) -> str:
    """A stacked proportional bar of criterion statuses (met/partial/…)."""
    if total <= 0:
        return ""
    segs: list[str] = []
    for status in _STATUS_ORDER:
        n = counts.get(status, 0)
        if not n:
            continue
        _, _, seg_cls = _CRITERION_HTML[status]
        pct = n * 100 / total
        segs.append(f'<span class="{seg_cls}" style="width:{pct:.4g}%" title="{n} {status}"></span>')
    return f'<div class="cov-bar">{"".join(segs)}</div>'


def _work_item_block_html(s: dict, repo_root: str | None) -> str:
    """One work item's scorecard + traceability matrix, with a push checkbox on
    the verdict. Shared by the alignment and combined renderers."""
    wi = s.get("workItem", {})
    verdict = s.get("verdict") or "Unknown"
    fg, bg, accent = _VERDICT_COLORS.get(verdict, _VERDICT_COLORS["Unknown"])
    conf = s.get("confidence") or "Low"
    conf_note = ' <span class="conf">low confidence</span>' if conf == "Low" else ""

    criteria = s.get("criteria") or []
    counts = {st: sum(1 for c in criteria if c.get("status") == st) for st in _STATUS_ORDER}
    total = len(criteria)
    checkable = total - counts["Unverifiable"]
    met = counts["Met"]

    # Verdict is pushable as a summary comment; id prefixed so the server
    # routes it to upsert_alignment_comment (vs create thread for gaps).
    align_id = html.escape(f"align:{wi.get('id')}", quote=True)
    p: list[str] = []
    p.append('<section class="block">')
    p.append(
        '<div class="wi-head">'
        f'<input type="checkbox" class="pick" data-finding-id="{align_id}" '
        'aria-label="Select verdict to push">'
        f'<span class="wi-id">#{_e(wi.get("id"))}</span>'
        f'<span class="wi-type">{_e(wi.get("type") or "Work Item")}</span>'
        + (f'<span class="wi-state">{_e(wi.get("state"))}</span>' if wi.get("state") else "")
        + f'<span class="wi-title">{_e(wi.get("title"))}</span>'
        f'<span class="push-mark" data-finding-id="{align_id}"></span>'
        "</div>"
    )

    # Scorecard: verdict badge + coverage bar + legend.
    p.append('<div class="scorecard">')
    p.append(
        f'<span class="verdict-badge" style="color:{fg};background:{bg};'
        f'border-left:5px solid {accent}">{_e(verdict)}{conf_note}</span>'
    )
    if total:
        cov_label = f"{met}/{checkable} met" if checkable else "no checkable criteria"
        legend = " · ".join(
            f'<b>{counts[st]}</b> {st}' for st in _STATUS_ORDER if counts[st]
        )
        p.append(
            '<div class="cov">'
            + _coverage_bar(counts, total)
            + f'<div class="cov-legend"><span>Coverage: <b>{cov_label}</b></span>'
            + (f"<span>{legend}</span>" if legend else "")
            + "</div></div>"
        )
    p.append("</div>")

    if s.get("summary"):
        p.append(f'<p class="summary-line">{_e(s.get("summary"))}</p>')
    if s.get("truncatedDiff"):
        p.append('<p class="note">⚠ Diff was truncated for size — this verdict is partial.</p>')

    # Traceability matrix.
    if criteria:
        p.append('<table class="trace"><thead><tr>'
                 "<th>Status</th><th>Criterion</th><th>Evidence</th>"
                 "</tr></thead><tbody>")
        for c in criteria:
            status = c.get("status", "Unverifiable")
            icon, cls, _ = _CRITERION_HTML.get(status, ("?", "unverifiable", "cov-seg-unv"))
            ev = c.get("evidence", "")
            ev_html = _rich_text_html(ev, repo_root) if str(ev).strip() else "—"
            p.append(
                f'<tr class="crit-{cls}">'
                f'<td class="status"><span class="crit-icon">{icon}</span> {_e(status)}</td>'
                f'<td>{_e(c.get("criterion"))}</td>'
                f'<td class="crit-ev">{ev_html}</td>'
                "</tr>"
            )
        p.append("</tbody></table>")
    else:
        p.append('<p class="note">No acceptance criteria on this item — judged against title/description.</p>')
    p.append("</section>")
    return "".join(p)


def _render_alignment_html(report: dict) -> str:
    """Render the requirement-alignment report as a tabbed page: Overview, then
    Alignment (per-work-item scorecard + traceability matrix), then Gaps.

    `report` is the dict written by `review-alignment` — a standard report
    envelope plus an `alignment` list of per-work-item sections.
    """
    sections = report.get("alignment", [])
    findings = report.get("findings", [])
    repo_root = report.get("repoRoot")
    tabs = [
        {"id": "overview", "label": "Overview", "count": None,
         "html": _overview_panel_html(report)},
        {"id": "alignment", "label": "Alignment", "count": len(sections) or None,
         "html": _alignment_panel_html(sections, repo_root)},
        {"id": "gaps", "label": "Gaps", "count": len(findings) or None,
         "html": _gaps_panel_html(findings, repo_root)},
    ]
    return _page_shell(report, subtitle="Requirement Alignment", tabs=tabs)


def _render_combined_html(report: dict) -> str:
    """Render code review *and* requirement alignment as one tabbed page.

    Used by `review --align`, which merges both passes into one report:
    ``findings`` holds the code-review findings plus the alignment gap findings
    (split apart here by agent so each shows once, under its own tab), and
    ``alignment`` holds the per-work-item verdict sections. The single push
    toolbar in the top bar spans every checkbox, so any mix of code findings,
    gaps, and verdicts is pushed in one POST.
    """
    all_findings = report["findings"]
    gap_findings = [f for f in all_findings if f.get("agent") == ALIGNMENT_AGENT_NAME]
    review_findings = [f for f in all_findings if f.get("agent") != ALIGNMENT_AGENT_NAME]
    sections = report.get("alignment", [])
    repo_root = report.get("repoRoot")
    tabs = [
        {"id": "overview", "label": "Overview", "count": None,
         "html": _overview_panel_html(report)},
        {"id": "code", "label": "Code Review", "count": len(review_findings) or None,
         "html": _findings_panel_html(
             review_findings, repo_root, title="Code Review Findings",
             empty="No code review findings. 🎉")},
        {"id": "alignment", "label": "Alignment", "count": len(sections) or None,
         "html": _alignment_panel_html(sections, repo_root)},
        {"id": "gaps", "label": "Gaps", "count": len(gap_findings) or None,
         "html": _gaps_panel_html(gap_findings, repo_root)},
    ]
    return _page_shell(report, subtitle="Review + Alignment", tabs=tabs)


_ALIGNMENT_STYLE = """
.wi-head .pick{flex:none;width:16px;height:16px;cursor:pointer;}
.wi-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:14px;}
.wi-id{font-weight:700;}
.wi-type{font-size:12px;color:var(--muted);background:#eaeef2;padding:1px 8px;border-radius:12px;}
.wi-state{font-size:12px;color:var(--muted);}
.wi-title{font-size:16px;font-weight:600;}
.scorecard{display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin-bottom:12px;}
.verdict-badge{font-size:15px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;
  padding:8px 14px;border-radius:8px;white-space:nowrap;}
.verdict-badge .conf{font-size:11px;font-weight:500;text-transform:none;letter-spacing:0;opacity:.85;}
.cov{flex:1;min-width:220px;}
.cov-bar{display:flex;height:14px;border-radius:7px;overflow:hidden;background:#eaeef2;}
.cov-bar span{display:block;height:100%;}
.cov-seg-met{background:#1a7f37;}
.cov-seg-partial{background:#bf8700;}
.cov-seg-notmet{background:#d1242f;}
.cov-seg-unv{background:#8c959f;}
.cov-legend{font-size:12px;color:var(--muted);margin-top:6px;display:flex;gap:14px;flex-wrap:wrap;}
.cov-legend b{color:var(--ink);}
.summary-line{font-size:14px;margin:0 0 12px;}
table.trace{width:100%;border-collapse:collapse;font-size:14px;}
table.trace th,table.trace td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;}
table.trace thead th{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.4px;}
table.trace td.status{white-space:nowrap;font-weight:600;}
table.trace .crit-icon{font-weight:700;}
.crit-met td.status{color:#1a7f37;}
.crit-partial td.status{color:#bf8700;}
.crit-notmet td.status{color:#d1242f;}
.crit-unverifiable td.status{color:#8c959f;}
table.trace td.crit-ev{color:var(--muted);}
"""


_HTML_STYLE = """
:root{--bg:#f6f8fa;--card:#fff;--ink:#1f2328;--muted:#59636e;--line:#d1d9e0;
  --accent:#0969da;--ok:#1a7f37;--ok-bg:#dafbe1;
  --hi:#d1242f;--hi-bg:#ffebe9;--hi-ink:#86181d;
  --me:#bf8700;--me-bg:#fff8c5;--me-ink:#7d4e00;
  --lo:#0969da;--lo-bg:#ddf4ff;--lo-ink:#0a3069;
  --radius:12px;--shadow:0 1px 3px rgba(31,35,40,.07),0 1px 2px rgba(31,35,40,.05);}
*{box-sizing:border-box;}
html{scroll-behavior:smooth;}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
code{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  font-size:.85em;background:#eaeef2;padding:1px 6px;border-radius:6px;}

/* sticky top bar */
.topbar{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.86);
  backdrop-filter:saturate(180%) blur(10px);-webkit-backdrop-filter:saturate(180%) blur(10px);
  border-bottom:1px solid var(--line);}
.topbar-inner{max-width:1060px;margin:0 auto;display:flex;align-items:center;
  justify-content:space-between;gap:16px;padding:12px 22px;flex-wrap:wrap;}
.brand{font-size:17px;font-weight:700;display:flex;align-items:center;gap:8px;}
.brand-sub{font-weight:500;color:var(--muted);font-size:14px;}
.topbar-right{display:flex;align-items:center;gap:14px;flex-wrap:wrap;}
.risk-badge{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;
  padding:5px 12px;border-radius:999px;white-space:nowrap;}
.risk-High,.risk-Unknown{color:var(--hi-ink);background:var(--hi-bg);}
.risk-Medium{color:var(--me-ink);background:var(--me-bg);}
.risk-Low{color:var(--lo-ink);background:var(--lo-bg);}
.risk-None{color:var(--ok);background:var(--ok-bg);}
.push-area{display:flex;align-items:center;gap:10px;}
.select-all{font-size:13px;color:var(--muted);display:flex;align-items:center;gap:6px;cursor:pointer;}
.push-btn{border:1px solid var(--ok);background:var(--ok);color:#fff;padding:7px 16px;
  border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:background .15s;}
.push-btn:hover:not(:disabled){background:#176d30;}
.push-btn:disabled{background:#9aa4ae;border-color:#9aa4ae;cursor:not-allowed;}
.topbar-status{max-width:1060px;margin:0 auto;padding:0 22px;}
.topbar-status:has(.push-status:empty){display:none;}
.push-status{font-size:13px;color:var(--muted);display:inline-block;padding-bottom:8px;}
.push-status:empty{display:none;}
.push-status.ok{color:var(--ok);}
.push-status.err{color:var(--hi);}

/* summary chips */
.chips{max-width:1060px;margin:0 auto;display:flex;gap:8px;flex-wrap:wrap;padding:14px 22px 0;}
.chip{font-size:12.5px;color:var(--muted);background:var(--card);border:1px solid var(--line);
  border-radius:999px;padding:4px 12px;display:inline-flex;gap:6px;align-items:center;}
.chip b{color:var(--ink);font-weight:700;}
.chip.hi b{color:var(--hi);}.chip.me b{color:var(--me);}.chip.lo b{color:var(--lo);}
.chip.ok{color:var(--ok);border-color:#aceebb;background:var(--ok-bg);}
.chip.warn{color:var(--me-ink);border-color:#e8d48a;background:var(--me-bg);}
button.chip{font:inherit;cursor:pointer;transition:box-shadow .12s,opacity .12s;}
button.chip:hover{opacity:.85;}
button.chip[data-filter-reset]:hover{box-shadow:0 0 0 2px var(--accent);}
button.chip.hi.active{box-shadow:0 0 0 2px var(--hi);font-weight:700;}
button.chip.me.active{box-shadow:0 0 0 2px var(--me);font-weight:700;}
button.chip.lo.active{box-shadow:0 0 0 2px var(--lo);font-weight:700;}
.filter-bar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:14px;}
.filter-label{font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.4px;}
.filter-btn{appearance:none;border:1px solid var(--line);background:var(--card);color:var(--muted);
  font-size:12px;font-weight:600;padding:4px 12px;border-radius:999px;cursor:pointer;transition:all .12s;}
.filter-btn:hover{border-color:var(--accent);color:var(--accent);}
.filter-btn.active{background:var(--accent);border-color:var(--accent);color:#fff;}
.filter-no-results{color:var(--muted);text-align:center;padding:24px 0;font-size:14px;}

/* tabs */
.tabs{max-width:1060px;margin:14px auto 0;padding:0 22px;display:flex;gap:2px;
  border-bottom:1px solid var(--line);flex-wrap:wrap;}
.tab{appearance:none;border:0;background:none;color:var(--muted);font-size:14px;
  font-weight:600;padding:10px 14px;cursor:pointer;border-bottom:2px solid transparent;
  margin-bottom:-1px;display:flex;align-items:center;gap:7px;}
.tab:hover{color:var(--ink);}
.tab.active{color:var(--ink);border-bottom-color:var(--accent);}
.tab-count{font-size:11px;font-weight:700;background:#eaeef2;color:var(--muted);
  padding:1px 7px;border-radius:999px;}
.tab.active .tab-count{background:var(--accent);color:#fff;}

/* layout + panels */
.wrap{max-width:1060px;margin:0 auto;padding:22px 22px 64px;}
.tabpanel{animation:fade .18s ease;}
.tabpanel.hidden{display:none;}
@keyframes fade{from{opacity:0;transform:translateY(4px);}to{opacity:1;transform:none;}}
.panel-title{font-size:13px;text-transform:uppercase;letter-spacing:.5px;
  color:var(--muted);margin:0 0 14px;}
.meta{color:var(--muted);font-size:13px;margin:0 0 16px;}
.muted{color:var(--muted);}
.kv{display:grid;grid-template-columns:130px 1fr;gap:9px 18px;font-size:14px;align-items:baseline;}
.kv-k{color:var(--muted);}
.kv-v{word-break:break-word;}

/* risk hero */
.hero{display:flex;flex-wrap:wrap;align-items:baseline;gap:14px;padding:18px 20px;
  border-radius:var(--radius);margin-bottom:18px;box-shadow:var(--shadow);}
.hero.risk-High,.hero.risk-Unknown{background:var(--hi-bg);border-left:6px solid var(--hi);}
.hero.risk-Medium{background:var(--me-bg);border-left:6px solid var(--me);}
.hero.risk-Low{background:var(--lo-bg);border-left:6px solid var(--lo);}
.hero.risk-None{background:var(--ok-bg);border-left:6px solid var(--ok);}
.hero-risk{font-size:19px;font-weight:800;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap;}
.hero-summary{font-size:14px;}
.note{color:var(--muted);font-size:13px;margin:4px 2px 0;}

/* cards / tables */
.block{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:18px 20px;margin-top:16px;box-shadow:var(--shadow);}
.block h2{margin:0 0 14px;font-size:16px;}
.verdict{padding:12px 16px;background:#f6f8fa;border-radius:8px;font-size:14px;
  border:1px solid var(--line);}
table.agents{width:100%;border-collapse:collapse;font-size:14px;}
table.agents th,table.agents td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--line);}
table.agents th:first-child,table.agents td:first-child{text-align:left;}
table.agents th:nth-child(2),table.agents td:nth-child(2){text-align:left;}
table.agents thead th{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.4px;}
table.agents tr.total td{font-weight:700;border-bottom:none;}
.pill{display:inline-block;padding:1px 9px;border-radius:20px;font-size:12px;font-weight:600;}
.pill.ok{color:var(--ok);background:var(--ok-bg);}
.pill.fail{color:var(--hi-ink);background:var(--hi-bg);}
.empty{color:var(--muted);text-align:center;padding:36px 0;font-size:15px;}

/* finding cards */
.push-mark{flex:none;font-size:13px;font-weight:600;margin-left:8px;}
.push-mark.ok{color:var(--ok);}
.push-mark.err{color:var(--hi);}
.finding summary .pick{flex:none;width:16px;height:16px;cursor:pointer;}
.finding{background:var(--card);border:1px solid var(--line);border-radius:10px;
  margin-bottom:10px;overflow:hidden;box-shadow:var(--shadow);}
.finding summary{display:flex;align-items:center;gap:10px;padding:12px 14px;
  cursor:pointer;list-style:none;}
.finding summary::-webkit-details-marker{display:none;}
.finding summary:hover{background:#f6f8fa;}
.finding[data-sev=High]{border-left:4px solid var(--hi);}
.finding[data-sev=Medium]{border-left:4px solid var(--me);}
.finding[data-sev=Low]{border-left:4px solid var(--lo);}
.badge{font-size:11px;font-weight:700;padding:2px 8px;border-radius:5px;
  text-transform:uppercase;letter-spacing:.4px;flex:none;}
.file{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:13px;font-weight:600;flex:none;}
.file-link{color:var(--accent);text-decoration:none;}
.file-link:hover{text-decoration:underline;}
.loc{color:var(--muted);font-weight:400;}
.agent-tag{font-size:12px;color:var(--muted);background:#eaeef2;
  padding:1px 8px;border-radius:12px;flex:none;margin-left:auto;}
.finding-body{padding:4px 16px 14px;border-top:1px solid var(--line);font-size:14px;}
.finding-body p{margin:10px 0 0;}
.finding-body ol,.finding-body ul{margin:8px 0 0;padding-left:24px;}
.finding-body li{margin:6px 0;}
.finding-body li:first-child{margin-top:0;}
.lbl{font-weight:700;}
.ftr{text-align:center;color:var(--muted);font-size:12px;margin-top:30px;}
@media(max-width:640px){.brand-sub{display:none;}.push-btn{padding:7px 12px;}}
"""

_HTML_SCRIPT = """
(function(){
  function qs(sel,root){return(root||document).querySelector(sel);}
  function qsa(sel,root){return[].slice.call((root||document).querySelectorAll(sel));}

  // ── Tab switching ──────────────────────────────────────────────────────────
  var tabs=qsa('.tab');
  var panels=qsa('.tabpanel');
  function switchTab(id){
    tabs.forEach(function(x){x.classList.toggle('active',x.getAttribute('data-tab')===id);});
    panels.forEach(function(p){p.classList.toggle('hidden',p.getAttribute('data-panel')!==id);});
    applyFilters();
  }
  tabs.forEach(function(t){
    t.addEventListener('click',function(){switchTab(t.getAttribute('data-tab'));});
  });

  // ── Filter state ───────────────────────────────────────────────────────────
  var activeSevs={};
  var activeAgents={};

  function applyFilters(){
    var sevKeys=Object.keys(activeSevs).filter(function(k){return activeSevs[k];});
    var agentKeys=Object.keys(activeAgents).filter(function(k){return activeAgents[k];});
    var panel=qs('.tabpanel:not(.hidden)');
    if(!panel) return;
    var findings=qsa('.finding',panel);
    var shown=0;
    findings.forEach(function(f){
      var ok=(sevKeys.length===0||activeSevs[f.getAttribute('data-sev')])&&
             (agentKeys.length===0||activeAgents[f.getAttribute('data-agent')||'']);
      f.style.display=ok?'':'none';
      if(ok) shown++;
    });
    var noRes=qs('.filter-no-results',panel);
    if(noRes) noRes.style.display=(findings.length>0&&shown===0)?'':'none';
    updateBtn();
  }

  // "N findings" chip — clears all filters and switches to findings tab.
  qsa('[data-filter-reset]').forEach(function(btn){
    btn.addEventListener('click',function(){
      activeSevs={};
      activeAgents={};
      qsa('[data-filter-sev]').forEach(function(b){b.classList.remove('active');});
      qsa('[data-filter-agent]').forEach(function(b){b.classList.remove('active');});
      var t=qs('.tab[data-tab="code"]')||qs('.tab[data-tab="gaps"]');
      if(t) t.click(); else applyFilters();
    });
  });

  // Severity chip buttons (in the chips bar at top of page)
  qsa('[data-filter-sev]').forEach(function(btn){
    btn.addEventListener('click',function(){
      var sev=btn.getAttribute('data-filter-sev');
      activeSevs[sev]=!activeSevs[sev];
      btn.classList.toggle('active',!!activeSevs[sev]);
      // If the active panel has no finding cards, switch to the code/gaps tab.
      var panel=qs('.tabpanel:not(.hidden)');
      if(panel&&qsa('.finding',panel).length===0){
        var t=qs('.tab[data-tab="code"]')||qs('.tab[data-tab="gaps"]');
        if(t){t.click();return;}
      }
      applyFilters();
    });
  });

  // Agent filter buttons (inside findings panel, via event delegation)
  document.addEventListener('click',function(e){
    if(!e.target.hasAttribute('data-filter-agent')) return;
    var agent=e.target.getAttribute('data-filter-agent');
    activeAgents[agent]=!activeAgents[agent];
    e.target.classList.toggle('active',!!activeAgents[agent]);
    applyFilters();
  });

  // ── Push selected items to Azure DevOps PR ────────────────────────────────
  // Active only when served by `pr-sentinel push-azure` / `review --pr`,
  // which inject window.PRS_PUSH.
  var cfg=window.PRS_PUSH;
  var btn=qs('#push-btn');
  var status=qs('#push-status');
  var selectAll=qs('#push-select-all');
  var picks=qsa('.pick');
  if(!btn) return;

  // offsetParent is null for elements inside display:none containers (hidden
  // tab panels or filtered-out finding cards).
  function visible(cb){return cb.offsetParent!==null;}
  function selectedIds(){
    return picks.filter(function(cb){return cb.checked&&!cb.disabled&&visible(cb);})
               .map(function(cb){return cb.getAttribute('data-finding-id');});
  }
  function updateBtn(){
    var n=selectedIds().length;
    btn.textContent=n?('Push selected ('+n+')'):'Push selected';
    if(cfg) btn.disabled=n===0;
  }

  // A checkbox inside <summary> would otherwise toggle the details panel.
  picks.forEach(function(cb){
    cb.addEventListener('click',function(e){e.stopPropagation();});
    cb.addEventListener('change',updateBtn);
  });
  if(selectAll){
    selectAll.addEventListener('change',function(){
      picks.forEach(function(cb){
        if(!cb.disabled&&visible(cb)) cb.checked=selectAll.checked;
      });
      updateBtn();
    });
  }

  function markPushed(id,label){
    var mark=document.querySelector('.push-mark[data-finding-id="'+id+'"]');
    if(mark){mark.className='push-mark ok';mark.textContent=label||'✓ pushed';}
    var cb=document.querySelector('.pick[data-finding-id="'+id+'"]');
    if(cb){
      cb.checked=false;
      // Alignment verdicts (align:*) are upserted — re-pushing refreshes them,
      // so keep them selectable. Finding/gap threads are idempotent, so lock.
      if(id.indexOf('align:')!==0){cb.disabled=true;}
    }
  }
  function markError(id,msg){
    var mark=document.querySelector('.push-mark[data-finding-id="'+id+'"]');
    if(mark){mark.className='push-mark err';mark.textContent='✗ '+(msg||'failed');}
  }

  if(!cfg){
    btn.disabled=true;
    if(status) status.textContent='Serve this report with `pr-sentinel review --pr <id>` (or `push-azure --pr <id>`) to push.';
    return;
  }

  // On load, mark items already commented on the PR so you don't re-select them.
  if(cfg.statusUrl){
    fetch(cfg.statusUrl+'?token='+encodeURIComponent(cfg.token))
      .then(function(r){return r.json();})
      .then(function(data){
        var ids=(data&&data.ids)||[];
        ids.forEach(function(id){markPushed(id,'✓ already pushed');});
        if(ids.length&&status){
          status.className='push-status';
          status.textContent=ids.length+' item(s) already pushed to this PR.';
        }
        updateBtn();
      })
      .catch(function(){/* non-fatal: leave the page as-is */});
  }

  btn.addEventListener('click',function(){
    var ids=selectedIds();
    if(!ids.length) return;
    btn.disabled=true;
    if(status){status.className='push-status';status.textContent='Pushing '+ids.length+' item(s)…';}
    fetch(cfg.url,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ids:ids,token:cfg.token})})
      .then(function(r){return r.json();})
      .then(function(data){
        if(data.error){throw new Error(data.error);}
        var ok=0,fail=0;
        (data.results||[]).forEach(function(res){
          if(res.ok){markPushed(res.id,res.skipped?'✓ already pushed':'✓ pushed');ok++;}
          else{markError(res.id,res.error);fail++;}
        });
        if(status){
          status.className='push-status '+(fail?'err':'ok');
          status.textContent='Pushed '+ok+' item(s)'+(fail?(', '+fail+' failed'):'')+'.';
        }
        updateBtn();
      })
      .catch(function(err){
        if(status){status.className='push-status err';status.textContent='Push failed: '+err.message;}
        btn.disabled=false;
      });
  });
  updateBtn();
})();
"""
