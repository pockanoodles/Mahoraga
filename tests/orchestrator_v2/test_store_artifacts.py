import pytest
from backend.orchestrator.domain.models import Artifact


async def test_save_and_get_artifact(store):
    art = Artifact.new(run_id="r1", task_id="t1", attempt_id="a1",
                       type="file", location={"path": "/tmp/out.py"})
    await store.artifacts.save(art)
    fetched = await store.artifacts.get(art.id)
    assert fetched is not None
    assert fetched.id == art.id
    assert fetched.type == "file"
    assert fetched.location == {"path": "/tmp/out.py"}
    assert fetched.created_at > 0


async def test_get_missing_artifact_returns_none(store):
    assert await store.artifacts.get("no-such-id") is None


async def test_list_artifacts_by_task(store):
    a1 = Artifact.new(run_id="r1", task_id="t1", attempt_id="a1",
                      type="diff", location={"ref": "x"})
    a2 = Artifact.new(run_id="r1", task_id="t1", attempt_id="a2",
                      type="report", location={"ref": "y"})
    a3 = Artifact.new(run_id="r1", task_id="t2", attempt_id="a3",
                      type="file", location={"ref": "z"})
    for a in [a1, a2, a3]:
        await store.artifacts.save(a)
    results = await store.artifacts.list_by_task("t1")
    ids = {r.id for r in results}
    assert a1.id in ids
    assert a2.id in ids
    assert a3.id not in ids


async def test_list_artifacts_by_run(store):
    a1 = Artifact.new(run_id="r1", task_id="t1", attempt_id="a1",
                      type="file", location={"ref": "a"})
    a2 = Artifact.new(run_id="r2", task_id="t2", attempt_id="a2",
                      type="file", location={"ref": "b"})
    await store.artifacts.save(a1)
    await store.artifacts.save(a2)
    results = await store.artifacts.list_by_run("r1")
    assert len(results) == 1
    assert results[0].id == a1.id


async def test_artifact_location_is_dict(store):
    art = Artifact.new(run_id="r1", task_id="t1", attempt_id="a1",
                       type="planning_output",
                       location={"url": "https://example.com", "format": "json"})
    await store.artifacts.save(art)
    fetched = await store.artifacts.get(art.id)
    assert fetched.location == {"url": "https://example.com", "format": "json"}
