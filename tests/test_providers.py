import json

import pytest

from pr_sentinel import runstats
from pr_sentinel.providers import claude, copilot


# --- claude envelope parsing ------------------------------------------------

def test_claude_parse_envelope_extracts_answer_and_usage():
    env = {
        "type": "result",
        "is_error": False,
        "result": '{"findings":[]}',
        "total_cost_usd": 0.1086,
        "usage": {
            "input_tokens": 2349,
            "cache_creation_input_tokens": 15384,
            "cache_read_input_tokens": 0,
            "output_tokens": 9,
        },
    }
    answer, usage = claude._parse_envelope(json.dumps(env))
    assert answer == '{"findings":[]}'
    assert usage["input_tokens"] == 2349 + 15384  # fresh + cache-creation + read
    assert usage["output_tokens"] == 9
    assert usage["cost_usd"] == 0.1086
    assert usage["premium_requests"] is None


def test_claude_parse_envelope_raises_on_non_json():
    with pytest.raises(claude.ClaudeRunnerError):
        claude._parse_envelope("not an envelope")


def test_claude_parse_envelope_raises_on_error_flag():
    with pytest.raises(claude.ClaudeRunnerError):
        claude._parse_envelope(json.dumps({"is_error": True, "result": "boom"}))


# --- copilot JSONL stream parsing -------------------------------------------

def test_copilot_parse_stream_extracts_answer_tokens_and_premium():
    lines = [
        json.dumps({"type": "session.mcp_servers_loaded", "data": {}}),
        json.dumps({"type": "assistant.turn_start", "data": {}}),
        json.dumps(
            {
                "type": "assistant.message",
                "data": {"content": '{"findings":[]}', "outputTokens": 70},
            }
        ),
        json.dumps(
            {"type": "result", "usage": {"premiumRequests": 0.33, "totalApiDurationMs": 3560}}
        ),
    ]
    answer, usage = copilot._parse_stream("\n".join(lines))
    assert answer == '{"findings":[]}'
    assert usage["output_tokens"] == 70
    assert usage["premium_requests"] == 0.33
    assert usage["input_tokens"] is None
    assert usage["cost_usd"] is None


def test_copilot_parse_stream_ignores_non_json_noise():
    stream = "warning: something\n" + json.dumps(
        {"type": "assistant.message", "data": {"content": "{}"}}
    )
    answer, usage = copilot._parse_stream(stream)
    assert answer == "{}"
    assert usage["output_tokens"] is None  # never reported


# --- runstats aggregation ---------------------------------------------------

def test_runstats_aggregates_across_calls():
    runstats.reset()
    runstats.record_call(
        "claude", "haiku", 1.5,
        {"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.01, "premium_requests": None},
    )
    runstats.record_call(
        "claude", "haiku", 2.5,
        {"input_tokens": 50, "output_tokens": 10, "cost_usd": 0.02, "premium_requests": None},
    )
    s = runstats.summary()
    assert s["calls"] == 2
    assert s["input_tokens"] == 150
    assert s["output_tokens"] == 30
    assert abs(s["cost_usd"] - 0.03) < 1e-9
    assert s["elapsed"] == 4.0
    assert s["has_input"] and s["has_cost"] and s["has_output"]
    assert not s["has_premium"]


def test_runstats_copilot_style_reports_premium_not_cost():
    runstats.reset()
    runstats.record_call(
        "copilot", "claude-sonnet-4.6", 3.0,
        {"input_tokens": None, "output_tokens": 70, "cost_usd": None, "premium_requests": 0.33},
    )
    s = runstats.summary()
    assert s["has_output"] and s["has_premium"]
    assert not s["has_input"] and not s["has_cost"]
    assert s["premium_requests"] == 0.33