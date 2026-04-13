"""Tests for CLI-subprocess-based workers: OpenCode, Gemini, Goose."""
from __future__ import annotations
import dataclasses
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.orchestrator.workers.base import WorkerEvent, WorkerHealth
from backend.orchestrator.domain.models import Task, TaskAttempt


def make_task(**kwargs) -> Task:
    t = Task.new(run_id="r1", title="Research task", goal="Find out about X")
    return dataclasses.replace(t, **kwargs) if kwargs else t


def make_attempt(worker_id: str = "opencode:cli") -> TaskAttempt:
    return TaskAttempt.new(task_id="t1", worker_id=worker_id)


class _FakeStream:
    """Fake asyncio.StreamReader that yields pre-set lines then stops."""

    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


def _make_proc(lines: list[str], returncode: int = 0, stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = _FakeStream([l.encode() for l in lines])
    proc.stderr.read = AsyncMock(return_value=stderr)
    proc.wait = AsyncMock()
    return proc


# ── OpenCodeWorker ────────────────────────────────────────────────────────────

def test_opencode_worker_id():
    from backend.orchestrator.workers.opencode import OpenCodeWorker
    w = OpenCodeWorker()
    assert w.id == "opencode:cli"


def test_opencode_worker_capabilities():
    from backend.orchestrator.workers.opencode import OpenCodeWorker
    w = OpenCodeWorker()
    assert "code" in w.capabilities
    assert "general" in w.capabilities


async def test_opencode_worker_execute_happy_path():
    from backend.orchestrator.workers.opencode import OpenCodeWorker
    proc = _make_proc(["Here is the result\n", "More output\n"])

    with patch("backend.orchestrator.workers.opencode.asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=proc)):
        w = OpenCodeWorker()
        events = [ev async for ev in w.execute(make_attempt("opencode:cli"), make_task())]

    completed = [e for e in events if e.type == "attempt.completed"]
    assert len(completed) == 1
    assert "Here is the result" in completed[0].payload["summary"]


async def test_opencode_worker_binary_not_found():
    from backend.orchestrator.workers.opencode import OpenCodeWorker

    with patch("backend.orchestrator.workers.opencode.asyncio.create_subprocess_exec",
               side_effect=FileNotFoundError("not found")):
        w = OpenCodeWorker()
        events = [ev async for ev in w.execute(make_attempt("opencode:cli"), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].payload["error_code"] == "binary_not_found"


async def test_opencode_worker_empty_response():
    from backend.orchestrator.workers.opencode import OpenCodeWorker
    proc = _make_proc(["   \n"])  # whitespace only → stripped to empty

    with patch("backend.orchestrator.workers.opencode.asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=proc)):
        w = OpenCodeWorker()
        events = [ev async for ev in w.execute(make_attempt("opencode:cli"), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert any(e.payload["error_code"] == "empty_response" for e in failed)


async def test_opencode_worker_nonzero_exit():
    from backend.orchestrator.workers.opencode import OpenCodeWorker
    proc = _make_proc([], returncode=1, stderr=b"fatal error")

    with patch("backend.orchestrator.workers.opencode.asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=proc)):
        w = OpenCodeWorker()
        events = [ev async for ev in w.execute(make_attempt("opencode:cli"), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert any(e.payload["error_code"] == "nonzero_exit" for e in failed)


async def test_opencode_worker_health_installed():
    from backend.orchestrator.workers.opencode import OpenCodeWorker
    with patch("backend.orchestrator.workers.opencode.shutil.which", return_value="/usr/local/bin/opencode"):
        w = OpenCodeWorker()
        h = await w.health()
    assert h.healthy is True
    assert h.worker_id == "opencode:cli"


async def test_opencode_worker_health_not_installed():
    from backend.orchestrator.workers.opencode import OpenCodeWorker
    with patch("backend.orchestrator.workers.opencode.shutil.which", return_value=None):
        w = OpenCodeWorker()
        h = await w.health()
    assert h.healthy is False
    assert "opencode" in h.detail.lower()
