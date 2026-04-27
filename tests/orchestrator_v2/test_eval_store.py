import pytest
import aiosqlite
from backend.orchestrator.store.eval_store import EvalStore


@pytest.fixture
async def store():
    async with aiosqlite.connect(":memory:") as conn:
        s = EvalStore(conn)
        await s.migrate()
        yield s


@pytest.mark.asyncio
async def test_create_and_finish_run(store):
    run_id = await store.create_run(
        run_type="ab_off",
        routing_enabled=False,
        baseline_policy="fixed:ollama:general",
        suite_name="default_ab",
    )
    assert isinstance(run_id, int)
    assert run_id > 0
    await store.finish_run(run_id)


@pytest.mark.asyncio
async def test_insert_run_task(store):
    run_id = await store.create_run("ab_on", True, None, "default_ab")
    await store.insert_run_task(
        run_id=run_id,
        task_id="code_simple_1",
        task_text="write hello world",
        bucket="code",
        difficulty="simple",
        selected_agent="ollama:general",
        latency_ms=1200.0,
        success=True,
        reward=0.7,
    )
    results = await store.get_run_tasks(run_id)
    assert len(results) == 1
    assert results[0]["selected_agent"] == "ollama:general"
    assert results[0]["success"] == 1


@pytest.mark.asyncio
async def test_get_run_summary(store):
    off_id = await store.create_run("ab_off", False, "fixed:ollama:general", "default_ab")
    on_id = await store.create_run("ab_on", True, None, "default_ab")

    for run_id, agent, latency, success in [
        (off_id, "ollama:general", 2000.0, True),
        (off_id, "ollama:general", 3000.0, False),
        (on_id, "claude:sonnet", 1500.0, True),
        (on_id, "ollama:general", 1200.0, True),
    ]:
        await store.insert_run_task(
            run_id=run_id, task_id="t1", task_text="x",
            bucket="code", difficulty="simple",
            selected_agent=agent, latency_ms=latency, success=success,
        )

    off_summary = await store.get_run_summary(off_id)
    on_summary = await store.get_run_summary(on_id)
    assert off_summary["success_rate"] == pytest.approx(0.5)
    assert on_summary["success_rate"] == pytest.approx(1.0)
