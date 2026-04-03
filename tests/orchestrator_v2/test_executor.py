import dataclasses
import pytest
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock
from backend.orchestrator.store.base import Store
from backend.orchestrator.domain.models import (
    Task, TaskStatus, TaskAttempt, Mission, Plan, Run, RunMode,
    Dependency, DependencyType,
)
from backend.orchestrator.domain import events as ev_types
from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
from backend.orchestrator.workers.registry import WorkerRegistry
from backend.orchestrator.service.executor import run_task
from backend.orchestrator.verifier.verifier import Verifier, VerificationResult


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

    async def execute(self, attempt: TaskAttempt, task: Task, feedback: str | None = None) -> AsyncIterator[WorkerEvent]:
        self.execute_called += 1
        for ev in self._events:
            yield ev

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id=self.id, healthy=True)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    yield s
    await s.close()


async def _setup(store: Store, **task_kwargs) -> tuple[str, str]:
    """Create mission + plan + run + task in store. Return (run_id, task_id)."""
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    await store.missions.save_run(r)

    defaults = dict(run_id=r.id, title="T", goal="G", required_capabilities=["file_editing"])
    defaults.update(task_kwargs)
    task = Task.new(**defaults)
    task = dataclasses.replace(task, status=TaskStatus.ready)
    await store.tasks.save(task)
    return r.id, task.id


def _reg(*workers) -> WorkerRegistry:
    reg = WorkerRegistry()
    for w in workers:
        reg.register(w)
    return reg


def _make_verifier(action: str, score: int = 9, feedback: str = "") -> Verifier:
    """Return a Verifier mock that always returns the given action."""
    result = VerificationResult(score=score, passed=(action == "pass"), feedback=feedback, action=action)
    v = MagicMock(spec=Verifier)
    v.verify = AsyncMock(return_value=result)
    return v

_PASS_VERIFIER = _make_verifier("pass")


# ── happy path ────────────────────────────────────────────────────────────────

async def test_executor_happy_path_task_completed(store):
    worker = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "done it"}),
    ])
    reg = _reg(worker)
    _, task_id = await _setup(store)

    await run_task(task_id, store, reg, _PASS_VERIFIER)

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed


async def test_executor_happy_path_publishes_task_completed_event(store):
    worker = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "done"}),
    ])
    reg = _reg(worker)
    _, task_id = await _setup(store)

    await run_task(task_id, store, reg, _PASS_VERIFIER)

    events = await store.events.list_by_task(task_id)
    event_types = [e.type for e in events]
    assert ev_types.TASK_COMPLETED in event_types


async def test_executor_attempt_saved(store):
    worker = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "done"}),
    ])
    reg = _reg(worker)
    _, task_id = await _setup(store)
    await run_task(task_id, store, reg, _PASS_VERIFIER)
    attempts = await store.tasks.list_attempts(task_id)
    assert len(attempts) == 1
    assert attempts[0].worker_id == "extension"


# ── escalation ────────────────────────────────────────────────────────────────

async def test_executor_escalates_on_failure(store):
    extension = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.failed", {"error_code": "timeout", "error": "timed out"}),
    ])
    claude = MockWorker("claude", ["file_editing", "deep_reasoning"], [
        WorkerEvent("attempt.completed", {"summary": "fixed it"}),
    ])
    reg = _reg(extension, claude)
    _, task_id = await _setup(store)

    await run_task(task_id, store, reg, _PASS_VERIFIER)

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
    assert task.escalation_count == 1
    attempts = await store.tasks.list_attempts(task_id)
    assert len(attempts) == 2


async def test_executor_escalation_attempt_marked_escalated(store):
    extension = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.failed", {"error_code": "err"}),
    ])
    claude = MockWorker("claude", ["file_editing", "deep_reasoning"], [
        WorkerEvent("attempt.completed", {"summary": "ok"}),
    ])
    reg = _reg(extension, claude)
    _, task_id = await _setup(store)
    await run_task(task_id, store, reg, _PASS_VERIFIER)
    attempts = await store.tasks.list_attempts(task_id)
    from backend.orchestrator.domain.models import AttemptStatus
    assert attempts[0].status == AttemptStatus.escalated


# ── no escalation path ────────────────────────────────────────────────────────

async def test_executor_blocks_when_no_escalation_path(store):
    worker = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.failed", {"error_code": "err", "error": "bad"}),
    ])
    reg = _reg(worker)  # Only one worker, no escalation possible
    _, task_id = await _setup(store)
    await run_task(task_id, store, reg, _PASS_VERIFIER)
    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.blocked


async def test_executor_blocks_publishes_approval_requested(store):
    worker = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.failed", {"error_code": "err"}),
    ])
    reg = _reg(worker)
    _, task_id = await _setup(store)
    await run_task(task_id, store, reg, _PASS_VERIFIER)
    task = await store.tasks.get(task_id)
    events = await store.events.list_by_task(task_id)
    assert any(e.type == ev_types.APPROVAL_REQUESTED for e in events)


# ── worker blocked ────────────────────────────────────────────────────────────

async def test_executor_worker_blocked_blocks_task(store):
    worker = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.blocked", {"reason": "needs human input"}),
    ])
    reg = _reg(worker)
    _, task_id = await _setup(store)
    await run_task(task_id, store, reg, _PASS_VERIFIER)
    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.blocked


# ── stream ends without terminal event ───────────────────────────────────────

async def test_executor_empty_stream_treated_as_failure(store):
    worker = MockWorker("extension", ["file_editing"], [])  # yields nothing
    reg = _reg(worker)
    _, task_id = await _setup(store)
    await run_task(task_id, store, reg, _PASS_VERIFIER)
    task = await store.tasks.get(task_id)
    # No escalation path, so blocked
    assert task.status == TaskStatus.blocked


# ── done_criteria not met ─────────────────────────────────────────────────────

async def test_executor_done_criteria_not_met_treats_as_failure(store):
    worker = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "poor output"}),
    ])
    reg = _reg(worker)  # Only one worker, no escalation possible
    _, task_id = await _setup(store)
    escalate_verifier = _make_verifier("escalate")
    await run_task(task_id, store, reg, escalate_verifier)
    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.blocked


# ── downstream unlock ─────────────────────────────────────────────────────────

async def test_executor_unlocks_downstream_on_completion(store):
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    await store.missions.save_run(r)

    upstream = Task.new(run_id=r.id, title="Upstream", goal="G", required_capabilities=["file_editing"])
    upstream = dataclasses.replace(upstream, status=TaskStatus.ready)
    await store.tasks.save(upstream)

    from backend.orchestrator.domain.models import Dependency, DependencyType
    downstream = Task.new(run_id=r.id, title="Downstream", goal="G",
                          dependencies=[Dependency(task_id=upstream.id, type=DependencyType.completion)])
    await store.tasks.save(downstream)

    worker = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "done"}),
    ])
    reg = _reg(worker)
    await run_task(upstream.id, store, reg, _PASS_VERIFIER)

    ds = await store.tasks.get(downstream.id)
    assert ds.status == TaskStatus.ready


async def test_executor_no_capable_worker_blocks_immediately(store):
    """When no worker can handle the task from the start, it blocks immediately with no attempt."""
    # Task requires a capability that no registered worker has
    worker = MockWorker("extension", ["file_editing"], [])
    reg = _reg(worker)
    _, task_id = await _setup(store, required_capabilities=["deep_reasoning"])
    await run_task(task_id, store, reg, _PASS_VERIFIER)
    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.blocked
    # No attempt was ever created (task was blocked before assignment)
    attempts = await store.tasks.list_attempts(task_id)
    assert len(attempts) == 0


async def test_executor_dispatch_events_published(store):
    """ATTEMPT_ASSIGNED and ATTEMPT_STARTED events are published during dispatch."""
    worker = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "done"}),
    ])
    reg = _reg(worker)
    _, task_id = await _setup(store)
    await run_task(task_id, store, reg, _PASS_VERIFIER)
    events = await store.events.list_by_task(task_id)
    event_types = [e.type for e in events]
    assert ev_types.ATTEMPT_ASSIGNED in event_types
    assert ev_types.ATTEMPT_STARTED in event_types


# ── context propagation ────────────────────────────────────────────────────────

class CapturingWorker(WorkerAdapter):
    """Worker that records every task object it receives."""
    def __init__(self, worker_id: str, capabilities: list[str], events: list[WorkerEvent]):
        self._id = worker_id
        self._caps = capabilities
        self._events = events
        self.received_tasks: list[Task] = []

    @property
    def id(self) -> str:
        return self._id

    @property
    def capabilities(self) -> list[str]:
        return self._caps

    async def execute(self, attempt: TaskAttempt, task: Task, feedback: str | None = None) -> AsyncIterator[WorkerEvent]:
        self.received_tasks.append(task)
        for ev in self._events:
            yield ev

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id=self.id, healthy=True)


async def _setup_pair(store: Store) -> tuple[str, str, str]:
    """Create a run with task A and task B (depends on A). Return (run_id, a_id, b_id)."""
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    await store.missions.save_run(r)

    task_a = Task.new(run_id=r.id, title="Task A", goal="Do A", required_capabilities=["file_editing"])
    task_a = dataclasses.replace(task_a, status=TaskStatus.ready)
    await store.tasks.save(task_a)

    task_b = Task.new(run_id=r.id, title="Task B", goal="Do B", required_capabilities=["file_editing"])
    task_b = dataclasses.replace(task_b, status=TaskStatus.ready, dependencies=[
        Dependency(task_id=task_a.id, type=DependencyType.completion)
    ])
    await store.tasks.save(task_b)

    return r.id, task_a.id, task_b.id


async def test_executor_saves_artifact_on_completion(store):
    worker = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "the output"}),
    ])
    _, task_id = await _setup(store)
    await run_task(task_id, store, _reg(worker), _PASS_VERIFIER)

    artifacts = await store.artifacts.list_by_task(task_id)
    assert len(artifacts) == 1
    assert artifacts[0].type == "text_output"
    assert artifacts[0].location["content"] == "the output"


async def test_executor_injects_upstream_output_into_dependent_task(store):
    _, task_a_id, task_b_id = await _setup_pair(store)

    # Complete task A with a known summary
    worker_a = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "result from A"}),
    ])
    await run_task(task_a_id, store, _reg(worker_a), _PASS_VERIFIER)

    # Run task B — capture what task object it receives
    worker_b = CapturingWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "result from B"}),
    ])
    await run_task(task_b_id, store, _reg(worker_b), _PASS_VERIFIER)

    assert len(worker_b.received_tasks) == 1
    assert "result from A" in worker_b.received_tasks[0].context_refs


async def test_executor_no_upstream_leaves_context_refs_unchanged(store):
    capturing = CapturingWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "done"}),
    ])
    _, task_id = await _setup(store, context_refs=["existing ref"])
    await run_task(task_id, store, _reg(capturing), _PASS_VERIFIER)

    assert capturing.received_tasks[0].context_refs == ["existing ref"]


# ── verifier-driven tests ─────────────────────────────────────────────────────

async def test_executor_passes_on_score_8(store):
    worker = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "good output"}),
    ])
    reg = _reg(worker)
    _, task_id = await _setup(store)
    verifier = _make_verifier("pass", score=9)

    await run_task(task_id, store, reg, verifier)

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
    assert verifier.verify.call_count == 1


async def test_executor_soft_retry_on_score_5(store):
    """score=5 → soft retry → second attempt passes."""
    call_count = [0]

    class RetryThenPassWorker(WorkerAdapter):
        @property
        def id(self): return "extension"
        @property
        def capabilities(self): return ["file_editing"]
        async def execute(self, attempt, task, feedback=None):
            call_count[0] += 1
            yield WorkerEvent("attempt.completed", {"summary": f"output {call_count[0]}"})
        async def cancel(self, attempt_id): pass
        async def health(self): return WorkerHealth(worker_id=self.id, healthy=True)

    retry_result = VerificationResult(score=5, passed=False, feedback="needs more detail", action="retry")
    pass_result = VerificationResult(score=9, passed=True, feedback="", action="pass")
    verifier = MagicMock(spec=Verifier)
    verifier.verify = AsyncMock(side_effect=[retry_result, pass_result])

    reg = _reg(RetryThenPassWorker())
    _, task_id = await _setup(store)
    await run_task(task_id, store, reg, verifier)

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
    assert call_count[0] == 2
    assert verifier.verify.call_count == 2


async def test_executor_escalates_immediately_on_score_2(store):
    """score=2 → skip soft retry, escalate immediately to next worker."""
    extension = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "wrong output"}),
    ])
    claude = MockWorker("claude:sonnet", ["file_editing", "deep_reasoning"], [
        WorkerEvent("attempt.completed", {"summary": "correct output"}),
    ])
    reg = _reg(extension, claude)
    _, task_id = await _setup(store)

    escalate_result = VerificationResult(score=2, passed=False, feedback="wrong direction", action="escalate")
    pass_result = VerificationResult(score=9, passed=True, feedback="", action="pass")
    verifier = MagicMock(spec=Verifier)
    verifier.verify = AsyncMock(side_effect=[escalate_result, pass_result])

    await run_task(task_id, store, reg, verifier)

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
    assert extension.execute_called == 1  # no retry on extension
    assert claude.execute_called == 1


async def test_executor_respects_max_soft_retries(store):
    """After MAX_SOFT_RETRIES=2, escalate even if score is in retry range."""
    call_count = [0]

    class AlwaysRetryWorker(WorkerAdapter):
        @property
        def id(self): return "extension"
        @property
        def capabilities(self): return ["file_editing"]
        async def execute(self, attempt, task, feedback=None):
            call_count[0] += 1
            yield WorkerEvent("attempt.completed", {"summary": "still not good enough"})
        async def cancel(self, attempt_id): pass
        async def health(self): return WorkerHealth(worker_id=self.id, healthy=True)

    retry_result = VerificationResult(score=5, passed=False, feedback="still missing X", action="retry")
    verifier = MagicMock(spec=Verifier)
    verifier.verify = AsyncMock(return_value=retry_result)

    reg = _reg(AlwaysRetryWorker())  # only one worker → escalation leads to block
    _, task_id = await _setup(store)
    await run_task(task_id, store, reg, verifier)

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.blocked
    assert call_count[0] == 3  # original + 2 retries


async def test_executor_verifier_error_escalates(store):
    """VerifierError is treated as escalate — never silently passes bad output."""
    from backend.orchestrator.verifier.verifier import VerifierError
    extension = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "output"}),
    ])
    claude = MockWorker("claude:sonnet", ["file_editing", "deep_reasoning"], [
        WorkerEvent("attempt.completed", {"summary": "better output"}),
    ])
    reg = _reg(extension, claude)
    _, task_id = await _setup(store)

    verifier = MagicMock(spec=Verifier)
    verifier.verify = AsyncMock(side_effect=[
        VerifierError("haiku down"),
        VerificationResult(score=9, passed=True, feedback="", action="pass"),
    ])

    await run_task(task_id, store, reg, verifier)

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
