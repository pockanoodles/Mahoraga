"""Tests for the implicit-reward → decisions DB path.

Bug context: prior to the fix, BanditRouter.apply_implicit_reward updated
the bandit + episodic memory but never touched routing_decisions.db. So
the only labels A3 could train on came from explicit observe() calls.
The fix adds DecisionLogger.log_implicit_outcome and calls it from the
implicit reward path; explicit outcomes still take precedence.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.orchestrator.routing.bandit_router import BanditRouter
from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.reward import TaskOutcome


class _Task:
    def __init__(self, tid: str, goal: str):
        self.id = tid
        self.title = goal
        self.goal = goal


@pytest.fixture
def router(tmp_path):
    logger = DecisionLogger(db_path=tmp_path / "d.db")
    r = BanditRouter(
        strategy="linucb_per_bucket",
        registry=None,
        logger=logger,
        state_path=tmp_path / "state.json",
    )
    return r


def _row_for_task(logger, task_id):
    return logger._conn.execute(
        "SELECT success, quality_score, reward FROM decisions WHERE task_id = ?",
        (task_id,),
    ).fetchone()


def test_implicit_reward_fills_decision_row(router):
    """An implicit accept (0.6) should populate the row's success/quality/reward."""
    t = _Task("t1", "Refactor the monolithic executor")
    agent = router.route(t, available_agents=["ollama", "aider", "codex-cli"])
    # Pre-implicit: row exists, outcome columns null.
    row = _row_for_task(router.logger, "t1")
    assert row == (None, None, None)

    router.apply_implicit_reward(
        task_id="t1", agent_name=agent, task_goal=t.goal, implicit_signal=0.6,
    )
    row = _row_for_task(router.logger, "t1")
    assert row == (1, 0.6, 0.6)


def test_implicit_retry_writes_failure_label(router):
    t = _Task("t2", "What is gradient descent?")
    agent = router.route(t, available_agents=["ollama", "aider"])
    router.apply_implicit_reward(
        task_id="t2", agent_name=agent, task_goal=t.goal, implicit_signal=0.0,
    )
    row = _row_for_task(router.logger, "t2")
    assert row == (0, 0.0, 0.0)


def test_explicit_outcome_takes_precedence(router):
    """log_outcome runs first → implicit signal must NOT clobber it."""
    t = _Task("t3", "Add type hints to parser.py")
    agent = router.route(t, available_agents=["ollama", "aider", "codex-cli"])
    explicit = TaskOutcome(
        success=True, latency_s=5.0, cost_usd=0.001,
        quality_score=0.92, agent_name=agent,
    )
    router.observe(t, explicit)
    pre = _row_for_task(router.logger, "t3")
    assert pre[0] == 1  # success
    assert abs(pre[1] - 0.92) < 1e-6

    router.apply_implicit_reward(
        task_id="t3", agent_name=agent, task_goal=t.goal, implicit_signal=0.0,
    )
    post = _row_for_task(router.logger, "t3")
    # Explicit outcome preserved.
    assert post == pre


def test_implicit_only_updates_matching_agent(router):
    """If the row's selected_agent doesn't match the implicit signal's
    agent, leave the row alone (defensive)."""
    t = _Task("t4", "Plan the migration to Postgres")
    chosen = router.route(t, available_agents=["ollama", "aider"])
    other = "codex-cli" if chosen != "codex-cli" else "ollama"
    updated = router.logger.log_implicit_outcome(
        task_id="t4", task_goal=t.goal,
        agent_name=other, implicit_signal=0.6,
    )
    assert updated is False
    row = _row_for_task(router.logger, "t4")
    assert row == (None, None, None)


def test_implicit_no_matching_row_returns_false(router):
    updated = router.logger.log_implicit_outcome(
        task_id="never_routed",
        task_goal="some random task",
        agent_name="ollama",
        implicit_signal=0.6,
    )
    assert updated is False


def test_implicit_falls_back_to_task_goal_when_id_missing(router):
    t = _Task("t5", "Some unique goal text 91237")
    agent = router.route(t, available_agents=["ollama", "aider"])
    # No task_id provided — match by goal.
    updated = router.logger.log_implicit_outcome(
        task_id=None, task_goal=t.goal,
        agent_name=agent, implicit_signal=0.6,
    )
    assert updated is True
    row = _row_for_task(router.logger, "t5")
    assert row[0] == 1
