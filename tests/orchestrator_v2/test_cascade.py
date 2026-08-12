"""Unit tests for the live serving cascade (routing.cascade) — fake workers only.

Era 19 proved the local→judge→cloud cascade on benchmarks; `cascade.py` puts the
escalation half on the serving path, spending the judge verdict the reward path
already computes. Invariants under test:

  - only an explicit judge REJECT escalates — an abstain (None) and an accept
    (1.0) must never spend, so turning the judge off cannot start billing;
  - escalation is pinned to the code-like buckets the judge is measured on;
  - `escalate` NEVER raises: a missing arm, a worker error, an empty answer, or
    an exception all degrade to "serve the local output";
  - the daily cap bounds spend, and a reservation that produced no answer is
    refunded rather than burned.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.orchestrator.routing import cascade
from backend.orchestrator.routing.execution_gate import EXEC_GATE_BUCKETS
from backend.orchestrator.workers.base import WorkerEvent


class _FakeCloudWorker:
    """Scripted escalation arm: an answer, a failure, or an explosion."""

    id = "fake:cloud"

    def __init__(self, answer: str = "escalated answer", *, fail: str | None = None,
                 boom: bool = False, cost: float = 0.02):
        self._answer = answer
        self._fail = fail
        self._boom = boom
        self._cost = cost
        self.calls = 0

    async def execute(self, attempt, task, feedback=None):
        self.calls += 1
        if self._boom:
            raise RuntimeError("cloud arm exploded")
        yield WorkerEvent(type="metrics", payload={"cost_usd": self._cost})
        if self._fail:
            yield WorkerEvent(type="attempt.failed", payload={"error": self._fail})
        else:
            yield WorkerEvent(type="attempt.completed", payload={"summary": self._answer})


@pytest.fixture(autouse=True)
def _clean_cascade(monkeypatch):
    """Every test starts with a fresh budget, cached arm, and default env."""
    for var in (
        "MAHORAGA_CASCADE",
        "MAHORAGA_ESCALATE_TO",
        "MAHORAGA_ESCALATE_MAX_PER_DAY",
        "MAHORAGA_AGENTS_YAML",
    ):
        monkeypatch.delenv(var, raising=False)
    cascade.reset_budget_for_tests()
    yield
    cascade.reset_budget_for_tests()


def _install_arm(monkeypatch, worker) -> None:
    """Bypass agents.yaml construction and serve `worker` as the escalation arm."""
    monkeypatch.setattr(cascade, "_get_escalation_worker", lambda: worker)


# ── should_escalate: only a reject spends ────────────────────────────────────

def test_only_reject_escalates():
    assert cascade.should_escalate(0.0, "code") is True
    assert cascade.should_escalate(1.0, "code") is False


def test_judge_abstain_never_escalates():
    """None is the judge off/unavailable/unparseable — the reward path's no-op.

    If an abstain escalated, disabling the judge would start billing rather
    than stopping it.
    """
    assert cascade.should_escalate(None, "code") is False


def test_escalation_pinned_to_judged_buckets():
    assert cascade.CASCADE_BUCKETS == EXEC_GATE_BUCKETS
    for bucket in EXEC_GATE_BUCKETS:
        assert cascade.should_escalate(0.0, bucket) is True
    for bucket in ("general", "plan", "research", "explain", "review"):
        assert cascade.should_escalate(0.0, bucket) is False


def test_disabled_cascade_never_escalates(monkeypatch):
    monkeypatch.setenv("MAHORAGA_CASCADE", "off")
    assert cascade.should_escalate(0.0, "code") is False


# ── escalate: the happy path ─────────────────────────────────────────────────

def test_escalate_returns_answer_and_cost(monkeypatch):
    worker = _FakeCloudWorker("def f(): return 42", cost=0.031)
    _install_arm(monkeypatch, worker)

    output, cost, detail = asyncio.run(cascade.escalate("write f"))

    assert output == "def f(): return 42"
    assert cost == pytest.approx(0.031)
    assert "escalated to" in detail
    assert worker.calls == 1
    assert cascade.escalations_today() == 1


# ── escalate: every failure degrades to "serve local" ────────────────────────

def test_missing_arm_degrades_to_none(monkeypatch):
    _install_arm(monkeypatch, None)
    output, cost, detail = asyncio.run(cascade.escalate("write f"))
    assert output is None
    assert cost == 0.0
    assert "unavailable" in detail


def test_worker_error_degrades_to_none(monkeypatch):
    _install_arm(monkeypatch, _FakeCloudWorker(fail="cli timed out"))
    output, _cost, detail = asyncio.run(cascade.escalate("write f"))
    assert output is None
    assert "cli timed out" in detail


def test_empty_answer_degrades_to_none(monkeypatch):
    """An arm that returns whitespace must not replace a real local answer."""
    _install_arm(monkeypatch, _FakeCloudWorker("   \n  "))
    output, _cost, detail = asyncio.run(cascade.escalate("write f"))
    assert output is None
    assert "produced nothing" in detail


def test_exception_never_propagates(monkeypatch):
    _install_arm(monkeypatch, _FakeCloudWorker(boom=True))
    output, cost, detail = asyncio.run(cascade.escalate("write f"))
    assert output is None
    assert cost == 0.0
    assert "escalation failed" in detail


def test_unresolvable_arm_logs_once_and_returns_none(monkeypatch, tmp_path):
    """A roster with no such arm must not raise out of the request path."""
    cfg = tmp_path / "agents.yaml"
    cfg.write_text("ollama:\n  models: []\n")
    monkeypatch.setenv("MAHORAGA_AGENTS_YAML", str(cfg))
    monkeypatch.setenv("MAHORAGA_ESCALATE_TO", "nonexistent-arm")
    cascade.reset_budget_for_tests()

    output, _cost, detail = asyncio.run(cascade.escalate("write f"))
    assert output is None
    assert "unavailable" in detail


# ── the daily cap ────────────────────────────────────────────────────────────

def test_daily_cap_stops_spending(monkeypatch):
    monkeypatch.setenv("MAHORAGA_ESCALATE_MAX_PER_DAY", "2")
    worker = _FakeCloudWorker()
    _install_arm(monkeypatch, worker)

    assert asyncio.run(cascade.escalate("a"))[0] is not None
    assert asyncio.run(cascade.escalate("b"))[0] is not None
    output, _cost, detail = asyncio.run(cascade.escalate("c"))

    assert output is None
    assert "daily cap" in detail
    assert worker.calls == 2, "the capped call must not reach the arm"


def test_failed_escalation_refunds_its_reservation(monkeypatch):
    """A reservation that bought no answer must not consume the day's budget.

    Otherwise a flaky arm would silently exhaust the cap and disable the
    cascade for the rest of the day without a single escalation served.
    """
    monkeypatch.setenv("MAHORAGA_ESCALATE_MAX_PER_DAY", "1")
    _install_arm(monkeypatch, _FakeCloudWorker(fail="boom"))
    assert asyncio.run(cascade.escalate("a"))[0] is None
    assert cascade.escalations_today() == 0

    _install_arm(monkeypatch, _FakeCloudWorker("real answer"))
    output, _cost, _detail = asyncio.run(cascade.escalate("b"))
    assert output == "real answer"


def test_zero_cap_disables_the_limit(monkeypatch):
    monkeypatch.setenv("MAHORAGA_ESCALATE_MAX_PER_DAY", "0")
    _install_arm(monkeypatch, _FakeCloudWorker())
    for _ in range(5):
        assert asyncio.run(cascade.escalate("a"))[0] is not None


def test_malformed_cap_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MAHORAGA_ESCALATE_MAX_PER_DAY", "not-a-number")
    assert cascade.daily_cap() == 25


# ── config surface ───────────────────────────────────────────────────────────

def test_escalation_arm_default_and_override(monkeypatch):
    assert cascade.escalation_arm() == "claude-cli"
    monkeypatch.setenv("MAHORAGA_ESCALATE_TO", "codex")
    assert cascade.escalation_arm() == "codex"


def test_cascade_enabled_off_aliases(monkeypatch):
    for value in ("off", "0", "false", "no", "OFF"):
        monkeypatch.setenv("MAHORAGA_CASCADE", value)
        assert cascade.cascade_enabled() is False
    monkeypatch.setenv("MAHORAGA_CASCADE", "on")
    assert cascade.cascade_enabled() is True


# ── the exec-gate trigger ────────────────────────────────────────────────────
#
# Live traffic exposed the gap these cover: granite produced non-compiling code
# on 2 of 6 tasks, the exec gate flipped them to failed, and because the reward
# judge only runs on successes the judge never voted — so the answers most
# certain to be wrong were the only ones that never escalated.


def test_exec_failure_escalates_without_a_judge_verdict():
    """Code that does not compile is wrong deterministically — escalate it."""
    assert cascade.should_escalate(None, "code", exec_failed=True) is True


def test_exec_failure_escalates_even_if_the_judge_accepted():
    """The gate is the harder signal; a judge accept cannot override it."""
    assert cascade.should_escalate(1.0, "code", exec_failed=True) is True


def test_exec_failure_still_respects_the_bucket_pin():
    assert cascade.should_escalate(None, "general", exec_failed=True) is False


def test_exec_failure_still_respects_the_off_switch(monkeypatch):
    monkeypatch.setenv("MAHORAGA_CASCADE", "off")
    assert cascade.should_escalate(None, "code", exec_failed=True) is False


def test_default_exec_failed_preserves_judge_only_behaviour():
    """Callers that pass no exec signal keep the original semantics."""
    assert cascade.should_escalate(0.0, "code") is True
    assert cascade.should_escalate(None, "code") is False
    assert cascade.should_escalate(1.0, "code") is False
