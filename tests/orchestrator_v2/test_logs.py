import pytest
from httpx import AsyncClient, ASGITransport
from backend.orchestrator.store.base import Store
from backend.orchestrator.domain.models import Mission, Plan, Run, RunMode
from backend.orchestrator.domain import events as ev_types
from backend.orchestrator.workers.registry import WorkerRegistry
from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
from backend.orchestrator.service.app import app, get_store, get_registry, get_verifier
from backend.orchestrator.verifier.verifier import Verifier, VerificationResult
from unittest.mock import AsyncMock, MagicMock
from typing import AsyncIterator


def _make_pass_verifier() -> Verifier:
    v = MagicMock(spec=Verifier)
    v.verify = AsyncMock(return_value=VerificationResult(score=9, passed=True, feedback="", action="pass"))
    return v


class _StubWorker(WorkerAdapter):
    @property
    def id(self) -> str: return "extension"
    @property
    def capabilities(self) -> list[str]: return ["file_editing"]
    async def execute(self, attempt, task, feedback=None) -> AsyncIterator[WorkerEvent]:
        yield WorkerEvent("attempt.completed", {"summary": "done"})
    async def cancel(self, attempt_id: str) -> None: pass
    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id="extension", healthy=True)


@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    yield s
    await s.close()


@pytest.fixture
def registry():
    reg = WorkerRegistry()
    reg.register(_StubWorker())
    return reg


@pytest.fixture
def client(store, registry):
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_verifier] = lambda: _make_pass_verifier()
    yield
    app.dependency_overrides.clear()


async def _seed_run(store: Store) -> tuple[Mission, Plan, Run]:
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    await store.missions.save_run(r)
    return m, p, r


@pytest.mark.asyncio
async def test_logs_empty(client, store):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/logs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


@pytest.mark.asyncio
async def test_logs_returns_run_with_events(client, store):
    _, _, run = await _seed_run(store)
    event = ev_types.make_event(run.id, ev_types.RUN_STARTED)
    await store.events.append(event)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["runs"]) == 1
    r = data["runs"][0]
    assert r["id"] == run.id
    assert r["status"] == "paused"  # Run.new() starts in paused state
    assert len(r["events"]) == 1
    assert r["events"][0]["type"] == "run.started"
    assert r["events"][0]["task_id"] is None


@pytest.mark.asyncio
async def test_logs_limit(client, store):
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    for _ in range(6):
        r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
        await store.missions.save_run(r)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/logs?limit=5")
    assert resp.status_code == 200
    assert len(resp.json()["runs"]) == 5


@pytest.mark.asyncio
async def test_logs_max_limit_capped_at_20(client, store):
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    for _ in range(25):
        r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
        await store.missions.save_run(r)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/logs?limit=25")
    assert resp.status_code == 200
    assert len(resp.json()["runs"]) == 20
