import pytest
from backend.orchestrator.domain.models import Event


async def test_append_and_list_events_by_run(store):
    e1 = Event.new(run_id="r1", type="task.created", payload={"title": "T1"}, task_id="t1")
    e2 = Event.new(run_id="r1", type="task.ready", task_id="t1")
    e3 = Event.new(run_id="r2", type="task.created", task_id="t2")
    for e in [e1, e2, e3]:
        await store.events.append(e)
    results = await store.events.list_by_run("r1")
    assert len(results) == 2
    assert results[0].ts <= results[1].ts  # ordered by ts asc


async def test_append_and_list_events_by_task(store):
    e1 = Event.new(run_id="r1", type="task.created", task_id="t1")
    e2 = Event.new(run_id="r1", type="attempt.started", task_id="t1", attempt_id="a1")
    e3 = Event.new(run_id="r1", type="task.created", task_id="t2")
    for e in [e1, e2, e3]:
        await store.events.append(e)
    results = await store.events.list_by_task("t1")
    ids = {r.id for r in results}
    assert e1.id in ids
    assert e2.id in ids
    assert e3.id not in ids


async def test_events_preserve_payload(store):
    e = Event.new(run_id="r1", type="attempt.failed",
                  payload={"error": "timeout", "code": 504}, task_id="t1")
    await store.events.append(e)
    results = await store.events.list_by_run("r1")
    assert results[0].payload == {"error": "timeout", "code": 504}


async def test_list_events_by_type(store):
    e1 = Event.new(run_id="r1", type="approval.requested", task_id="t1")
    e2 = Event.new(run_id="r1", type="approval.granted", task_id="t1")
    e3 = Event.new(run_id="r1", type="task.completed", task_id="t1")
    for e in [e1, e2, e3]:
        await store.events.append(e)
    granted = await store.events.list_by_type("r1", "approval.granted")
    assert len(granted) == 1
    assert granted[0].id == e2.id


async def test_events_are_ordered_by_ts_asc(store):
    import asyncio
    e1 = Event.new(run_id="r1", type="task.created", task_id="t1")
    await asyncio.sleep(0.01)
    e2 = Event.new(run_id="r1", type="task.ready", task_id="t1")
    await store.events.append(e1)
    await store.events.append(e2)
    results = await store.events.list_by_run("r1")
    assert results[0].id == e1.id
    assert results[1].id == e2.id
