"""Shared logic for provider runners (claude, copilot).

Each provider exposes the same surface: a `run_json(prompt, ...)` that shells
out to its CLI and returns parsed JSON. The only thing that differs between
providers is *how the subprocess is invoked* (binary name and flags). That
difference is captured in an `invoke` callable; everything else — JSON
extraction, caching, and the retry-once-on-bad-JSON dance — lives here.
"""
import json
import re
from typing import Callable

import click

from pr_sentinel import cache
from pr_sentinel.config import DEFAULT_TIMEOUT

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_FIRST_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

RETRY_NUDGE = (
    "\n\nIMPORTANT: Your previous response was not valid JSON. "
    "Return ONLY a single JSON object matching the schema. No prose, no code fences."
)


class RunnerError(click.ClickException):
    """Raised when a provider CLI fails or returns unparseable output."""


def extract_json(text: str) -> dict:
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
    invoke: Callable[[str, int, str | None], str],
    provider: str,
    prompt: str,
    timeout: int = DEFAULT_TIMEOUT,
    model: str | None = None,
    use_cache: bool = True,
) -> dict:
    """Invoke a provider CLI and return parsed JSON. Retries once on parse failure.

    `invoke(prompt, timeout, model) -> str` runs the actual subprocess and
    returns its stdout. Successful responses are cached under
    sha256(provider + model + prompt). Cache hits skip the subprocess entirely.
    Failures (timeouts, exit codes, JSON-parse errors) are never cached.
    """
    key = cache.cache_key(prompt, model, provider) if use_cache else None
    if key is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached

    raw = invoke(prompt, timeout, model)
    try:
        parsed = extract_json(raw)
        if key is not None:
            cache.set(key, parsed)
        return parsed
    except (ValueError, json.JSONDecodeError):
        pass

    raw = invoke(prompt + RETRY_NUDGE, timeout, model)
    try:
        parsed = extract_json(raw)
        if key is not None:
            cache.set(key, parsed)
        return parsed
    except (ValueError, json.JSONDecodeError) as e:
        raise RunnerError(
            f"{provider} returned non-JSON output after retry: {raw[:300]!r}"
        ) from e
