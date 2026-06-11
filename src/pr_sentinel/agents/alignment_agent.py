"""Alignment Agent — judges whether a diff satisfies a linked work item.

Unlike the routed agents (security/quality/…), this one is a standalone,
holistic pass: it carries one work item plus the *whole* diff in a single
provider call and returns a requirement-coverage verdict. It is run directly by
the ``review-alignment`` command, not through the orchestrator or the agent
registry — mirroring how :class:`SummaryAgent` runs on its own.
"""
from importlib import resources

from pr_sentinel.config import (
    ALIGNMENT_AGENT_NAME,
    ALIGNMENT_CONFIDENCES,
    ALIGNMENT_CRITERION_STATUSES,
    ALIGNMENT_VERDICTS,
    DEFAULT_PROVIDER,
    DEFAULT_TIMEOUT,
    VALID_SEVERITIES,
)
from pr_sentinel.integrations.azure_devops import WorkItem
from pr_sentinel.providers import get_runner


def format_work_item(wi: WorkItem) -> str:
    """Render a WorkItem as the plain-text block injected into the prompt."""
    header = f"Work Item #{wi.id} — {wi.type or 'Work Item'}"
    if wi.state:
        header += f" ({wi.state})"
    parts = [header, f"Title: {wi.title}"]
    if wi.description:
        parts += ["", "Description:", wi.description]
    if wi.criteria:
        parts += ["", "Acceptance Criteria:"]
        parts += [f"{i}. {c}" for i, c in enumerate(wi.criteria, start=1)]
    if wi.repro_steps:
        parts += ["", "Repro Steps:", wi.repro_steps]
    return "\n".join(parts)


class AlignmentAgent:
    display_name = ALIGNMENT_AGENT_NAME
    prompt_file = "alignment.md"

    def __init__(self) -> None:
        self._template = (
            resources.files("pr_sentinel.prompts")
            .joinpath(self.prompt_file)
            .read_text(encoding="utf-8")
        )

    def run(
        self,
        work_item: WorkItem,
        diff_block: str,
        model: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        use_cache: bool = True,
        provider: str = DEFAULT_PROVIDER,
    ) -> dict:
        """Judge one work item against the diff.

        Returns a result dict with ``verdict``/``confidence``/``summary``/
        ``criteria``/``findings``. On any provider or parse failure, returns a
        safe "could not determine" result (verdict ``"Unknown"``) so the command
        never crashes — the caller surfaces it as a warning.
        """
        prompt = self._template.replace(
            "<<<WORK_ITEM>>>", format_work_item(work_item)
        ).replace("<<<DIFF>>>", diff_block)

        try:
            response = get_runner(provider).run_json(
                prompt, timeout=timeout, model=model, use_cache=use_cache
            )
        except Exception as e:
            return self._unknown_result(str(e))

        return self._validate(response)

    def _unknown_result(self, error: str) -> dict:
        return {
            "verdict": "Unknown",
            "confidence": "Low",
            "summary": f"Could not determine alignment: {error}",
            "criteria": [],
            "findings": [],
            "failed": True,
            "error": error,
        }

    def _validate(self, response: dict) -> dict:
        if not isinstance(response, dict):
            return self._unknown_result("provider returned a non-object response")

        verdict = response.get("verdict")
        if verdict not in ALIGNMENT_VERDICTS:
            verdict = "Unknown"
        confidence = response.get("confidence")
        if confidence not in ALIGNMENT_CONFIDENCES:
            confidence = "Low"

        criteria = self._validate_criteria(response.get("criteria"))
        findings = self._validate_findings(response.get("findings"))

        return {
            "verdict": verdict,
            "confidence": confidence,
            "summary": str(response.get("summary", "")).strip(),
            "criteria": criteria,
            "findings": findings,
        }

    @staticmethod
    def _validate_criteria(raw) -> list[dict]:
        if not isinstance(raw, list):
            return []
        out: list[dict] = []
        for c in raw:
            if not isinstance(c, dict):
                continue
            status = c.get("status")
            if status not in ALIGNMENT_CRITERION_STATUSES:
                status = "Unverifiable"
            out.append(
                {
                    "criterion": str(c.get("criterion", "")).strip(),
                    "status": status,
                    "evidence": str(c.get("evidence", "")).strip(),
                }
            )
        return out

    @staticmethod
    def _validate_findings(raw) -> list[dict]:
        """Coerce gap findings into the standard finding schema.

        Same shape the routed agents emit (see ``BaseAgent._validate_findings``)
        so the report writer and ``push-azure`` treat them identically; the
        ``agent`` field is stamped as the Alignment Agent.
        """
        if not isinstance(raw, list):
            return []
        out: list[dict] = []
        for f in raw:
            if not isinstance(f, dict):
                continue
            severity = f.get("severity", "Medium")
            if severity not in VALID_SEVERITIES:
                severity = "Medium"
            out.append(
                {
                    "agent": ALIGNMENT_AGENT_NAME,
                    "severity": severity,
                    "file": str(f.get("file", "(requirement)")) or "(requirement)",
                    "lineHint": str(f.get("lineHint", "")),
                    "issue": str(f.get("issue", "")).strip(),
                    "reasoning": str(f.get("reasoning", "")).strip(),
                    "recommendation": str(f.get("recommendation", "")).strip(),
                }
            )
        return out
