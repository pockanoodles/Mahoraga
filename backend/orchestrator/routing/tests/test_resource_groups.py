import pytest
from backend.orchestrator.resource_groups import (
    RESOURCE_GROUPS, get_resource_group, get_group_concurrency,
)
from backend.orchestrator.routing.decision_log import DecisionLogger
import numpy as np


def test_get_resource_group_known_agents():
    assert get_resource_group("ollama") == "local_ollama"
    assert get_resource_group("aider") == "local_ollama"
    assert get_resource_group("codex-cli") == "openai_api"
    assert get_resource_group("gemini-cli") == "google_api"
    assert get_resource_group("claude") == "anthropic_api"


def test_get_resource_group_unknown():
    assert get_resource_group("mystery-agent") == "unknown"


def test_get_group_concurrency():
    assert get_group_concurrency("local_ollama") == 1
    assert get_group_concurrency("openai_api") == 2
    assert get_group_concurrency("google_api") == 3
    assert get_group_concurrency("no_such_group") == 1  # conservative default


def test_decision_logger_get_recent_empty():
    logger = DecisionLogger(db_path=":memory:")
    assert logger.get_recent(limit=10) == []


def test_decision_logger_get_recent_returns_decisions():
    logger = DecisionLogger(db_path=":memory:")

    class _Task:
        id = "t1"
        goal = "create a function"

    class _Ctx:
        def to_vector(self):
            return np.array([0.1] * 8)

    logger.log_decision(_Task(), _Ctx(), "aider", ["aider", "ollama"], "linucb")
    recent = logger.get_recent(limit=10)
    assert len(recent) == 1
    assert recent[0]["selected_agent"] == "aider"
    assert recent[0]["task_goal"] == "create a function"


def test_decision_logger_get_recent_agent_filter():
    logger = DecisionLogger(db_path=":memory:")

    class _Task:
        id = "t1"
        goal = "write code"

    class _Ctx:
        def to_vector(self):
            return np.array([0.1] * 8)

    logger.log_decision(_Task(), _Ctx(), "aider", ["aider", "ollama"], "linucb")
    logger.log_decision(_Task(), _Ctx(), "ollama", ["aider", "ollama"], "linucb")

    aider_only = logger.get_recent(limit=10, agent="aider")
    assert len(aider_only) == 1
    assert aider_only[0]["selected_agent"] == "aider"
