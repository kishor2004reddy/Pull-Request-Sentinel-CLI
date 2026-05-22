import json
import re
import shutil
import subprocess

import click

from pr_sentinel import cache

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


def _invoke(prompt: str, timeout: int, model: str | None = None) -> str:
    claude = _ensure_claude_available()
    args = [claude]
    if model:
        args.extend(["--model", model])
    args.append("-p")
    try:
        result = subprocess.run(
            args,
            input=prompt,
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


def run_json(
    prompt: str,
    timeout: int = 600,
    model: str | None = None,
    use_cache: bool = True,
) -> dict:
    """Invoke `claude -p` and return parsed JSON. Retries once on parse failure.

    Successful responses are cached under sha256(model + prompt). Cache hits skip
    the subprocess entirely. Failures (timeouts, exit codes, JSON-parse errors)
    are never cached.
    """
    key = cache.cache_key(prompt, model) if use_cache else None
    if key is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached

    raw = _invoke(prompt, timeout, model)
    try:
        parsed = _extract_json(raw)
        if key is not None:
            cache.set(key, parsed)
        return parsed
    except (ValueError, json.JSONDecodeError):
        pass

    raw = _invoke(prompt + RETRY_NUDGE, timeout, model)
    try:
        parsed = _extract_json(raw)
        if key is not None:
            cache.set(key, parsed)
        return parsed
    except (ValueError, json.JSONDecodeError) as e:
        raise ClaudeRunnerError(
            f"claude returned non-JSON output after retry: {raw[:300]!r}"
        ) from e
