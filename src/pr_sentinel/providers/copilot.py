import shutil
import subprocess

from pr_sentinel.config import DEFAULT_TIMEOUT
from pr_sentinel.providers import common

PROVIDER = "copilot"

# Alias for symmetry with providers.claude.ClaudeRunnerError.
CopilotRunnerError = common.RunnerError


def _ensure_copilot_available() -> str:
    path = shutil.which("copilot")
    if not path:
        raise CopilotRunnerError(
            "copilot CLI not found on PATH. Install the GitHub Copilot CLI "
            "(winget install GitHub.Copilot) and run `copilot login` first."
        )
    return path


def _invoke(prompt: str, timeout: int, model: str | None = None) -> str:
    """Run the Copilot CLI non-interactively with the prompt on stdin.

    `--no-color` keeps captured stdout free of ANSI escape codes.
    """
    copilot = _ensure_copilot_available()
    args = [copilot, "--no-color"]
    if model:
        args.extend(["--model", model])
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
        raise CopilotRunnerError(f"copilot timed out after {timeout}s") from e

    if result.returncode != 0:
        raise CopilotRunnerError(
            f"copilot exited {result.returncode}: "
            f"{(result.stderr or result.stdout).strip()[:500]}"
        )
    return result.stdout


def run_json(
    prompt: str,
    timeout: int = DEFAULT_TIMEOUT,
    model: str | None = None,
    use_cache: bool = True,
) -> dict:
    """Invoke the Copilot CLI and return parsed JSON. Retries once on parse failure.

    Shares caching/retry logic with the other providers; see
    `providers.common.run_json`.
    """
    return common.run_json(
        _invoke, PROVIDER, prompt, timeout=timeout, model=model, use_cache=use_cache
    )
