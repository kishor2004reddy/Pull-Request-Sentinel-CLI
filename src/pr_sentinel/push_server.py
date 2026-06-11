"""Local companion server backing the HTML report's "Push selected" button.

A self-contained ``file://`` report can't call the Azure DevOps API directly:
the PAT must not live in the browser, and the cross-origin call is blocked. So
``pr-sentinel push-azure`` starts this short-lived server on loopback, serves the
report (with a one-time nonce injected), and turns the browser's ``POST /push``
into authenticated Azure DevOps REST calls.

stdlib only — no extra dependencies.
"""
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from pr_sentinel.config import PUSH_CONFIG_PLACEHOLDER, PUSH_SERVER_HOST
from pr_sentinel.integrations.azure_devops import (
    AzureDevOpsClient,
    AzureDevOpsError,
    WorkItem,
    format_alignment_comment,
    format_finding_comment,
    line_from_hint,
)
from pr_sentinel.report_generator import _render_alignment_html, _render_html


def _push_alignment(client: AzureDevOpsClient, pr_id: int, section: dict) -> dict:
    """Post (or refresh) one work item's alignment-summary comment."""
    wi_dict = section.get("workItem", {})
    wi = WorkItem(
        id=int(wi_dict.get("id", 0) or 0),
        type=str(wi_dict.get("type", "") or ""),
        state=str(wi_dict.get("state", "") or ""),
        title=str(wi_dict.get("title", "") or ""),
    )
    content = format_alignment_comment(wi, section)
    client.upsert_alignment_comment(pr_id, wi.id, content)
    # upsert always refreshes, so report it as updated rather than skipped.
    return {"ok": True, "updated": True}


def _push_items(
    client: AzureDevOpsClient,
    pr_id: int,
    findings_by_id: dict[str, dict],
    alignment_by_id: dict[str, dict],
    ids: list[str],
) -> list[dict]:
    """Push each selected item; route ``align:*`` ids to summary comments and
    finding ids to comment threads. Returns one result dict per id.

    Gap findings already posted on a previous run (detected via the thread
    property marker) are reported as ``ok`` with ``skipped: True`` rather than
    duplicated. Alignment summaries are upserted, so they always refresh.
    """
    already = client.list_thread_finding_ids(pr_id)
    results: list[dict] = []
    for fid in ids:
        try:
            if fid in alignment_by_id:
                results.append({"id": fid, **_push_alignment(client, pr_id, alignment_by_id[fid])})
                continue
            finding = findings_by_id.get(fid)
            if finding is None:
                results.append({"id": fid, "ok": False, "error": "unknown item"})
                continue
            if fid in already:
                results.append({"id": fid, "ok": True, "skipped": True})
                continue
            client.create_pr_thread(
                pr_id,
                format_finding_comment(finding),
                fid,
                file_path=finding.get("file") or None,
                line=line_from_hint(finding.get("lineHint")),
            )
            results.append({"id": fid, "ok": True})
        except AzureDevOpsError as e:
            results.append({"id": fid, "ok": False, "error": str(e)})
    return results


def _build_handler(html_page: str, findings_by_id: dict, alignment_by_id: dict, client, pr_id, nonce, on_event):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence default stderr logging
            pass

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 (stdlib naming)
            parts = urlsplit(self.path)
            if parts.path in ("/", "/index.html", "/report"):
                self._send(200, html_page.encode("utf-8"), "text/html; charset=utf-8")
            elif parts.path == "/pushed":
                # Live list of finding ids already commented on the PR, so the
                # page can mark them on load (and after a refresh / server restart).
                token = (parse_qs(parts.query).get("token") or [""])[0]
                if not secrets.compare_digest(token, nonce):
                    self._reply({"error": "invalid or missing session token"}, 403)
                    return
                self._reply({"ids": sorted(client.list_thread_finding_ids(pr_id))}, 200)
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")

        def do_POST(self):  # noqa: N802
            if self.path != "/push":
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._reply({"error": "invalid request body"}, 400)
                return
            if not secrets.compare_digest(str(payload.get("token", "")), nonce):
                self._reply({"error": "invalid or missing session token"}, 403)
                return
            ids = [str(i) for i in (payload.get("ids") or [])]
            if not ids:
                self._reply({"error": "no findings selected"}, 400)
                return
            results = _push_items(client, pr_id, findings_by_id, alignment_by_id, ids)
            on_event(results)
            self._reply({"results": results}, 200)

        def _reply(self, obj: dict, code: int) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"),
                       "application/json; charset=utf-8")

    return Handler


def start_server(
    report: dict,
    client: AzureDevOpsClient,
    pr_id: int,
    host: str = PUSH_SERVER_HOST,
    port: int = 0,
    open_browser: bool = True,
    on_event=lambda results: None,
) -> tuple[str, ThreadingHTTPServer]:
    """Start the push server in a background thread; return ``(url, httpd)``.

    Renders the report HTML with a one-time nonce injected so only this browser
    session can trigger a push, and (optionally) opens it in the default
    browser. The caller owns the lifetime: serve until interrupted, then call
    ``httpd.shutdown()``. ``on_event(results)`` is invoked after each push so the
    CLI can log what happened.
    """
    findings_by_id = {
        f["id"]: f for f in report.get("findings", []) if f.get("id")
    }
    # Alignment reports carry per-work-item verdict sections, pushable as summary
    # comments under an "align:<workItemId>" id. Their presence also selects the
    # alignment renderer (verdict scorecard + traceability matrix).
    alignment_sections = report.get("alignment") or []
    alignment_by_id = {
        f"align:{s['workItem']['id']}": s
        for s in alignment_sections
        if s.get("workItem", {}).get("id") is not None
    }
    render = _render_alignment_html if alignment_sections else _render_html

    nonce = secrets.token_urlsafe(24)
    config_script = (
        "<script>window.PRS_PUSH="
        + json.dumps({"url": "/push", "statusUrl": "/pushed", "token": nonce})
        + ";</script>"
    )
    html_page = render(report).replace(PUSH_CONFIG_PLACEHOLDER, config_script)

    handler = _build_handler(
        html_page, findings_by_id, alignment_by_id, client, pr_id, nonce, on_event
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{httpd.server_address[1]}/"

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    return url, httpd
