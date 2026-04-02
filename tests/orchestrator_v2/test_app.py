import dataclasses
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from backend.orchestrator.store.base import Store
from backend.orchestrator.domain.models import (
    Mission, Plan, Run, RunMode, Task, TaskStatus,
)
from backend.orchestrator.workers.registry import WorkerRegistry
from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
from backend.orchestrator.service.app import app, get_store, get_registry
from typing import AsyncIterator


# ── test double ───────────────────────────────────────────────────────────────

class _OkWorker(WorkerAdapter):
    @property
    def id(self) -> str:
        return "extension"

    @property
    def capabilities(self) -> list[str]:
        return ["file_editing"]

    async def execute(self, attempt, task) -> AsyncIterator[WorkerEvent]:
        yield WorkerEvent("attempt.completed", {"summary": "done"})

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id="extension", healthy=True)


# ── fixtures ──────────────────────────────────────────────────────────────────

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
def client(store, registry):
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_registry] = lambda: registry
    yield  # client created per test via httpx.AsyncClient
    app.dependency_overrides.clear()


async def _make_run(store: Store) -> tuple[Mission, Plan, Run]:
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    await store.missions.save_run(r)
    return m, p, r


# ── tests ─────────────────────────────────────────────────────────────────────

async def test_get_task_not_found(store, registry, client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/tasks/nonexistent")
    assert resp.status_code == 404


async def test_get_task_returns_task(store, registry, client):
    _, _, run = await _make_run(store)
    task = Task.new(run_id=run.id, title="T", goal="G")
    await store.tasks.save(task)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/tasks/{task.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == task.id


async def test_list_tasks_for_run(store, registry, client):
    _, _, run = await _make_run(store)
    t1 = Task.new(run_id=run.id, title="A", goal="G")
    t2 = Task.new(run_id=run.id, title="B", goal="G")
    await store.tasks.save(t1)
    await store.tasks.save(t2)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/runs/{run.id}/tasks")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_approve_task(store, registry, client):
    _, _, run = await _make_run(store)
    task = Task.new(run_id=run.id, title="T", goal="G")
    task = dataclasses.replace(task, status=TaskStatus.blocked)
    await store.tasks.save(task)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/tasks/{task.id}/approve", json={"run_id": run.id})
    assert resp.status_code == 200
    updated = await store.tasks.get(task.id)
    assert updated.status == TaskStatus.ready


async def test_reject_task(store, registry, client):
    _, _, run = await _make_run(store)
    task = Task.new(run_id=run.id, title="T", goal="G")
    task = dataclasses.replace(task, status=TaskStatus.blocked)
    await store.tasks.save(task)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/tasks/{task.id}/reject", json={"run_id": run.id})
    assert resp.status_code == 200
    updated = await store.tasks.get(task.id)
    assert updated.status == TaskStatus.failed


async def test_workers_health(store, registry, client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/workers/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "extension" in data


async def test_get_run_events(store, registry, client):
    _, _, run = await _make_run(store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/runs/{run.id}/events")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_get_task_events(store, registry, client):
    _, _, run = await _make_run(store)
    task = Task.new(run_id=run.id, title="T", goal="G")
    await store.tasks.save(task)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/tasks/{task.id}/events")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_get_task_attempts(store, registry, client):
    _, _, run = await _make_run(store)
    task = Task.new(run_id=run.id, title="T", goal="G")
    await store.tasks.save(task)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/tasks/{task.id}/attempts")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_execute_task_queued(store, registry, client):
    _, _, run = await _make_run(store)
    task = Task.new(run_id=run.id, title="T", goal="G")
    task = dataclasses.replace(task, status=TaskStatus.ready)
    await store.tasks.save(task)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/tasks/{task.id}/run")
    assert resp.status_code == 202
    data = resp.json()
    assert data["task_id"] == task.id
    assert data["status"] == "queued"


async def test_execute_task_non_ready_returns_409(store, registry, client):
    _, _, run = await _make_run(store)
    import dataclasses as dc
    task = Task.new(run_id=run.id, title="T", goal="G")
    task = dc.replace(task, status=TaskStatus.completed)
    await store.tasks.save(task)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(f"/tasks/{task.id}/run")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_mission(store, registry, client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/missions", json={
            "title": "Build REST API",
            "goal": "Create a user authentication API",
            "background": "",
            "success_condition": "All endpoints return correct responses",
        })
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert data["title"] == "Build REST API"


@pytest.mark.asyncio
async def test_list_missions_empty(store, registry, client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/missions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_mission_not_found(store, registry, client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/missions/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_plan(store, registry, client):
    # Create a mission first
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_resp = await ac.post("/missions", json={
            "title": "Test Mission",
            "goal": "Test goal",
        })
    assert m_resp.status_code == 201
    mission_id = m_resp.json()["id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/plans", json={
            "mission_id": mission_id,
            "mode": "direct",
        })
    assert resp.status_code == 201
    data = resp.json()
    assert "plan_id" in data
    assert "run_id" in data
    assert data["run_status"] == "paused"


@pytest.mark.asyncio
async def test_create_plan_mission_not_found(store, registry, client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/plans", json={"mission_id": "nonexistent", "mode": "direct"})
    assert resp.status_code == 404

@pytest.mark.asyncio
async def test_generate_plan_creates_tasks(store, registry, client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        m_resp = await ac.post("/missions", json={"title": "Build API", "goal": "Make endpoints"})
    mission_id = m_resp.json()["id"]

    stub_tasks = [
        Task.new(run_id="stub", title="Set up project", goal="Create structure"),
        Task.new(run_id="stub", title="Write code", goal="Implement feature"),
    ]

    with patch(
        "backend.orchestrator.service.app.generate_tasks",
        new=AsyncMock(return_value=stub_tasks),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(f"/missions/{mission_id}/generate")

    assert resp.status_code == 201
    data = resp.json()
    assert "plan_id" in data
    assert "run_id" in data
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["title"] == "Set up project"


@pytest.mark.asyncio
async def test_generate_plan_mission_not_found(store, registry, client):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/missions/nonexistent/generate")
    assert resp.status_code == 404
