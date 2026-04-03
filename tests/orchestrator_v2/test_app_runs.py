import dataclasses
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport
from backend.orchestrator.store.base import Store
from backend.orchestrator.domain.models import (
    Mission, Plan, Run, RunMode, RunStatus, Task,
)
from backend.orchestrator.workers.registry import WorkerRegistry
from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
from backend.orchestrator.service.app import app, get_store, get_registry, get_verifier
from backend.orchestrator.verifier.verifier import Verifier, VerificationResult
from typing import AsyncIterator


def _make_pass_verifier() -> Verifier:
    result = VerificationResult(score=9, passed=True, feedback="", action="pass")
    v = MagicMock(spec=Verifier)
    v.verify = AsyncMock(return_value=result)
    return v


class _OkWorker(WorkerAdapter):
    @property
    def id(self) -> str:
        return "w"

    @property
    def capabilities(self) -> list[str]:
        return ["file_editing"]

    async def execute(self, attempt, task, feedback=None) -> AsyncIterator[WorkerEvent]:
        yield WorkerEvent("attempt.completed", {"summary": "done"})

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id="w", healthy=True)


@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    yield s
    await s.close()


@pytest.fixture
def registry():
    reg = WorkerRegistry()
    reg.register(_OkWorker())
    return reg


@pytest.fixture
def client_setup(store, registry):
    verifier = _make_pass_verifier()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_verifier] = lambda: verifier
    yield
    app.dependency_overrides.clear()


async def _make_plan(store: Store) -> tuple[Mission, Plan]:
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    return m, p


async def test_start_run_creates_and_returns_run_id(store, client_setup):
    _, p = await _make_plan(store)

    with patch("backend.orchestrator.service.app._run_run", new=AsyncMock()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(f"/runs/{p.id}/start")

    assert resp.status_code == 202
    data = resp.json()
    assert "run_id" in data
    assert len(data["run_id"]) > 0


async def test_start_run_404_on_unknown_plan(store, client_setup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/runs/nonexistent/start")

    assert resp.status_code == 404


async def test_get_run_returns_run(store, client_setup):
    _, p = await _make_plan(store)
    r = Run.new(mission_id=p.mission_id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save_run(r)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/runs/{r.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == r.id
    assert data["plan_id"] == p.id


async def test_get_run_404_on_unknown_id(store, client_setup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/runs/nonexistent")

    assert resp.status_code == 404


async def test_list_runs_returns_runs_for_mission(store, client_setup):
    m, p = await _make_plan(store)
    r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save_run(r)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/runs", params={"mission_id": m.id})

    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 1
    assert runs[0]["id"] == r.id


async def test_list_runs_all(store, client_setup):
    m, p = await _make_plan(store)
    r1 = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    r2 = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save_run(r1)
    await store.missions.save_run(r2)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/runs")

    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_cancel_run_updates_status(store, client_setup):
    m, p = await _make_plan(store)
    r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save_run(r)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete(f"/runs/{r.id}")

    assert resp.status_code == 200
    updated = await store.missions.get_run(r.id)
    assert updated.status == RunStatus.cancelled


async def test_cancel_run_404_on_unknown_id(store, client_setup):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/runs/nonexistent")

    assert resp.status_code == 404
