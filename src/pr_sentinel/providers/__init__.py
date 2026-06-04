"""Provider runners: each shells out to a local AI CLI.

Every provider module exposes the same surface:
`run_json(prompt, timeout, model, use_cache) -> dict`. `get_runner(name)` maps a
provider name (as accepted by the `--provider` flag) to its module.
"""
from pr_sentinel.providers import claude, copilot

_RUNNERS = {
    claude.PROVIDER: claude,
    copilot.PROVIDER: copilot,
}


def get_runner(provider: str):
    """Return the runner module for `provider`. Raises ValueError if unknown."""
    try:
        return _RUNNERS[provider]
    except KeyError:
        raise ValueError(
            f"unknown provider {provider!r}; valid: {', '.join(sorted(_RUNNERS))}"
        )
