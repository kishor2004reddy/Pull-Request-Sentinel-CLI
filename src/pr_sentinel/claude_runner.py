import json
import re
import shutil
import subprocess

import click

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_FIRST_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

RETRY_NUDGE = (
    "\n\nIMPORTANT: Your previous response was not valid JSON. "
    "Return ONLY a single JSON object matching the schema. No prose, no code fences."
)


class ClaudeRunnerError(click.ClickException):
    pass


def _ensure_claude_available() -> str:
    path = shutil.which("claude")
    if not path:
        raise ClaudeRunnerError(
            "claude CLI not found on PATH. Install Claude Code and ensure `claude` is runnable."
        )
    return path


def _invoke(prompt: str, timeout: int) -> str:
    claude = _ensure_claude_available()
    try:
        result = subprocess.run(
            [claude, "-p", prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise ClaudeRunnerError(f"claude -p timed out after {timeout}s") from e

    if result.returncode != 0:
        raise ClaudeRunnerError(
            f"claude -p exited {result.returncode}: "
            f"{(result.stderr or result.stdout).strip()[:500]}"
        )
    return result.stdout


def _extract_json(text: str) -> dict:
    text = text.strip()

    fenced = _JSON_FENCE.search(text)
    if fenced:
        return json.loads(fenced.group(1))

    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    match = _FIRST_OBJECT.search(text)
    if match:
        return json.loads(match.group(0))

    raise ValueError("no JSON object found in output")


def run_json(prompt: str, timeout: int = 180) -> dict:
    """Invoke `claude -p` and return parsed JSON. Retries once on parse failure."""
    raw = _invoke(prompt, timeout)
    try:
        return _extract_json(raw)
    except (ValueError, json.JSONDecodeError):
        pass

    raw = _invoke(prompt + RETRY_NUDGE, timeout)
    try:
        return _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as e:
        raise ClaudeRunnerError(
            f"claude returned non-JSON output after retry: {raw[:300]!r}"
        ) from e
