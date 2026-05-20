from pr_sentinel.agents.base import BaseAgent


class PerformanceAgent(BaseAgent):
    name = "performance"
    display_name = "Performance Agent"
    prompt_file = "performance.md"
