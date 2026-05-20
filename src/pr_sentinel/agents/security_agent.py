from pr_sentinel.agents.base import BaseAgent


class SecurityAgent(BaseAgent):
    name = "security"
    display_name = "Security Agent"
    prompt_file = "security.md"
