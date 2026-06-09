import json
import shutil
import subprocess

from pr_sentinel.config import DEFAULT_TIMEOUT
from pr_sentinel.providers import common

PROVIDER = "claude"

# Backwards-compatible alias: this used to be a dedicated exception class.
ClaudeRunnerError = common.RunnerError


def _ensure_claude_available() -> str:
    path = shutil.which("claude")
    if not path:
        raise ClaudeRunnerError(
            "claude CLI not found on PATH. Install Claude Code and ensure `claude` is runnable."
        )
    return path


def _invoke(prompt: str, timeout: int, model: str | None = None) -> tuple[str, dict]:
    claude = _ensure_claude_available()
    args = [claude]
    if model:
        args.extend(["--model", model])
    # --output-format json wraps the answer in a metadata envelope that also
    # carries token usage and cost; _parse_envelope unwraps both.
    args.extend(["-p", "--output-format", "json"])
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
    return _parse_envelope(result.stdout)


def _parse_envelope(stdout: str) -> tuple[str, dict]:
    """Unwrap claude's --output-format json envelope into (answer_text, usage).

    The model's answer lives in `result`; tokens/cost live alongside it. Total
    input tokens sum the fresh, cache-creation, and cache-read counts (Claude
    Code injects a large cached system context, so cache tokens dominate).
    """
    try:
        env = json.loads(stdout)
    except (ValueError, json.JSONDecodeError) as e:
        raise ClaudeRunnerError(
            f"claude returned a non-JSON envelope: {stdout[:300]!r}"
        ) from e

    if env.get("is_error"):
        raise ClaudeRunnerError(
            f"claude reported an error: {str(env.get('result'))[:300]}"
        )

    answer = str(env.get("result", ""))
    u = env.get("usage") or {}
    if u:
        input_tokens = (
            (u.get("input_tokens") or 0)
            + (u.get("cache_creation_input_tokens") or 0)
            + (u.get("cache_read_input_tokens") or 0)
        )
        output_tokens = u.get("output_tokens")
    else:
        input_tokens = None
        output_tokens = None
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": env.get("total_cost_usd"),
        "premium_requests": None,
    }
    return answer, usage


def run_json(
    prompt: str,
    timeout: int = DEFAULT_TIMEOUT,
    model: str | None = None,
    use_cache: bool = True,
) -> dict:
    """Invoke `claude -p` and return parsed JSON. Retries once on parse failure.

    The prompt is fed on stdin. Successful responses are cached; see
    `providers.common.run_json` for the shared caching/retry logic.
    """
    return common.run_json(
        _invoke, PROVIDER, prompt, timeout=timeout, model=model, use_cache=use_cache
    )
