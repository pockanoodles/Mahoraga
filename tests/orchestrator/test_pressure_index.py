# tests/orchestrator/test_pressure_index.py
import pytest
from backend.orchestrator_svc.task_store import TaskStore
from backend.orchestrator_svc.models import Task, Event


@pytest.fixture
async def store(tmp_path):
    s = TaskStore(db_path=tmp_path / "test.db")
    await s.connect()
    yield s
    await s.close()


async def test_events_index_exists(store):
    """events(task_id) index must exist in sqlite_master."""
    async with store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='events'"
    ) as cur:
        rows = await cur.fetchall()
    index_names = [r[0] for r in rows]
    assert any("task_id" in name for name in index_names), (
        f"No task_id index on events table. Found: {index_names}"
    )


async def test_events_composite_index_exists(store):
    """Composite index events(task_id, ts) must exist."""
    async with store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='events'"
    ) as cur:
        rows = await cur.fetchall()
    index_names = [r[0] for r in rows]
    assert any("ts" in name for name in index_names), (
        f"No ts composite index on events table. Found: {index_names}"
    )
