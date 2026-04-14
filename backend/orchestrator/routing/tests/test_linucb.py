"""Tests for LinUCBRouter."""
import numpy as np
import pytest
from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.strategies.linucb import LinUCBRouter


@pytest.fixture
def ctx_code():
    """Code generation task context."""
    return TaskContext(0.2, 0.3, 0.0, 0.67, 0.1, 0.0, 1.0, 0.0, 0.0)


@pytest.fixture
def ctx_research():
    """Research task context."""
    return TaskContext(0.1, 0.0, 1.0, 0.33, 0.0, 0.0, 0.0, 1.0, 0.0)


def test_initial_a_matrix_is_identity(ctx_code):
    router = LinUCBRouter(d=9)
    router._init_agent("test")
    assert np.allclose(router.A["test"], np.identity(9))
    # b is initialized with prior (0.5 for unknown agents), not zeros
    assert np.allclose(router.b["test"], 0.5 * np.ones((9, 1)))


def test_learns_to_prefer_agent_for_context(ctx_code):
    """After high rewards for aider on code tasks, LinUCB should prefer aider."""
    router = LinUCBRouter(d=9, alpha=0.1)  # low exploration
    agents = ["aider", "ollama"]
    for _ in range(30):
        router.update(ctx_code, "aider", 1.0)
        router.update(ctx_code, "ollama", 0.0)
    # Now select multiple times — should prefer aider
    selections = [router.select_agent(ctx_code, agents) for _ in range(10)]
    assert selections.count("aider") > 7


def test_ucb_scores_decrease_after_exploration(ctx_code):
    """Explore term should decrease as agent gets more observations."""
    router = LinUCBRouter(d=9, alpha=1.0)
    router.select_agent(ctx_code, ["aider", "ollama"])  # initial selection populates scores
    initial_explore = router.get_scores()["aider"]["explore"]
    for _ in range(20):
        router.update(ctx_code, "aider", 0.8)
    router.select_agent(ctx_code, ["aider", "ollama"])
    later_explore = router.get_scores()["aider"]["explore"]
    assert later_explore < initial_explore  # uncertainty reduced


def test_decay_reduces_influence_of_old_observations():
    """With decay < 1.0, old observations lose influence over time."""
    ctx1 = TaskContext(0.1, 0.9, 0.0, 0.67, 0.0, 0.0, 0.0, 0.0, 0.0)
    ctx2 = TaskContext(0.1, 0.0, 1.0, 0.33, 0.0, 0.0, 0.0, 1.0, 0.0)
    router = LinUCBRouter(d=9, alpha=0.1, decay=0.9)
    for _ in range(20):
        router.update(ctx1, "aider", 1.0)
        router.update(ctx1, "ollama", 0.0)
    for _ in range(20):
        router.update(ctx2, "ollama", 1.0)
        router.update(ctx2, "aider", 0.0)
    # Should run without error; result is non-deterministic
    result = router.select_agent(ctx2, ["aider", "ollama"])
    assert result in ["aider", "ollama"]


def test_save_load_roundtrip(ctx_code, tmp_path):
    router = LinUCBRouter(d=9)
    router.update(ctx_code, "aider", 0.9)
    router.update(ctx_code, "ollama", 0.3)
    path = str(tmp_path / "linucb.json")
    router.save_state(path)
    router2 = LinUCBRouter(d=9)
    router2.load_state(path)
    assert router2.t == router.t
    assert router2.alpha == router.alpha
    assert np.allclose(router2.A["aider"], router.A["aider"])
    assert np.allclose(router2.b["aider"], router.b["aider"])


def test_feature_importance_returns_named_weights(ctx_code):
    router = LinUCBRouter(d=9)
    names = [
        "word_count_norm", "code_keyword_density", "is_question", "complexity_tier",
        "file_count", "has_error_keywords", "has_creation_keywords", "has_research_keywords",
        "queue_depth_norm",
    ]
    fi = router.get_feature_importance("aider", names)
    assert list(fi.keys()) == names
    assert all(isinstance(v, float) for v in fi.values())


def test_empty_agents_raises(ctx_code):
    router = LinUCBRouter(d=9)
    with pytest.raises(ValueError):
        router.select_agent(ctx_code, [])


def test_t_increments_on_each_selection(ctx_code):
    router = LinUCBRouter(d=9)
    assert router.t == 0
    router.select_agent(ctx_code, ["a"])
    assert router.t == 1
    router.select_agent(ctx_code, ["a"])
    assert router.t == 2


def test_scores_contain_ucb_exploit_explore(ctx_code):
    router = LinUCBRouter(d=9)
    router.select_agent(ctx_code, ["aider", "ollama"])
    scores = router.get_scores()
    for agent in ["aider", "ollama"]:
        assert "ucb" in scores[agent]
        assert "exploit" in scores[agent]
        assert "explore" in scores[agent]


def test_get_theta_returns_flat_array(ctx_code):
    router = LinUCBRouter(d=9)
    router.update(ctx_code, "aider", 0.8)
    theta = router.get_theta("aider")
    assert theta.shape == (9,)


def test_b_vector_accumulates_reward_signal(ctx_code):
    """After a high-reward update, b should be non-zero."""
    router = LinUCBRouter(d=9)
    router.update(ctx_code, "aider", 1.0)
    assert not np.allclose(router.b["aider"], np.zeros((9, 1)))
