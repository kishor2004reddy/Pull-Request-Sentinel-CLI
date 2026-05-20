from pr_sentinel.agents.base import BaseAgent


class TestingAgent(BaseAgent):
    name = "testing"
    display_name = "Testing Agent"
    prompt_file = "testing.md"
