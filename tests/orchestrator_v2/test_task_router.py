# tests/orchestrator_v2/test_task_router.py
import pytest
from backend.orchestrator.workers.router import TaskRouter
from backend.orchestrator.domain.models import Task


def _task(title: str, goal: str) -> Task:
    return Task.new(run_id="r", title=title, goal=goal)


router = TaskRouter()
_PREFIX = "ollama:qwen3-4b"


def test_code_keywords_route_to_coder():
    t = _task("Write a function", "implement a fibonacci function in Python")
    assert router.route(t, "ollama") == f"{_PREFIX}:coder"


def test_debug_keyword_routes_to_coder():
    t = _task("Fix bug", "debug the authentication module")
    assert router.route(t, "ollama") == f"{_PREFIX}:coder"


def test_refactor_keyword_routes_to_coder():
    t = _task("Refactor", "refactor the database layer")
    assert router.route(t, "ollama") == f"{_PREFIX}:coder"


def test_planning_keyword_routes_to_planner():
    t = _task("Plan the approach", "outline the steps needed to build this feature")
    assert router.route(t, "ollama") == f"{_PREFIX}:planner"


def test_short_task_routes_to_fast():
    t = _task("What is Python", "What is Python")
    assert router.route(t, "ollama") == f"{_PREFIX}:fast"


def test_what_is_phrase_routes_to_fast():
    t = _task("Explain", "what is the difference between TCP and UDP")
    assert router.route(t, "ollama") == f"{_PREFIX}:fast"


def test_how_many_phrase_routes_to_fast():
    t = _task("Count", "how many items are in the list")
    assert router.route(t, "ollama") == f"{_PREFIX}:fast"


def test_general_task_routes_to_general():
    t = _task("Write a blog post", "write a persuasive essay about climate change")
    assert router.route(t, "ollama") == f"{_PREFIX}:general"


def test_analysis_task_routes_to_general():
    t = _task("Summarize findings", "analyze the quarterly revenue data and write a summary")
    assert router.route(t, "ollama") == f"{_PREFIX}:general"


def test_wrong_backend_raises():
    t = _task("anything", "anything")
    with pytest.raises(ValueError, match="ollama"):
        router.route(t, "claude")


def test_import_keyword_routes_to_coder():
    t = _task("Fix imports", "fix the import statements in the module")
    assert router.route(t, "ollama") == f"{_PREFIX}:coder"


def test_api_keyword_routes_to_coder():
    t = _task("Build endpoint", "build a REST API endpoint for user authentication")
    assert router.route(t, "ollama") == f"{_PREFIX}:coder"
