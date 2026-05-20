from pr_sentinel.agents.base import BaseAgent


class QualityAgent(BaseAgent):
    name = "quality"
    display_name = "Code Quality Agent"
    prompt_file = "quality.md"
