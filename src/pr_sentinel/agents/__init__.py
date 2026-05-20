from pr_sentinel.agents.performance_agent import PerformanceAgent
from pr_sentinel.agents.quality_agent import QualityAgent
from pr_sentinel.agents.security_agent import SecurityAgent
from pr_sentinel.agents.testing_agent import TestingAgent

AGENT_REGISTRY = {
    "security": SecurityAgent,
    "quality": QualityAgent,
    "performance": PerformanceAgent,
    "testing": TestingAgent,
}
