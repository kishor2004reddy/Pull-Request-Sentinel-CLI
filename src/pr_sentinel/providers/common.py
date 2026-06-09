"""Shared logic for provider runners (claude, copilot).

Each provider exposes the same surface: a `run_json(prompt, ...)` that shells
out to its CLI and returns parsed JSON. The only thing that differs between
providers is *how the subprocess is invoked* (binary name and flags). That
difference is captured in an `invoke` callable; everything else — JSON
extraction, caching, and the retry-once-on-bad-JSON dance — lives here.
"""
import json
import re
import time
from typing import Callable

import click

from pr_sentinel import cache, runstats
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
    invoke: Callable[[str, int, str | None], tuple[str, dict]],
    provider: str,
    prompt: str,
    timeout: int = DEFAULT_TIMEOUT,
    model: str | None = None,
    use_cache: bool = True,
) -> dict:
    """Invoke a provider CLI and return parsed JSON. Retries once on parse failure.

    `invoke(prompt, timeout, model) -> (answer_text, usage)` runs the actual
    subprocess: it returns the model's answer text plus a usage dict (tokens,
    cost, etc.). Each invocation is timed and recorded in `runstats` — including
    a retry, which is a second billed call. Successful responses are cached under
    sha256(provider + model + prompt); cache hits skip the subprocess entirely.
    Failures (timeouts, exit codes, JSON-parse errors) are never cached.
    """
    key = cache.cache_key(prompt, model, provider) if use_cache else None
    if key is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached

    answer, parsed = _invoke_timed(invoke, provider, model, prompt, timeout)
    if parsed is None:
        answer, parsed = _invoke_timed(
            invoke, provider, model, prompt + RETRY_NUDGE, timeout
        )
    if parsed is None:
        raise RunnerError(
            f"{provider} returned non-JSON output after retry: {answer[:300]!r}"
        )

    if key is not None:
        cache.set(key, parsed)
    return parsed


def _invoke_timed(
    invoke: Callable[[str, int, str | None], tuple[str, dict]],
    provider: str,
    model: str | None,
    prompt: str,
    timeout: int,
) -> tuple[str, dict | None]:
    """Run one invocation, record its timing/usage, and try to parse the answer.

    Returns (answer_text, parsed_or_None). The call is recorded in `runstats`
    regardless of whether the answer parsed, since it was billed either way.
    """
    t0 = time.perf_counter()
    answer, usage = invoke(prompt, timeout, model)
    runstats.record_call(provider, model, time.perf_counter() - t0, usage)
    try:
        return answer, extract_json(answer)
    except (ValueError, json.JSONDecodeError):
        return answer, None
