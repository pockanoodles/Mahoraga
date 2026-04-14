import pytest
from backend.orchestrator.service.wave_executor import WaveExecutor


class _FakeTask:
    def __init__(self, id: str, agent: str, files: list[str] | None = None, deps: list = None):
        self.id = id
        self.scope = files or []
        self.dependencies = deps or []
    # agent is stored in assignments dict, not on the task


def test_single_task_one_wave():
    ex = WaveExecutor(max_concurrent=2)
    tasks = [_FakeTask("t0", "ollama")]
    waves = ex._build_waves(tasks, {"t0": "ollama"})
    assert len(waves) == 1
    assert len(waves[0]) == 1


def test_two_tasks_different_resource_groups_same_wave():
    """ollama (local_ollama) + codex-cli (openai_api) can run concurrently."""
    ex = WaveExecutor(max_concurrent=2)
    tasks = [_FakeTask("t0", "ollama"), _FakeTask("t1", "codex-cli")]
    waves = ex._build_waves(tasks, {"t0": "ollama", "t1": "codex-cli"})
    assert len(waves) == 1
    assert len(waves[0]) == 2


def test_ollama_aider_must_be_sequential():
    """ollama + aider both hit local_ollama (max=1) — must be in separate waves."""
    ex = WaveExecutor(max_concurrent=2)
    tasks = [_FakeTask("t0", "ollama"), _FakeTask("t1", "aider")]
    waves = ex._build_waves(tasks, {"t0": "ollama", "t1": "aider"})
    assert len(waves) == 2


def test_file_overlap_forces_sequential():
    """Tasks writing to the same file go in separate waves."""
    ex = WaveExecutor(max_concurrent=2)
    tasks = [
        _FakeTask("t0", "codex-cli", files=["src/auth.py"]),
        _FakeTask("t1", "gemini-cli", files=["src/auth.py"]),
    ]
    waves = ex._build_waves(tasks, {"t0": "codex-cli", "t1": "gemini-cli"})
    assert len(waves) == 2


def test_no_file_overlap_same_wave():
    ex = WaveExecutor(max_concurrent=2)
    tasks = [
        _FakeTask("t0", "codex-cli", files=["src/hash.py"]),
        _FakeTask("t1", "gemini-cli", files=["src/validation.py"]),
    ]
    waves = ex._build_waves(tasks, {"t0": "codex-cli", "t1": "gemini-cli"})
    assert len(waves) == 1


def test_global_cap_limits_wave_size():
    """Even with heterogeneous groups, global max_concurrent=2 caps wave at 2."""
    ex = WaveExecutor(max_concurrent=2)
    tasks = [
        _FakeTask("t0", "codex-cli"),
        _FakeTask("t1", "gemini-cli"),
        _FakeTask("t2", "claude"),
    ]
    waves = ex._build_waves(tasks, {"t0": "codex-cli", "t1": "gemini-cli", "t2": "claude"})
    assert all(len(w) <= 2 for w in waves)


@pytest.mark.asyncio
async def test_execute_batch_calls_run_single():
    """execute_batch should call run_single for each task."""
    ex = WaveExecutor(max_concurrent=2)
    called = []

    class _FakeDep:
        def __init__(self, task_id):
            self.task_id = task_id

    t0 = _FakeTask("t0", "ollama")
    t1 = _FakeTask("t1", "codex-cli")

    async def _run_single(task, agent):
        called.append((task.id, agent))
        return {"status": "success", "task_index": 0}

    results = await ex.execute_batch([t0, t1], {"t0": "ollama", "t1": "codex-cli"}, _run_single)
    assert len(called) == 2
    assert len(results) == 2
