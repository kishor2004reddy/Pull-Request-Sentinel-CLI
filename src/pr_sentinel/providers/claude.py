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
