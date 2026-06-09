"""Per-run aggregation of provider-call metrics (tokens, cost, time).

Populated by `providers.common.run_json` on every real CLI call. Cache hits make
no call and contribute nothing here. Thread-safe because agent calls run in
parallel under the orchestrator's thread pool.

Providers report different things: `claude` gives input/output tokens and a USD
cost; `copilot` gives output tokens and "premium requests" but no input tokens
or cost. Each metric therefore tracks an availability flag so the UI can render
only what was actually measured rather than showing a misleading 0.
"""
import threading

_lock = threading.Lock()
_data: dict = {}


def reset() -> None:
    """Clear all counters. Called once at the start of a review run."""
    with _lock:
        _data.clear()
        _data.update(
            calls=0,
            elapsed=0.0,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            premium_requests=0.0,
            has_input=False,
            has_output=False,
            has_cost=False,
            has_premium=False,
        )


reset()


def record_call(
    provider: str, model: str | None, elapsed: float, usage: dict | None
) -> None:
    """Record one provider subprocess call. `usage` fields may be None when the
    provider does not report them (e.g. copilot has no input tokens or cost)."""
    usage = usage or {}
    with _lock:
        _data["calls"] += 1
        _data["elapsed"] += elapsed
        for key, flag in (
            ("input_tokens", "has_input"),
            ("output_tokens", "has_output"),
            ("cost_usd", "has_cost"),
            ("premium_requests", "has_premium"),
        ):
            value = usage.get(key)
            if value is not None:
                _data[key] += value
                _data[flag] = True


def summary() -> dict:
    """Return a snapshot copy of the aggregated counters."""
    with _lock:
        return dict(_data)