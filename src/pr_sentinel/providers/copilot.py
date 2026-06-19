import json
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


def _invoke(prompt: str, timeout: int, model: str | None = None) -> tuple[str, dict]:
    """Run the Copilot CLI non-interactively with the prompt on stdin.

    `--no-color` keeps captured output free of ANSI escape codes; `--output-format
    json` emits a JSONL event stream that carries the answer plus usage data,
    which `_parse_stream` decodes.
    """
    copilot = _ensure_copilot_available()
    args = [copilot, "--no-color", "--output-format", "json"]
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
    return _parse_stream(result.stdout)


def _parse_stream(stdout: str) -> tuple[str, dict]:
    """Decode copilot's --output-format json event stream into (answer_text, usage).

    The stream is JSONL: one JSON object per line. The answer is the content of
    the final `assistant.message` event; output tokens accumulate across those
    events and premium-request usage comes from the terminal `result` event.
    Copilot reports neither input tokens nor a USD cost, so those stay None.
    """
    answer = ""
    output_tokens = 0
    saw_output = False
    premium = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue  # ignore any non-JSON noise on the stream
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == "assistant.message":
            content = data.get("content")
            if content:
                answer = str(content)  # keep the latest (final) message
            ot = data.get("outputTokens")
            if isinstance(ot, (int, float)):
                output_tokens += int(ot)
                saw_output = True
        elif etype == "result":
            pr = (event.get("usage") or {}).get("premiumRequests")
            if isinstance(pr, (int, float)):
                premium = float(pr)
    usage = {
        "input_tokens": None,
        "output_tokens": output_tokens if saw_output else None,
        "cost_usd": None,
        "premium_requests": premium,
    }
    return answer, usage


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


def interactive_argv(prompt: str, model: str | None = None) -> list[str]:
    """Argv to launch an *interactive* Copilot CLI session seeded with ``prompt``.

    Unlike :func:`run_json` (headless, prompt on stdin), ``copilot -i <prompt>``
    starts interactive mode and auto-runs the prompt; the CLI's native permission
    prompts gate every file edit. The caller runs it with inherited stdio and
    ``cwd`` at the repo root, and blocks until the user exits.
    """
    args = [_ensure_copilot_available()]
    if model:
        args.extend(["--model", model])
    args.extend(["-i", prompt])
    return args
