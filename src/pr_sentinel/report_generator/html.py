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

from pr_sentinel.config import PUSH_CONFIG_PLACEHOLDER
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


def _render_html(report: dict) -> str:
    findings = report["findings"]
    risk = report["riskLevel"]

    counts = {"High": 0, "Medium": 0, "Low": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    raw_count = report.get("rawFindingCount")

    fg, bg, accent = _RISK_COLORS.get(risk, _RISK_COLORS["Unknown"])
    coverage_ok = report.get("coverageComplete", True)
    repo_root = report.get("repoRoot")

    p: list[str] = []
    p.append("<!DOCTYPE html>")
    p.append('<html lang="en"><head>')
    p.append('<meta charset="utf-8">')
    p.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    p.append(f"<title>PR Sentinel Review — {_e(risk)} risk</title>")
    p.append(f"<style>{_HTML_STYLE}</style>")
    # The push server replaces this placeholder with a <script> that defines
    # window.PRS_PUSH (endpoint + nonce). Left inert in the on-disk report.
    p.append(PUSH_CONFIG_PLACEHOLDER)
    p.append("</head><body>")
    p.append('<main class="wrap">')

    # Header
    p.append('<header class="hdr">')
    p.append('<div class="hdr-title">🛡️ PR Sentinel <span class="hdr-sub">Review Report</span></div>')
    meta = [
        f"base <code>{_e(report['baseBranch'])}</code>",
        f"source <code>{_e(report['source'])}</code>",
        f"{len(report['agentsExecuted'])} agent(s)",
        _e(report["reviewedAt"]),
    ]
    p.append('<div class="hdr-meta">' + " · ".join(meta) + "</div>")
    p.append("</header>")

    # Risk banner
    p.append(
        f'<section class="banner" style="color:{fg};background:{bg};border-left:6px solid {accent}">'
        f'<span class="banner-risk">Risk: {_e(risk)}</span>'
        f'<span class="banner-summary">{_e(report["summary"])}</span>'
        f"</section>"
    )

    # Stat cards
    cov_label = "Complete" if coverage_ok else "Incomplete"
    cov_class = "ok" if coverage_ok else "warn"
    cards = [
        ("Findings", str(len(findings)), ""),
        ("High", str(counts["High"]), "sev-high"),
        ("Medium", str(counts["Medium"]), "sev-medium"),
        ("Low", str(counts["Low"]), "sev-low"),
        ("Coverage", cov_label, cov_class),
    ]
    p.append('<section class="cards">')
    for label, value, cls in cards:
        p.append(
            f'<div class="card {cls}"><div class="card-val">{_e(value)}</div>'
            f'<div class="card-lbl">{_e(label)}</div></div>'
        )
    p.append("</section>")
    if raw_count is not None and raw_count != len(findings):
        p.append(
            f'<p class="note">Cleaned from {raw_count} raw finding(s) by the summary pass.</p>'
        )

    # Merge verdict
    p.append('<section class="block">')
    p.append("<h2>Merge Verdict</h2>")
    p.append(f'<div class="verdict" style="border-left:4px solid {accent}">{_multiline_html(_merge_verdict(report))}</div>')
    p.append("</section>")

    # Findings by agent
    rows, totals = _agent_summary_data(report)
    p.append('<section class="block">')
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
    p.append("</section>")

    # All findings (filterable, collapsible)
    p.append('<section class="block">')
    p.append('<div class="findings-head"><h2>Findings</h2>')
    if findings:
        p.append('<div class="filters" role="group" aria-label="Filter by severity">')
        p.append('<button class="filter active" data-sev="all">All</button>')
        for sev in ("High", "Medium", "Low"):
            if counts[sev]:
                p.append(f'<button class="filter" data-sev="{sev}">{sev} ({counts[sev]})</button>')
        p.append("</div>")
    p.append("</div>")

    # Push toolbar — select findings via the checkboxes below and push the
    # selection to an Azure DevOps PR. Only functional when this page is served
    # by `pr-sentinel push-azure` (which injects PRS_PUSH); otherwise the JS
    # disables the button and shows how to enable it.
    if findings:
        p.append('<div class="push-bar" id="push-bar">')
        p.append('<label class="push-all"><input type="checkbox" id="push-select-all"> Select all</label>')
        p.append('<button class="push-btn" id="push-btn" type="button" disabled>Push selected to PR</button>')
        p.append('<span class="push-status" id="push-status"></span>')
        p.append("</div>")

    if not findings:
        p.append('<p class="empty">No findings. 🎉</p>')
    else:
        for severity in ("High", "Medium", "Low"):
            for f in (x for x in findings if x["severity"] == severity):
                sev_fg, sev_bg = _SEV_COLORS[severity]
                file_html = _file_cell(f["file"], f.get("lineHint"), repo_root)
                fid = html.escape(str(f.get("id", "")), quote=True)
                p.append(f'<details class="finding" data-sev="{severity}" data-finding-id="{fid}">')
                p.append(
                    "<summary>"
                    f'<input type="checkbox" class="pick" data-finding-id="{fid}" '
                    'aria-label="Select finding to push">'
                    f'<span class="badge" style="color:{sev_fg};background:{sev_bg}">{severity}</span>'
                    f'<span class="file">{file_html}</span>'
                    f'<span class="agent-tag">{_e(f.get("agent"))}</span>'
                    f'<span class="push-mark" data-finding-id="{fid}"></span>'
                    "</summary>"
                )
                p.append('<div class="finding-body">')
                p.append(f'<p><span class="lbl">Issue.</span> {_rich_text_html(f.get("issue", ""), repo_root)}</p>')
                if str(f.get("reasoning", "")).strip():
                    p.append(f'<p><span class="lbl">Reasoning.</span> {_rich_text_html(f["reasoning"], repo_root)}</p>')
                if str(f.get("recommendation", "")).strip():
                    p.append(f'<p><span class="lbl">Recommendation.</span> {_rich_text_html(f["recommendation"], repo_root)}</p>')
                p.append("</div></details>")
    p.append("</section>")

    p.append('<footer class="ftr">Generated by PR Sentinel.</footer>')
    p.append("</main>")
    p.append(f"<script>{_HTML_SCRIPT}</script>")
    p.append("</body></html>")
    return "\n".join(p) + "\n"


_HTML_STYLE = """
:root{--bg:#f4f5f7;--card:#fff;--ink:#1f2328;--muted:#656d76;--line:#d0d7de;}
*{box-sizing:border-box;}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
code{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  font-size:.85em;background:#eaeef2;padding:1px 5px;border-radius:5px;}
.wrap{max-width:960px;margin:0 auto;padding:28px 20px 60px;}
.hdr{margin-bottom:18px;}
.hdr-title{font-size:24px;font-weight:700;}
.hdr-sub{color:var(--muted);font-weight:500;}
.hdr-meta{color:var(--muted);font-size:13px;margin-top:6px;}
.banner{display:flex;flex-wrap:wrap;align-items:baseline;gap:12px;
  padding:16px 20px;border-radius:10px;margin-bottom:18px;}
.banner-risk{font-size:18px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;}
.banner-summary{font-size:14px;}
.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:8px;}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:16px 12px;text-align:center;box-shadow:0 1px 2px rgba(27,31,36,.04);}
.card-val{font-size:26px;font-weight:700;line-height:1.1;}
.card-lbl{font-size:12px;color:var(--muted);margin-top:4px;text-transform:uppercase;letter-spacing:.4px;}
.card.sev-high .card-val{color:#d1242f;}
.card.sev-medium .card-val{color:#bf8700;}
.card.sev-low .card-val{color:#0969da;}
.card.ok .card-val{color:#1a7f37;font-size:18px;}
.card.warn .card-val{color:#bf8700;font-size:18px;}
.note{color:var(--muted);font-size:13px;margin:4px 2px 0;}
.block{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:18px 20px;margin-top:18px;box-shadow:0 1px 2px rgba(27,31,36,.04);}
.block h2{margin:0 0 14px;font-size:16px;}
.verdict{padding:10px 14px;background:#f6f8fa;border-radius:6px;font-size:14px;}
table.agents{width:100%;border-collapse:collapse;font-size:14px;}
table.agents th,table.agents td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--line);}
table.agents th:first-child,table.agents td:first-child{text-align:left;}
table.agents th:nth-child(2),table.agents td:nth-child(2){text-align:left;}
table.agents thead th{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.4px;}
table.agents tr.total td{font-weight:700;border-bottom:none;}
.pill{display:inline-block;padding:1px 9px;border-radius:20px;font-size:12px;font-weight:600;}
.pill.ok{color:#1a7f37;background:#dafbe1;}
.pill.fail{color:#86181d;background:#ffebe9;}
.findings-head{display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;gap:10px;margin-bottom:14px;}
.findings-head h2{margin:0;}
.filters{display:flex;gap:6px;flex-wrap:wrap;}
.filter{border:1px solid var(--line);background:#fff;color:var(--ink);
  padding:5px 12px;border-radius:20px;font-size:13px;cursor:pointer;}
.filter:hover{background:#f3f4f6;}
.filter.active{background:#1f2328;color:#fff;border-color:#1f2328;}
.empty{color:var(--muted);text-align:center;padding:24px 0;}
.push-bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
  padding:10px 12px;margin-bottom:14px;background:#f6f8fa;
  border:1px solid var(--line);border-radius:8px;}
.push-all{font-size:13px;color:var(--muted);display:flex;align-items:center;gap:6px;cursor:pointer;}
.push-btn{border:1px solid #1a7f37;background:#1a7f37;color:#fff;
  padding:6px 14px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;}
.push-btn:hover:not(:disabled){background:#176d30;}
.push-btn:disabled{background:#8c959f;border-color:#8c959f;cursor:not-allowed;}
.push-status{font-size:13px;color:var(--muted);}
.push-status.ok{color:#1a7f37;}
.push-status.err{color:#d1242f;}
.finding summary .pick{flex:none;width:16px;height:16px;cursor:pointer;}
.push-mark{flex:none;font-size:13px;font-weight:600;margin-left:8px;}
.push-mark.ok{color:#1a7f37;}
.push-mark.err{color:#d1242f;}
.finding{border:1px solid var(--line);border-radius:8px;margin-bottom:10px;overflow:hidden;}
.finding summary{display:flex;align-items:center;gap:10px;padding:11px 14px;
  cursor:pointer;list-style:none;}
.finding summary::-webkit-details-marker{display:none;}
.finding summary:hover{background:#f6f8fa;}
.finding[data-sev=High]{border-left:4px solid #d1242f;}
.finding[data-sev=Medium]{border-left:4px solid #bf8700;}
.finding[data-sev=Low]{border-left:4px solid #0969da;}
.badge{font-size:11px;font-weight:700;padding:2px 8px;border-radius:5px;
  text-transform:uppercase;letter-spacing:.4px;flex:none;}
.file{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:13px;font-weight:600;flex:none;}
.file-link{color:#0969da;text-decoration:none;}
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
@media(max-width:680px){.cards{grid-template-columns:repeat(2,1fr);}}
"""

_HTML_SCRIPT = """
(function(){
  var buttons=document.querySelectorAll('.filter');
  var findings=document.querySelectorAll('.finding');
  buttons.forEach(function(btn){
    btn.addEventListener('click',function(){
      var sev=btn.getAttribute('data-sev');
      buttons.forEach(function(b){b.classList.remove('active');});
      btn.classList.add('active');
      findings.forEach(function(f){
        f.style.display=(sev==='all'||f.getAttribute('data-sev')===sev)?'':'none';
      });
    });
  });
})();

// Push selected findings to an Azure DevOps PR. Active only when this page is
// served by `pr-sentinel push-azure`, which injects window.PRS_PUSH.
(function(){
  var cfg=window.PRS_PUSH;
  var bar=document.getElementById('push-bar');
  if(!bar) return;
  var btn=document.getElementById('push-btn');
  var status=document.getElementById('push-status');
  var selectAll=document.getElementById('push-select-all');
  var picks=Array.prototype.slice.call(document.querySelectorAll('.pick'));

  function selectedIds(){
    return picks.filter(function(cb){return cb.checked&&!cb.disabled;})
                .map(function(cb){return cb.getAttribute('data-finding-id');});
  }
  function updateBtn(){ if(cfg) btn.disabled=selectedIds().length===0; }

  // A checkbox inside <summary> would otherwise toggle the details panel.
  picks.forEach(function(cb){
    cb.addEventListener('click',function(e){e.stopPropagation();});
    cb.addEventListener('change',updateBtn);
  });
  if(selectAll){
    selectAll.addEventListener('click',function(e){e.stopPropagation();});
    selectAll.addEventListener('change',function(){
      picks.forEach(function(cb){
        if(cb.disabled) return;
        var f=cb.closest('.finding');
        if(!f||f.style.display!=='none') cb.checked=selectAll.checked;
      });
      updateBtn();
    });
  }

  if(!cfg){
    btn.disabled=true;
    status.textContent='Serve this report with `pr-sentinel push-azure --pr <id>` to push.';
    return;
  }

  btn.addEventListener('click',function(){
    var ids=selectedIds();
    if(!ids.length) return;
    btn.disabled=true;
    status.className='push-status';
    status.textContent='Pushing '+ids.length+' finding(s)…';
    fetch(cfg.url,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ids:ids,token:cfg.token})})
      .then(function(r){return r.json();})
      .then(function(data){
        if(data.error){throw new Error(data.error);}
        var ok=0,fail=0;
        (data.results||[]).forEach(function(res){
          var mark=document.querySelector('.push-mark[data-finding-id="'+res.id+'"]');
          if(mark){mark.className='push-mark '+(res.ok?'ok':'err');
            mark.textContent=res.ok?'✓ pushed':('✗ '+(res.error||'failed'));}
          var cb=document.querySelector('.pick[data-finding-id="'+res.id+'"]');
          if(res.ok&&cb){cb.checked=false;cb.disabled=true;}
          res.ok?ok++:fail++;
        });
        status.className='push-status '+(fail?'err':'ok');
        status.textContent='Pushed '+ok+' finding(s)'+(fail?(', '+fail+' failed'):'')+'.';
        updateBtn();
      })
      .catch(function(err){
        status.className='push-status err';
        status.textContent='Push failed: '+err.message;
        btn.disabled=false;
      });
  });
  updateBtn();
})();
"""
