import pytest
from backend.orchestrator.routing.strategies.linucb import LinUCBRouter
from backend.orchestrator.routing.bandit_router import BanditRouter
from backend.orchestrator.routing.context import TaskContext


def _ctx(goal: str = "write a function") -> TaskContext:
    class _T:
        tier = 2
        def __init__(self, g): self.goal = g
    return TaskContext.from_task(_T(goal))


def test_compute_scores_does_not_increment_t():
    router = LinUCBRouter()
    ctx = _ctx()
    t_before = router.t
    scores = router.compute_scores(ctx, ["aider", "ollama"])
    assert router.t == t_before, "compute_scores must not increment t"
    assert set(scores.keys()) == {"aider", "ollama"}
    assert "ucb" in scores["aider"]


def test_compute_scores_is_idempotent():
    router = LinUCBRouter()
    ctx = _ctx()
    s1 = router.compute_scores(ctx, ["aider", "ollama"])
    s2 = router.compute_scores(ctx, ["aider", "ollama"])
    assert s1 == s2


def test_compute_scores_does_not_corrupt_select_agent():
    """After compute_scores, select_agent still works and increments t."""
    router = LinUCBRouter()
    ctx = _ctx()
    router.compute_scores(ctx, ["aider", "ollama"])
    t_before = router.t
    winner = router.select_agent(ctx, ["aider", "ollama"])
    assert router.t == t_before + 1
    assert winner in ["aider", "ollama"]


def test_bandit_score_all_returns_scores_and_strategy():
    bandit = BanditRouter(strategy="linucb")

    class _T:
        goal = "create a dockerfile"
        tier = 2

    result = bandit.score_all(_T())
    assert "strategy" in result
    assert "scores" in result
    assert isinstance(result["scores"], dict)
