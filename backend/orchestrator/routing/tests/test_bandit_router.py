"""Tests for BanditRouter integration."""
import os
import pytest
from backend.orchestrator.routing import BanditRouter, TaskOutcome
from backend.orchestrator.routing.decision_log import DecisionLogger


class MockTask:
    goal = "write a python function for binary search"
    id = "mock-task-1"


class MockRegistry:
    """Fake adapter registry for testing."""
    def __init__(self, agents):
        self._agents = agents

    def all(self):
        return [type('A', (), {'name': a})() for a in self._agents]


@pytest.fixture
def state_file(tmp_path):
    return str(tmp_path / "bandit_state.json")


@pytest.fixture
def noop_logger(tmp_path):
    """A DecisionLogger writing to a temp DB so tests don't pollute ~/.mahoraga-v2."""
    return DecisionLogger(db_path=tmp_path / "test_decisions.db")


def test_router_selects_from_available_agents(state_file, noop_logger):
    registry = MockRegistry(["aider", "ollama", "claude"])
    router = BanditRouter(
        strategy="linucb",
        registry=registry,
        logger=noop_logger,
        state_path=state_file,
    )
    agent = router.route(MockTask())
    assert agent in ["aider", "ollama", "claude"]


def test_router_calls_strategy_update_after_observe(state_file, noop_logger):
    router = BanditRouter(
        strategy="ucb1",
        logger=noop_logger,
        state_path=state_file,
    )
    task = MockTask()
    agent = router.route(task)
    initial_n = router.strategy.N.get(agent, 0)
    outcome = TaskOutcome(True, 1.0, 0.0, 0.9, agent)
    router.observe(task, outcome)
    assert router.strategy.N[agent] == initial_n + 1


def test_state_persists_after_observe(state_file, noop_logger):
    router = BanditRouter(
        strategy="ucb1",
        logger=noop_logger,
        state_path=state_file,
    )
    task = MockTask()
    agent = router.route(task)
    router.observe(task, TaskOutcome(True, 1.0, 0.0, 0.9, agent))
    assert os.path.exists(state_file)


def test_strategy_can_be_switched(state_file, noop_logger):
    router = BanditRouter(
        strategy="linucb",
        logger=noop_logger,
        state_path=state_file,
    )
    router.route(MockTask())
    router.observe(MockTask(), TaskOutcome(True, 1.0, 0.0, 0.9, "ollama"))
    # State file should exist before switch
    assert os.path.exists(state_file)
    router.set_strategy("ucb1")
    assert router.strategy.name == "ucb1"
    # State file should be deleted after switch
    assert not os.path.exists(state_file)


def test_unknown_strategy_raises(state_file):
    with pytest.raises(ValueError, match="Unknown strategy"):
        BanditRouter(strategy="gpt5", state_path=state_file)


def test_fallback_to_ollama_without_registry(state_file, noop_logger):
    """Without registry, _available_agents returns ['ollama']."""
    router = BanditRouter(
        strategy="static",
        logger=noop_logger,
        state_path=state_file,
    )
    agent = router.route(MockTask())
    assert agent == "ollama"


def test_get_stats_returns_expected_keys(state_file, noop_logger):
    router = BanditRouter(
        strategy="ucb1",
        logger=noop_logger,
        state_path=state_file,
    )
    stats = router.get_stats()
    assert "strategy" in stats
    assert "t" in stats
    assert "scores" in stats
    assert stats["strategy"] == "ucb1"


def test_state_loaded_on_init(tmp_path, noop_logger):
    """If a valid state file exists when BanditRouter is created, it should be loaded."""
    state_file = str(tmp_path / "ucb1_persist.json")
    # First router: route + observe to accumulate state
    r1 = BanditRouter(strategy="ucb1", logger=noop_logger, state_path=state_file)
    task = MockTask()
    agent = r1.route(task)
    r1.observe(task, TaskOutcome(True, 0.5, 0.0, 0.8, agent))
    t_after = r1.strategy.t
    # Second router: should load the saved state
    r2 = BanditRouter(strategy="ucb1", logger=noop_logger, state_path=state_file)
    assert r2.strategy.t == t_after


def test_thompson_strategy_selectable(state_file, noop_logger):
    router = BanditRouter(
        strategy="thompson",
        logger=noop_logger,
        state_path=state_file,
    )
    agent = router.route(MockTask())
    assert agent == "ollama"  # fallback without registry


def test_observe_triggers_save_to_custom_path(tmp_path, noop_logger):
    custom = str(tmp_path / "subdir" / "state.json")
    router = BanditRouter(
        strategy="linucb",
        logger=noop_logger,
        state_path=custom,
    )
    task = MockTask()
    agent = router.route(task)
    router.observe(task, TaskOutcome(True, 2.0, 0.005, 0.85, agent))
    assert os.path.exists(custom)
