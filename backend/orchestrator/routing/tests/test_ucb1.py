"""Tests for UCB1Router."""
import json
import pytest
from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.strategies.ucb1 import UCB1Router


@pytest.fixture
def ctx():
    return TaskContext(0.1, 0.2, 1.0, 0.33, 0.0, 0.0, 0.5, 0.0)


def test_untried_agents_explored_first(ctx):
    """Untried agents should be returned before any UCB computation."""
    router = UCB1Router()
    agents = ["aider", "ollama", "claude"]
    selected = set()
    for _ in range(6):
        a = router.select_agent(ctx, agents)
        router.update(ctx, a, 0.8)
        selected.add(a)
    assert selected == set(agents)  # all agents tried


def test_exploitation_after_learning(ctx):
    """After many successes on one agent, it should be selected most often."""
    router = UCB1Router(c=0.1)  # low exploration
    agents = ["aider", "ollama"]
    # Train: aider always succeeds, ollama always fails
    for _ in range(30):
        router.update(ctx, "aider", 1.0)
        router.update(ctx, "ollama", 0.0)
    # Pre-populate N to bypass untried-first logic
    router.N["aider"] = 30
    router.N["ollama"] = 30
    router.t = 60
    selections = [router.select_agent(ctx, agents) for _ in range(10)]
    assert selections.count("aider") > 7  # aider dominates


def test_incremental_mean_update(ctx):
    """Q update should be a running mean."""
    router = UCB1Router()
    router.N["a"] = 0
    router.Q["a"] = 0.0
    for r in [0.8, 0.6, 1.0]:
        router.update(ctx, "a", r)
    expected_mean = (0.8 + 0.6 + 1.0) / 3.0
    assert abs(router.Q["a"] - expected_mean) < 1e-9


def test_save_load_roundtrip(ctx, tmp_path):
    router = UCB1Router()
    router.select_agent(ctx, ["a", "b"])
    router.update(ctx, "a", 0.7)
    path = str(tmp_path / "ucb1.json")
    router.save_state(path)
    router2 = UCB1Router()
    router2.load_state(path)
    assert router2.N == router.N
    assert router2.Q == router.Q
    assert router2.t == router.t


def test_empty_agents_raises(ctx):
    router = UCB1Router()
    with pytest.raises(ValueError):
        router.select_agent(ctx, [])


def test_single_agent_always_selected(ctx):
    """With one agent, it must always be selected."""
    router = UCB1Router()
    for _ in range(5):
        result = router.select_agent(ctx, ["only"])
        assert result == "only"


def test_t_increments_on_each_selection(ctx):
    router = UCB1Router()
    assert router.t == 0
    router.select_agent(ctx, ["a"])
    assert router.t == 1
    router.select_agent(ctx, ["a"])
    assert router.t == 2


def test_q_initialized_to_zero_for_new_agents(ctx):
    router = UCB1Router()
    router.select_agent(ctx, ["brand_new"])
    assert router.Q["brand_new"] == 0.0
    assert router.N["brand_new"] == 0


def test_get_scores_returns_last_scores(ctx):
    router = UCB1Router()
    router.select_agent(ctx, ["x", "y"])
    scores = router.get_scores()
    assert isinstance(scores, dict)
