from importlib import resources
from typing import Callable

from pr_sentinel import chunker, claude_runner

VALID_SEVERITIES = {"Low", "Medium", "High"}


def load_prompt(filename: str) -> str:
    return resources.files("pr_sentinel.prompts").joinpath(filename).read_text(encoding="utf-8")


class BaseAgent:
    name: str = ""
    prompt_file: str = ""
    display_name: str = ""

    def __init__(self) -> None:
        if not self.prompt_file:
            raise ValueError(f"{type(self).__name__} missing prompt_file")
        self._template = load_prompt(self.prompt_file)

    def run(
        self,
        files: list[dict],
        chunk_budget: int,
        on_chunk_done: Callable[[str, int, int], None] | None = None,
        model: str | None = None,
    ) -> dict:
        if not files:
            return {"agent": self.display_name, "findings": []}

        chunks = chunker.chunk_files(files, budget=chunk_budget)
        total = len(chunks)
        all_findings: list[dict] = []

        for idx, chunk in enumerate(chunks, start=1):
            diff_block = chunker.format_diff_block(chunk)
            prompt = self._template.replace("<<<DIFF>>>", diff_block)
            response = claude_runner.run_json(prompt, model=model)
            all_findings.extend(self._validate_findings(response))
            if on_chunk_done:
                on_chunk_done(self.display_name, idx, total)

        return {"agent": self.display_name, "findings": all_findings}

    def _validate_findings(self, response: dict) -> list[dict]:
        findings = response.get("findings", [])
        if not isinstance(findings, list):
            return []

        cleaned: list[dict] = []
        for f in findings:
            if not isinstance(f, dict):
                continue
            severity = f.get("severity", "Low")
            if severity not in VALID_SEVERITIES:
                severity = "Low"
            cleaned.append(
                {
                    "agent": self.display_name,
                    "severity": severity,
                    "file": str(f.get("file", "<unknown>")),
                    "lineHint": str(f.get("lineHint", "")),
                    "issue": str(f.get("issue", "")).strip(),
                    "reasoning": str(f.get("reasoning", "")).strip(),
                    "recommendation": str(f.get("recommendation", "")).strip(),
                }
            )
        return cleaned
