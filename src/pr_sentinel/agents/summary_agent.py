import json
import re
import time
from importlib import resources

from pr_sentinel.config import (
    DEFAULT_PROVIDER,
    DEFAULT_TIMEOUT,
    SEVERITY_ORDER,
    VALID_SEVERITIES,
)
from pr_sentinel.providers import get_runner

_VALID_AGENT_NAMES = {
    "Security Agent",
    "Code Quality Agent",
    "Performance Agent",
    "Testing Agent",
}


_NUMBERED_ITEM = re.compile(r"(?<!\n)( +)(\d+\. )")


def _fix_numbered_list(text: str) -> str:
    """Ensure each numbered list item starts on its own line.

    The model sometimes runs items together inline ("1. foo 2. bar") despite
    being instructed to use newlines. This normalizes any whitespace run
    immediately before "N. " (N >= 2) to a single newline.
    Only fires when the text actually starts with a numbered item.
    """
    if not re.match(r"^\d+\. ", text):
        return text
    return _NUMBERED_ITEM.sub(r"\n\2", text)


def _canonical_key(f: dict) -> tuple[str, ...]:
    """Stable, content-based sort key so an identical set of findings always
    serializes to the same prompt (and thus the same cache key)."""
    return (
        str(f.get("file", "")),
        str(f.get("lineHint", "")),
        str(f.get("agent", "")),
        str(f.get("severity", "")),
        str(f.get("issue", "")),
        str(f.get("reasoning", "")),
        str(f.get("recommendation", "")),
    )


def _exact_key(f: dict) -> tuple[str, ...]:
    """Identity of the *defect itself*, ignoring which agent reported it and what
    severity label it carried. Two findings sharing this key describe the same
    problem at the same place with the same fix — the strongest possible
    duplicate signal, so they can be collapsed without an LLM judgement call."""
    return (
        str(f.get("file", "")),
        str(f.get("lineHint", "")),
        str(f.get("issue", "")).strip(),
        str(f.get("reasoning", "")).strip(),
        str(f.get("recommendation", "")).strip(),
    )


def _collapse_exact_duplicates(findings: list[dict]) -> tuple[list[dict], int]:
    """Drop byte-identical duplicate findings locally before the LLM pass.

    Findings with the same `_exact_key` are merged; the highest-severity copy
    wins (ties keep the first in the already-canonicalized input order, so the
    result is deterministic). Returns (reduced_findings, removed_count).
    """
    winner_by_key: dict[tuple[str, ...], dict] = {}
    order: list[tuple[str, ...]] = []
    for f in findings:
        k = _exact_key(f)
        current = winner_by_key.get(k)
        if current is None:
            winner_by_key[k] = f
            order.append(k)
        elif SEVERITY_ORDER.get(f.get("severity"), 99) < SEVERITY_ORDER.get(
            current.get("severity"), 99
        ):
            winner_by_key[k] = f
    reduced = [winner_by_key[k] for k in order]
    return reduced, len(findings) - len(reduced)


class SummaryAgent:
    display_name = "Summary Agent"
    prompt_file = "summary.md"

    def __init__(self) -> None:
        self._template = (
            resources.files("pr_sentinel.prompts")
            .joinpath(self.prompt_file)
            .read_text(encoding="utf-8")
        )
        # TESTING ONLY: wall-clock seconds of the most recent summary LLM call
        # (None if no provider call was made, e.g. dedup short-circuited).
        self.last_llm_seconds: float | None = None

    def run(
        self,
        findings: list[dict],
        model: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        use_cache: bool = True,
        provider: str = DEFAULT_PROVIDER,
    ) -> tuple[list[dict], int]:
        """Clean findings. Returns (cleaned_findings, removed_count).

        On any failure, returns (original_findings, 0) so the report is never lost.
        """
        # With 0 or 1 finding there is nothing to deduplicate or consolidate, so
        # skip the (serial, on the critical path) provider call entirely.
        if len(findings) <= 1:
            return findings, 0

        # Canonicalize order before building the prompt. Upstream agents emit
        # findings in chunk-completion order (orchestrator uses as_completed),
        # which shuffles run-to-run. Without this, an identical set of findings
        # produces a different prompt each run and never hits the summary cache.
        ordered = sorted(findings, key=_canonical_key)

        # Cheap, exact-match dedup first: collapse byte-identical findings so the
        # LLM reasons over a smaller set (faster, cheaper, no quality loss). If
        # this alone leaves <=1 finding, there is nothing left to consolidate.
        deduped, local_removed = _collapse_exact_duplicates(ordered)
        if len(deduped) <= 1:
            return deduped, local_removed

        # Include reasoning — it is the strongest signal for deciding whether two
        # differently-worded findings describe the same underlying problem.
        slim = [
            {
                "_id": i,
                "agent": f["agent"],
                "severity": f["severity"],
                "file": f["file"],
                "lineHint": f["lineHint"],
                "issue": f["issue"],
                "reasoning": f.get("reasoning", ""),
                "recommendation": f.get("recommendation", ""),
            }
            for i, f in enumerate(deduped)
        ]
        findings_json = json.dumps(slim, separators=(",", ":"))
        prompt = self._template.replace("<<<FINDINGS>>>", findings_json)

        # TESTING ONLY: time how long the provider/LLM takes to respond.
        _t0 = time.perf_counter()
        try:
            response = get_runner(provider).run_json(
                prompt, timeout=timeout, model=model, use_cache=use_cache
            )
        except Exception:
            self.last_llm_seconds = time.perf_counter() - _t0
            return deduped, local_removed
        self.last_llm_seconds = time.perf_counter() - _t0

        cleaned = self._validate(response, deduped)
        removed = local_removed + max(0, len(deduped) - len(cleaned))
        return cleaned, removed

    def _validate(self, response: dict, original: list[dict]) -> list[dict]:
        """Reconstruct surviving findings from the originals by `_id`.

        The model returns only the winning `_id` per finding, plus optional
        `file`/`lineHint`/`recommendation` overrides for Rule 2/3 consolidations.
        Every verbatim field is restored from `original[_id]`, so the model can
        never corrupt or hallucinate them — and its output stays tiny.
        """
        raw = response.get("findings", [])
        if not isinstance(raw, list) or not raw:
            return original

        cleaned: list[dict] = []
        for f in raw:
            if not isinstance(f, dict):
                continue
            source_id = f.get("_id")
            if not isinstance(source_id, int) or not (0 <= source_id < len(original)):
                continue  # cannot reconstruct without a valid source finding

            base = original[source_id]
            agent = base.get("agent", "")
            if agent not in _VALID_AGENT_NAMES:
                agent = "Code Quality Agent"
            severity = base.get("severity", "Low")
            if severity not in VALID_SEVERITIES:
                severity = "Low"

            # Restore verbatim fields from the winner; apply an override only
            # when the model explicitly supplied one (Rule 2/3).
            file = str(f["file"]) if "file" in f else str(base.get("file", "<unknown>"))
            line_hint = (
                str(f["lineHint"]) if "lineHint" in f else str(base.get("lineHint", ""))
            )
            recommendation = (
                _fix_numbered_list(str(f["recommendation"]).strip())
                if "recommendation" in f
                else str(base.get("recommendation", "")).strip()
            )

            cleaned.append({
                "agent": agent,
                "severity": severity,
                "file": file,
                "lineHint": line_hint,
                "issue": str(base.get("issue", "")).strip(),
                "reasoning": base.get("reasoning", ""),
                "recommendation": recommendation,
            })

        if not cleaned:
            return original
        return cleaned
