"""Tests for the live judge-escalation gate (routing/judge_escalation.py) and
its wiring into the serving executor.

The unit tests cover verdict → decision mapping; the integration tests pin the
three properties that make the gate safe to run on live traffic (see the module
docstring): a reject escalates rather than fails, the judge is not consulted
when escalation is impossible, and a judge-escalated answer is served rather
than lost if the escalation target dies.
"""
import dataclasses
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.orchestrator.domain import events as ev_types
from backend.orchestrator.domain.models import (
    Mission, Plan, Run, RunMode, Task, TaskAttempt, TaskStatus,
)
from backend.orchestrator.routing.judge_escalation import (
    CODE_RUBRIC_BUCKETS,
    judge_arm,
    judge_gate_enabled,
    rubric_for_bucket,
    select_judge_worker,
    should_escalate_by_judge,
)
from backend.orchestrator.routing.judge_gate import GENERAL_RUBRIC, JUDGE_RUBRIC
from backend.orchestrator.service.executor import pop_judge_gate, run_task
from backend.orchestrator.store.base import Store
from backend.orchestrator.verifier.verifier import Verifier, VerificationResult
from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
from backend.orchestrator.workers.registry import WorkerRegistry


# ── test doubles ─────────────────────────────────────────────────────────────

class MockWorker(WorkerAdapter):
    def __init__(self, worker_id: str, capabilities: list[str], events: list[WorkerEvent]):
        self._id = worker_id
        self._caps = capabilities
        self._events = events
        self.execute_called = 0

    @property
    def id(self) -> str:
        return self._id

    @property
    def capabilities(self) -> list[str]:
        return self._caps

    async def execute(
        self, attempt: TaskAttempt, task: Task, feedback: str | None = None,
    ) -> AsyncIterator[WorkerEvent]:
        self.execute_called += 1
        for ev in self._events:
            yield ev

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id=self.id, healthy=True)


def _judge(verdict: str, worker_id: str = "ollama:qwen3.5:general") -> MockWorker:
    """A judge worker that always replies with the given raw text.

    Capabilities are deliberately empty so `assign_worker` can never pick the
    judge to *do* the task — it is only reachable via `select_judge_worker`.
    """
    return MockWorker(worker_id, [], [WorkerEvent("attempt.completed", {"summary": verdict})])


_SAYS_CORRECT = '{"correct": true, "reason": "ok"}'
_SAYS_INCORRECT = '{"correct": false, "reason": "off-by-one"}'


@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    yield s
    await s.close()


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("MAHORAGA_JUDGE_GATE", "on")


async def _setup(store: Store, **task_kwargs) -> str:
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    await store.missions.save_run(r)

    defaults = dict(
        run_id=r.id, title="T", goal="Explain why the sky is blue",
        required_capabilities=["file_editing"],
    )
    defaults.update(task_kwargs)
    task = Task.new(**defaults)
    task = dataclasses.replace(task, status=TaskStatus.ready)
    await store.tasks.save(task)
    return task.id


def _reg(*workers) -> WorkerRegistry:
    reg = WorkerRegistry()
    for w in workers:
        reg.register(w)
    return reg


def _pass_verifier() -> Verifier:
    result = VerificationResult(score=9, passed=True, feedback="", action="pass")
    v = MagicMock(spec=Verifier)
    v.verify = AsyncMock(return_value=result)
    return v


async def _output_of(store: Store, task_id: str) -> str:
    artifacts = await store.artifacts.list_by_task(task_id)
    return next((a.location.get("content", "") for a in artifacts if a.type == "text_output"), "")


# ── config ───────────────────────────────────────────────────────────────────

def test_gate_is_off_by_default(monkeypatch):
    monkeypatch.delenv("MAHORAGA_JUDGE_GATE", raising=False)
    assert judge_gate_enabled() is False


@pytest.mark.parametrize("value", ["on", "1", "true", "YES", "On"])
def test_gate_enabled_by_env(monkeypatch, value):
    monkeypatch.setenv("MAHORAGA_JUDGE_GATE", value)
    assert judge_gate_enabled() is True


@pytest.mark.parametrize("value", ["off", "0", "false", "no", "", "  "])
def test_gate_stays_off_for_other_values(monkeypatch, value):
    monkeypatch.setenv("MAHORAGA_JUDGE_GATE", value)
    assert judge_gate_enabled() is False


def test_judge_arm_defaults_to_the_5c_judge(monkeypatch):
    monkeypatch.delenv("MAHORAGA_JUDGE_MODEL", raising=False)
    assert judge_arm() == "qwen3.5"


def test_judge_arm_override(monkeypatch):
    monkeypatch.setenv("MAHORAGA_JUDGE_MODEL", "granite4.1-8b")
    assert judge_arm() == "granite4.1-8b"


@pytest.mark.parametrize("bucket", sorted(CODE_RUBRIC_BUCKETS))
def test_code_buckets_get_the_code_rubric(bucket):
    assert rubric_for_bucket(bucket) is JUDGE_RUBRIC


@pytest.mark.parametrize("bucket", ["general", "plan", "research", "explain"])
def test_other_buckets_get_the_general_rubric(bucket):
    assert rubric_for_bucket(bucket) is GENERAL_RUBRIC


# ── judge selection ──────────────────────────────────────────────────────────

def test_select_judge_prefers_the_general_role(monkeypatch):
    monkeypatch.setenv("MAHORAGA_JUDGE_MODEL", "qwen3.5")
    coder = MockWorker("ollama:qwen3.5:coder", [], [])
    general = MockWorker("ollama:qwen3.5:general", [], [])
    assert select_judge_worker(_reg(coder, general)) is general


def test_select_judge_falls_back_to_any_role_of_the_arm(monkeypatch):
    monkeypatch.setenv("MAHORAGA_JUDGE_MODEL", "qwen3.5")
    coder = MockWorker("ollama:qwen3.5:coder", [], [])
    assert select_judge_worker(_reg(coder)) is coder


def test_select_judge_returns_none_when_arm_absent(monkeypatch):
    monkeypatch.setenv("MAHORAGA_JUDGE_MODEL", "not-installed")
    assert select_judge_worker(_reg(MockWorker("ollama:granite4.1-8b:general", [], []))) is None


def test_select_judge_returns_none_with_no_local_workers(monkeypatch):
    monkeypatch.setenv("MAHORAGA_JUDGE_MODEL", "qwen3.5")
    assert select_judge_worker(_reg(MockWorker("claude-cli:sonnet", [], []))) is None


def test_select_judge_accepts_a_full_worker_id(monkeypatch):
    monkeypatch.setenv("MAHORAGA_JUDGE_MODEL", "ollama:qwen3.5:coder")
    coder = MockWorker("ollama:qwen3.5:coder", [], [])
    general = MockWorker("ollama:qwen3.5:general", [], [])
    assert select_judge_worker(_reg(coder, general)) is coder


def test_select_judge_still_returns_a_self_judge(monkeypatch, caplog):
    """Self-judging is weaker evidence than Era 14/15 measured — warn, don't refuse."""
    monkeypatch.setenv("MAHORAGA_JUDGE_MODEL", "qwen3.5")
    general = MockWorker("ollama:qwen3.5:general", [], [])
    chosen = select_judge_worker(_reg(general), producer_worker_id="ollama:qwen3.5:coder")
    assert chosen is general
    assert "self-judging" in caplog.text


# ── verdict → decision ───────────────────────────────────────────────────────

async def test_correct_verdict_keeps_local():
    d = await should_escalate_by_judge(_judge(_SAYS_CORRECT), "task", "answer", "general")
    assert (d.escalate, d.verdict) == (False, True)


async def test_incorrect_verdict_escalates():
    d = await should_escalate_by_judge(_judge(_SAYS_INCORRECT), "task", "answer", "general")
    assert (d.escalate, d.verdict) == (True, False)


async def test_unparseable_verdict_escalates():
    """5c's safe default: a judge that replied but made no sense → escalate."""
    d = await should_escalate_by_judge(
        _judge("I am not sure, could be true or false"), "task", "answer", "general",
    )
    assert (d.escalate, d.verdict) == (True, None)


async def test_failed_judge_call_abstains():
    """Deliberate deviation from route_one: a dead judge must not reroute all traffic."""
    broken = MockWorker(
        "ollama:qwen3.5:general", [],
        [WorkerEvent("attempt.failed", {"error": "connection refused"})],
    )
    d = await should_escalate_by_judge(broken, "task", "answer", "general")
    assert d.escalate is False
    assert d.verdict is None
    assert "unavailable" in d.reason


async def test_empty_output_abstains():
    d = await should_escalate_by_judge(_judge(_SAYS_INCORRECT), "task", "   ", "general")
    assert d.escalate is False
    assert "nothing for the judge" in d.reason


async def test_judge_latency_is_measured():
    """The per-task tax on a serving path is time; it has to be recorded."""
    d = await should_escalate_by_judge(_judge(_SAYS_CORRECT), "task", "answer", "general")
    assert d.judge_ms >= 0.0


async def test_abstain_on_empty_output_costs_nothing():
    d = await should_escalate_by_judge(_judge(_SAYS_CORRECT), "task", "", "general")
    assert d.judge_ms == 0.0


# ── executor wiring ──────────────────────────────────────────────────────────

async def test_gate_off_means_no_judge_call(store, monkeypatch):
    monkeypatch.delenv("MAHORAGA_JUDGE_GATE", raising=False)
    local = MockWorker("ollama:granite4.1-8b:general", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the local answer"}),
    ])
    judge = _judge(_SAYS_INCORRECT)
    cloud = MockWorker("claude-cli:sonnet", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the cloud answer"}),
    ])
    task_id = await _setup(store)

    await run_task(task_id, store, _reg(local, judge, cloud), _pass_verifier())

    assert judge.execute_called == 0
    assert cloud.execute_called == 0
    assert await _output_of(store, task_id) == "the local answer"


async def test_correct_verdict_serves_local_answer(store, gate_on):
    local = MockWorker("ollama:granite4.1-8b:general", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the local answer"}),
    ])
    judge = _judge(_SAYS_CORRECT)
    cloud = MockWorker("claude-cli:sonnet", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the cloud answer"}),
    ])
    task_id = await _setup(store)

    await run_task(task_id, store, _reg(local, judge, cloud), _pass_verifier())

    assert judge.execute_called == 1
    assert cloud.execute_called == 0
    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
    assert await _output_of(store, task_id) == "the local answer"


async def test_incorrect_verdict_escalates_and_serves_the_next_worker(store, gate_on):
    """The 5c cascade, live: judge rejects local → the stronger arm's answer is served."""
    local = MockWorker("ollama:granite4.1-8b:general", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the local answer"}),
    ])
    judge = _judge(_SAYS_INCORRECT)
    cloud = MockWorker("claude-cli:sonnet", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the cloud answer"}),
    ])
    task_id = await _setup(store)

    await run_task(task_id, store, _reg(local, judge, cloud), _pass_verifier())

    assert cloud.execute_called == 1
    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
    assert await _output_of(store, task_id) == "the cloud answer"

    events = [e.type for e in await store.events.list_by_task(task_id)]
    assert ev_types.ATTEMPT_ESCALATED in events


async def test_reject_is_recorded_as_escalation_not_failure(store, gate_on):
    """Invariant 1: a verdict is a routing signal, never a task failure."""
    local = MockWorker("ollama:granite4.1-8b:general", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the local answer"}),
    ])
    cloud = MockWorker("claude-cli:sonnet", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the cloud answer"}),
    ])
    task_id = await _setup(store)

    await run_task(task_id, store, _reg(local, _judge(_SAYS_INCORRECT), cloud), _pass_verifier())

    attempts = await store.tasks.list_attempts(task_id)
    rejected = next(a for a in attempts if a.worker_id == local.id)
    assert rejected.error_code == "judge_rejected"
    events = [e.type for e in await store.events.list_by_task(task_id)]
    assert ev_types.TASK_BLOCKED not in events


async def test_judge_not_consulted_when_escalation_impossible(store, gate_on):
    """Invariant 2: with nowhere to escalate, a reject could only do harm — skip the call."""
    local = MockWorker("ollama:granite4.1-8b:general", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the only answer"}),
    ])
    judge = _judge(_SAYS_INCORRECT)
    task_id = await _setup(store)

    await run_task(task_id, store, _reg(local, judge), _pass_verifier())

    assert judge.execute_called == 0
    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
    assert await _output_of(store, task_id) == "the only answer"


async def test_local_answer_survives_a_failed_escalation(store, gate_on):
    """Invariant 3: a judge mistake costs money and latency, never the answer."""
    local = MockWorker("ollama:granite4.1-8b:general", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the local answer"}),
    ])
    cloud = MockWorker("claude-cli:sonnet", ["file_editing"], [
        WorkerEvent("attempt.failed", {"error_code": "timeout", "error": "cloud timed out"}),
    ])
    task_id = await _setup(store)

    await run_task(task_id, store, _reg(local, _judge(_SAYS_INCORRECT), cloud), _pass_verifier())

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
    assert await _output_of(store, task_id) == "the local answer"
    events = [e.type for e in await store.events.list_by_task(task_id)]
    assert ev_types.TASK_BLOCKED not in events
    assert ev_types.TASK_COMPLETED in events


async def test_unresolvable_judge_keeps_local_answer(store, gate_on, monkeypatch):
    """A misconfigured judge model deactivates the gate; it must not block traffic."""
    monkeypatch.setenv("MAHORAGA_JUDGE_MODEL", "not-installed")
    local = MockWorker("ollama:granite4.1-8b:general", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the local answer"}),
    ])
    cloud = MockWorker("claude-cli:sonnet", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the cloud answer"}),
    ])
    task_id = await _setup(store)

    await run_task(task_id, store, _reg(local, cloud), _pass_verifier())

    assert cloud.execute_called == 0
    assert await _output_of(store, task_id) == "the local answer"


# ── reward-path side-channel ─────────────────────────────────────────────────

async def test_reject_is_reported_to_the_reward_path(store, gate_on):
    """Without this the bandit would credit the routed agent for an answer the
    gate rejected, because the task still completes via the escalation target."""
    local = MockWorker("ollama:granite4.1-8b:general", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the local answer"}),
    ])
    cloud = MockWorker("claude-cli:sonnet", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the cloud answer"}),
    ])
    task_id = await _setup(store)

    await run_task(task_id, store, _reg(local, _judge(_SAYS_INCORRECT), cloud), _pass_verifier())

    record = pop_judge_gate(task_id)
    assert record["routed_output_rejected"] is True
    assert record["judged_worker_id"] == local.id
    assert record["judge_worker_id"] == "ollama:qwen3.5:general"
    assert record["verdict"] is False
    assert record["judge_ms"] >= 0.0
    # popped exactly once — no leak into the next task with the same id
    assert pop_judge_gate(task_id) == {}


async def test_accepted_answer_is_still_recorded(store, gate_on):
    """Rejects alone give no escalation *rate* — the denominator needs accepts too."""
    local = MockWorker("ollama:granite4.1-8b:general", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the local answer"}),
    ])
    cloud = MockWorker("claude-cli:sonnet", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "unused"}),
    ])
    task_id = await _setup(store)

    await run_task(task_id, store, _reg(local, _judge(_SAYS_CORRECT), cloud), _pass_verifier())

    record = pop_judge_gate(task_id)
    assert record["routed_output_rejected"] is False
    assert record["verdict"] is True


async def test_unjudged_task_records_nothing(store, monkeypatch):
    """Gate off → no consultation → no row, so it can't dilute the denominator."""
    monkeypatch.delenv("MAHORAGA_JUDGE_GATE", raising=False)
    local = MockWorker("ollama:granite4.1-8b:general", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the local answer"}),
    ])
    cloud = MockWorker("claude-cli:sonnet", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "unused"}),
    ])
    task_id = await _setup(store)

    await run_task(task_id, store, _reg(local, _judge(_SAYS_CORRECT), cloud), _pass_verifier())

    assert pop_judge_gate(task_id) == {}


async def test_reject_reported_even_when_fallback_is_served(store, gate_on):
    """The routed agent's answer was rejected; that it was ultimately served
    anyway (escalation died) doesn't make it the agent's win."""
    local = MockWorker("ollama:granite4.1-8b:general", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the local answer"}),
    ])
    cloud = MockWorker("claude-cli:sonnet", ["file_editing"], [
        WorkerEvent("attempt.failed", {"error_code": "timeout", "error": "cloud timed out"}),
    ])
    task_id = await _setup(store)

    await run_task(task_id, store, _reg(local, _judge(_SAYS_INCORRECT), cloud), _pass_verifier())

    assert await _output_of(store, task_id) == "the local answer"
    record = pop_judge_gate(task_id)
    assert record["routed_output_rejected"] is True
    assert record["served_fallback"] is True


async def test_code_task_judged_under_the_code_rubric(store, gate_on):
    """The rubric follows the bucket the reward path assigns, not a fixed default."""
    seen: dict[str, str] = {}

    class CapturingJudge(MockWorker):
        async def execute(self, attempt, task, feedback=None):
            seen["goal"] = task.goal
            self.execute_called += 1
            yield WorkerEvent("attempt.completed", {"summary": _SAYS_CORRECT})

    local = MockWorker("ollama:granite4.1-8b:general", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "def f(x):\n    return x + 1"}),
    ])
    judge = CapturingJudge("ollama:qwen3.5:general", [], [])
    cloud = MockWorker("claude-cli:sonnet", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "other"}),
    ])
    task_id = await _setup(store, goal="Write a function that increments an integer")

    await run_task(task_id, store, _reg(local, judge, cloud), _pass_verifier())

    assert seen["goal"].startswith(JUDGE_RUBRIC[:60])
