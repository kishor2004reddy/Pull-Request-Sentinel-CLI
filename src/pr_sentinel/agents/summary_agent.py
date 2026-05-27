import json
from importlib import resources

from pr_sentinel import claude_runner
from pr_sentinel.config import DEFAULT_TIMEOUT, VALID_SEVERITIES

_VALID_AGENT_NAMES = {
    "Security Agent",
    "Code Quality Agent",
    "Performance Agent",
    "Testing Agent",
}


class SummaryAgent:
    display_name = "Summary Agent"
    prompt_file = "summary.md"

    def __init__(self) -> None:
        self._template = (
            resources.files("pr_sentinel.prompts")
            .joinpath(self.prompt_file)
            .read_text(encoding="utf-8")
        )

    def run(
        self,
        findings: list[dict],
        model: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        use_cache: bool = True,
    ) -> tuple[list[dict], int]:
        """Clean findings. Returns (cleaned_findings, removed_count).

        On any failure, returns (original_findings, 0) so the report is never lost.
        """
        if not findings:
            return findings, 0

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
            for i, f in enumerate(findings)
        ]
        findings_json = json.dumps(slim, separators=(",", ":"))
        prompt = self._template.replace("<<<FINDINGS>>>", findings_json)

        try:
            response = claude_runner.run_json(
                prompt, timeout=timeout, model=model, use_cache=use_cache
            )
        except Exception:
            return findings, 0

        cleaned = self._validate(response, findings)
        removed = max(0, len(findings) - len(cleaned))
        return cleaned, removed

    def _validate(self, response: dict, original: list[dict]) -> list[dict]:
        raw = response.get("findings", [])
        if not isinstance(raw, list) or not raw:
            return original

        cleaned: list[dict] = []
        for f in raw:
            if not isinstance(f, dict):
                continue
            issue = str(f.get("issue", "")).strip()
            if not issue:
                continue
            agent = str(f.get("agent", "")).strip()
            if agent not in _VALID_AGENT_NAMES:
                agent = "Code Quality Agent"
            severity = f.get("severity", "Low")
            if severity not in VALID_SEVERITIES:
                severity = "Low"

            # Restore reasoning from the original finding identified by _id.
            # The LLM preserves _id from the primary/winning finding when merging.
            source_id = f.get("_id")
            if isinstance(source_id, int) and 0 <= source_id < len(original):
                reasoning = original[source_id].get("reasoning", "")
            else:
                reasoning = ""

            cleaned.append({
                "agent": agent,
                "severity": severity,
                "file": str(f.get("file", "<unknown>")),
                "lineHint": str(f.get("lineHint", "")),
                "issue": issue,
                "reasoning": reasoning,
                "recommendation": str(f.get("recommendation", "")).strip(),
            })

        if not cleaned:
            return original
        return cleaned
