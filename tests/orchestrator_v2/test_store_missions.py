import pytest
from backend.orchestrator.domain.models import (
    Mission, MissionStatus,
    Plan, PlanStatus,
    Run, RunMode, RunStatus,
)


async def test_save_and_get_mission(store):
    m = Mission.new(title="Refactor auth", goal="Modernise the auth layer")
    await store.missions.save(m)
    fetched = await store.missions.get(m.id)
    assert fetched is not None
    assert fetched.id == m.id
    assert fetched.title == "Refactor auth"
    assert fetched.status == MissionStatus.active
    assert fetched.context_refs == []
    assert fetched.global_constraints == []
    assert fetched.preferences == {}


async def test_get_missing_mission_returns_none_async(store):
    result = await store.missions.get("does-not-exist")
    assert result is None


async def test_save_mission_with_all_fields(store):
    m = Mission.new(
        title="Big refactor",
        goal="Rewrite the pipeline",
        background="Tech debt accumulated over 2 years",
        success_condition="All tests pass, no regressions",
        context_refs=["src/pipeline/", "docs/arch.md"],
        global_constraints=["no_breaking_changes"],
        preferences={"escalation_limit": 3},
    )
    await store.missions.save(m)
    fetched = await store.missions.get(m.id)
    assert fetched.background == "Tech debt accumulated over 2 years"
    assert fetched.context_refs == ["src/pipeline/", "docs/arch.md"]
    assert fetched.global_constraints == ["no_breaking_changes"]
    assert fetched.preferences == {"escalation_limit": 3}


async def test_update_mission_status(store):
    m = Mission.new(title="M", goal="G")
    await store.missions.save(m)
    await store.missions.update_status(m.id, MissionStatus.completed)
    fetched = await store.missions.get(m.id)
    assert fetched.status == MissionStatus.completed


async def test_list_missions(store):
    m1 = Mission.new(title="A", goal="G1")
    m2 = Mission.new(title="B", goal="G2")
    await store.missions.save(m1)
    await store.missions.save(m2)
    results = await store.missions.list()
    ids = {r.id for r in results}
    assert m1.id in ids
    assert m2.id in ids


async def test_save_and_get_plan(store):
    p = Plan.new(mission_id="m1", phases=["setup", "execute"],
                 worker_strategy={"default": "extension"},
                 task_graph_shape="linear")
    await store.missions.save_plan(p)
    fetched = await store.missions.get_plan(p.id)
    assert fetched is not None
    assert fetched.id == p.id
    assert fetched.phases == ["setup", "execute"]
    assert fetched.worker_strategy == {"default": "extension"}
    assert fetched.status == PlanStatus.draft


async def test_update_plan_status(store):
    p = Plan.new(mission_id="m1")
    await store.missions.save_plan(p)
    await store.missions.update_plan_status(p.id, PlanStatus.approved)
    fetched = await store.missions.get_plan(p.id)
    assert fetched.status == PlanStatus.approved


async def test_list_plans_for_mission(store):
    p1 = Plan.new(mission_id="m1")
    p2 = Plan.new(mission_id="m1")
    p3 = Plan.new(mission_id="m2")
    for p in [p1, p2, p3]:
        await store.missions.save_plan(p)
    results = await store.missions.list_plans("m1")
    ids = {r.id for r in results}
    assert p1.id in ids
    assert p2.id in ids
    assert p3.id not in ids


async def test_save_and_get_run(store):
    r = Run.new(mission_id="m1", plan_id="p1", mode=RunMode.direct)
    await store.missions.save_run(r)
    fetched = await store.missions.get_run(r.id)
    assert fetched is not None
    assert fetched.id == r.id
    assert fetched.mode == RunMode.direct
    assert fetched.status == RunStatus.paused


async def test_update_run_status(store):
    r = Run.new(mission_id="m1", plan_id="p1", mode=RunMode.direct)
    await store.missions.save_run(r)
    await store.missions.update_run_status(r.id, RunStatus.active)
    fetched = await store.missions.get_run(r.id)
    assert fetched.status == RunStatus.active
