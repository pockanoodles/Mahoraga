"""Tests for ThompsonSamplingRouter."""
import collections
import pytest
from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.strategies.thompson import ThompsonSamplingRouter


@pytest.fixture
def ctx():
    return TaskContext(0.1, 0.2, 1.0, 0.33, 0.0, 0.0, 0.5, 0.0)


def test_uniform_priors_roughly_random(ctx):
    """With uniform priors, selection should be roughly uniform."""
    router = ThompsonSamplingRouter()
    agents = ["a", "b", "c"]
    counts = collections.Counter(router.select_agent(ctx, agents) for _ in range(300))
    # Each should be selected ~33% ± large margin to avoid flakiness
    for a in agents:
        assert 20 <= counts[a] <= 150, f"Agent {a} selected {counts[a]}/300 times — too skewed"


def test_many_successes_dominates(ctx):
    """After many successes on one agent, it should win almost all rounds."""
    router = ThompsonSamplingRouter()
    agents = ["winner", "loser"]
    for _ in range(50):
        router.update(ctx, "winner", 1.0)  # alpha grows to 51
        router.update(ctx, "loser", 0.0)   # beta grows to 51
    wins = sum(1 for _ in range(100) if router.select_agent(ctx, agents) == "winner")
    assert wins > 80


def test_save_load_roundtrip(ctx, tmp_path):
    router = ThompsonSamplingRouter()
    router.update(ctx, "a", 1.0)
    router.update(ctx, "b", 0.0)
    path = str(tmp_path / "thompson.json")
    router.save_state(path)
    router2 = ThompsonSamplingRouter()
    router2.load_state(path)
    assert router2.alpha == router.alpha
    assert router2.beta_ == router.beta_
    assert router2.threshold == router.threshold


def test_empty_agents_raises(ctx):
    router = ThompsonSamplingRouter()
    with pytest.raises(ValueError):
        router.select_agent(ctx, [])


def test_update_increments_alpha_on_success(ctx):
    """Reward above threshold increments alpha."""
    router = ThompsonSamplingRouter(success_threshold=0.5)
    router.update(ctx, "agent", 1.0)
    assert router.alpha["agent"] == 2.0  # 1.0 prior + 1.0 increment
    assert router.beta_["agent"] == 1.0  # unchanged


def test_update_increments_beta_on_failure(ctx):
    """Reward below/at threshold increments beta."""
    router = ThompsonSamplingRouter(success_threshold=0.5)
    router.update(ctx, "agent", 0.0)
    assert router.alpha["agent"] == 1.0  # unchanged
    assert router.beta_["agent"] == 2.0  # 1.0 prior + 1.0 increment


def test_get_distributions_returns_all_agents(ctx):
    router = ThompsonSamplingRouter()
    router.update(ctx, "x", 1.0)
    router.update(ctx, "y", 0.0)
    dist = router.get_distributions()
    assert "x" in dist
    assert "y" in dist
    alpha_x, beta_x = dist["x"]
    assert alpha_x == 2.0
    assert beta_x == 1.0


def test_custom_threshold_applies(ctx):
    """With threshold=0.9, reward=0.8 should increment beta (failure)."""
    router = ThompsonSamplingRouter(success_threshold=0.9)
    router.update(ctx, "agent", 0.8)
    assert router.beta_["agent"] == 2.0


def test_select_returns_from_available(ctx):
    router = ThompsonSamplingRouter()
    agents = ["p", "q"]
    for _ in range(20):
        result = router.select_agent(ctx, agents)
        assert result in agents
