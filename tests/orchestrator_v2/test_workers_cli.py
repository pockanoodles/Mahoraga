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

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
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
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"Usage: opencode [flags]\n  -p, --prompt  headless mode\n", b""))
    with patch("backend.orchestrator.workers.opencode.shutil.which", return_value="/usr/local/bin/opencode"), \
         patch("backend.orchestrator.workers.opencode.asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
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


# ── GeminiWorker ──────────────────────────────────────────────────────────────

def test_gemini_worker_id():
    from backend.orchestrator.workers.gemini import GeminiWorker
    w = GeminiWorker()
    assert w.id == "gemini:cli"


def test_gemini_worker_capabilities():
    from backend.orchestrator.workers.gemini import GeminiWorker
    w = GeminiWorker()
    assert "code" in w.capabilities
    assert "research" in w.capabilities


async def test_gemini_worker_execute_happy_path():
    from backend.orchestrator.workers.gemini import GeminiWorker
    proc = _make_proc(["Gemini response: here is the answer\n"])

    with patch("backend.orchestrator.workers.gemini.asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=proc)):
        w = GeminiWorker()
        events = [ev async for ev in w.execute(make_attempt("gemini:cli"), make_task())]

    completed = [e for e in events if e.type == "attempt.completed"]
    assert len(completed) == 1
    assert "here is the answer" in completed[0].payload["summary"]


async def test_gemini_worker_binary_not_found():
    from backend.orchestrator.workers.gemini import GeminiWorker

    with patch("backend.orchestrator.workers.gemini.asyncio.create_subprocess_exec",
               side_effect=FileNotFoundError("not found")):
        w = GeminiWorker()
        events = [ev async for ev in w.execute(make_attempt("gemini:cli"), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert failed[0].payload["error_code"] == "binary_not_found"


async def test_gemini_worker_empty_response():
    from backend.orchestrator.workers.gemini import GeminiWorker
    proc = _make_proc(["  \n"])

    with patch("backend.orchestrator.workers.gemini.asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=proc)):
        w = GeminiWorker()
        events = [ev async for ev in w.execute(make_attempt("gemini:cli"), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert any(e.payload["error_code"] == "empty_response" for e in failed)


async def test_gemini_worker_health_not_installed():
    from backend.orchestrator.workers.gemini import GeminiWorker
    with patch("backend.orchestrator.workers.gemini.shutil.which", return_value=None):
        w = GeminiWorker()
        h = await w.health()
    assert h.healthy is False
    assert "gemini" in h.detail.lower()


# ── GooseWorker ───────────────────────────────────────────────────────────────

def test_goose_worker_id():
    from backend.orchestrator.workers.goose import GooseWorker
    w = GooseWorker()
    assert w.id == "goose:default"


def test_goose_worker_capabilities():
    from backend.orchestrator.workers.goose import GooseWorker
    w = GooseWorker()
    assert "research" in w.capabilities
    assert "general" in w.capabilities
    assert "explain" in w.capabilities
    assert "code" not in w.capabilities  # Goose is general-purpose, not a coding agent


async def test_goose_worker_execute_happy_path():
    from backend.orchestrator.workers.goose import GooseWorker
    proc = _make_proc(["Goose found: relevant information about the topic\n"])

    with patch("backend.orchestrator.workers.goose.asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=proc)):
        w = GooseWorker()
        events = [ev async for ev in w.execute(make_attempt("goose:default"), make_task())]

    completed = [e for e in events if e.type == "attempt.completed"]
    assert len(completed) == 1
    assert "relevant information" in completed[0].payload["summary"]


async def test_goose_worker_binary_not_found():
    from backend.orchestrator.workers.goose import GooseWorker

    with patch("backend.orchestrator.workers.goose.asyncio.create_subprocess_exec",
               side_effect=FileNotFoundError("not found")):
        w = GooseWorker()
        events = [ev async for ev in w.execute(make_attempt("goose:default"), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert failed[0].payload["error_code"] == "binary_not_found"


async def test_goose_worker_empty_response():
    from backend.orchestrator.workers.goose import GooseWorker
    proc = _make_proc(["\n"])

    with patch("backend.orchestrator.workers.goose.asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=proc)):
        w = GooseWorker()
        events = [ev async for ev in w.execute(make_attempt("goose:default"), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert any(e.payload["error_code"] == "empty_response" for e in failed)


async def test_goose_worker_health_not_installed():
    from backend.orchestrator.workers.goose import GooseWorker
    with patch("backend.orchestrator.workers.goose.shutil.which", return_value=None):
        w = GooseWorker()
        h = await w.health()
    assert h.healthy is False
    assert "goose" in h.detail.lower()
