# New Agent Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenCode, Gemini CLI, and Goose as fully-wired agent adapters to Mahoraga's existing subprocess-based adapter system.

**Architecture:** Each new agent gets a `WorkerAdapter` in `workers/` (subprocess execution) and an `AgentAdapter` in `adapters/` (routing/health/cost). Both are registered in `app.py`'s lifespan at startup. Aider is already done — this plan covers the three remaining agents from the spec.

**Tech Stack:** Python 3.12, asyncio subprocess, shutil.which, pytest-asyncio, existing `WorkerAdapter`/`AgentAdapter` ABCs

---

## File Map

### Create
| File | Purpose |
|------|---------|
| `backend/orchestrator/workers/opencode.py` | `OpenCodeWorker` — subprocess via `opencode -p` |
| `backend/orchestrator/workers/gemini.py` | `GeminiWorker` — subprocess via `gemini -p` |
| `backend/orchestrator/workers/goose.py` | `GooseWorker` — subprocess via `goose run` |
| `backend/orchestrator/adapters/opencode_adapter.py` | `OpenCodeAdapter` — routing/health/cost |
| `backend/orchestrator/adapters/gemini_adapter.py` | `GeminiCLIAdapter` — routing/health/cost |
| `backend/orchestrator/adapters/goose_adapter.py` | `GooseAdapter` — routing/health/cost |
| `tests/orchestrator_v2/test_workers_cli.py` | Worker unit tests (all three) |

### Modify
| File | Change |
|------|--------|
| `backend/orchestrator/service/app.py:32-38` | Add imports for 3 new workers + adapters |
| `backend/orchestrator/service/app.py:148-162` | Register 3 new workers + adapters at startup |
| `tests/orchestrator_v2/test_adapters.py` | Add 9 adapter tests (3 per new adapter) |
| `README.md` | Update Supported Agents table |

---

## Task 1: OpenCode Worker + Adapter

**Files:**
- Create: `tests/orchestrator_v2/test_workers_cli.py`
- Create: `backend/orchestrator/workers/opencode.py`
- Modify: `tests/orchestrator_v2/test_adapters.py`
- Create: `backend/orchestrator/adapters/opencode_adapter.py`

---

- [ ] **Step 1: Write failing worker tests**

Create `tests/orchestrator_v2/test_workers_cli.py`:

```python
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
    import shutil as _shutil
    with patch.object(_shutil, "which", return_value="/usr/local/bin/opencode"):
        w = OpenCodeWorker()
        h = await w.health()
    assert h.healthy is True
    assert h.worker_id == "opencode:cli"


async def test_opencode_worker_health_not_installed():
    from backend.orchestrator.workers.opencode import OpenCodeWorker
    import shutil as _shutil
    with patch.object(_shutil, "which", return_value=None):
        w = OpenCodeWorker()
        h = await w.health()
    assert h.healthy is False
    assert "opencode" in h.detail.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_workers_cli.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'backend.orchestrator.workers.opencode'`

- [ ] **Step 3: Implement OpenCodeWorker**

Create `backend/orchestrator/workers/opencode.py`:

```python
"""OpenCodeWorker — subprocess-based WorkerAdapter for OpenCode CLI.

Requirements: npm install -g opencode-ai
              OR: curl -fsSL https://opencode.ai/install | bash
Spawns `opencode -p` (non-interactive prompt mode) and collects stdout.

NOTE: Verify flags with `opencode --help` if behavior is unexpected.
The -p flag is the standard non-interactive convention shared by Claude Code,
Gemini CLI, and OpenCode.
"""
from __future__ import annotations
import asyncio
import logging
import shutil
from typing import AsyncGenerator

from .base import WorkerAdapter, WorkerEvent, WorkerHealth
from ..domain.models import Task, TaskAttempt

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300  # OpenCode tasks can be multi-step — allow longer than simple CLI calls


class OpenCodeWorker(WorkerAdapter):
    def __init__(
        self,
        worker_id: str = "opencode:cli",
        binary_path: str = "opencode",
        model: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._worker_id = worker_id
        self._binary = binary_path
        self._model = model
        self._timeout = timeout

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[str]:
        return ["code", "refactor", "test", "explain", "general"]

    async def execute(
        self,
        attempt: TaskAttempt,
        task: Task,
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]:
        binary = shutil.which(self._binary) or self._binary
        message = f"{task.title}\n\n{task.goal}"
        if task.done_criteria:
            message += f"\n\nDone when: {task.done_criteria}"
        if feedback:
            message += f"\n\nFeedback: {feedback}"

        # -p: non-interactive prompt mode (single-shot)
        # -q: quiet — suppress spinner/progress output for clean subprocess capture
        cmd = [binary, "-p", message, "-q"]
        if self._model:
            cmd.extend(["--model", self._model])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "binary_not_found",
                    "error": "opencode binary not found. Install: npm install -g opencode-ai",
                },
            )
            return

        collected: list[str] = []
        try:
            async with asyncio.timeout(self._timeout):
                assert proc.stdout is not None
                async for line in proc.stdout:
                    text = line.decode("utf-8", errors="replace")
                    collected.append(text)
                await proc.wait()
        except TimeoutError:
            proc.kill()
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "timeout", "error": f"opencode timed out after {self._timeout}s"},
            )
            return
        except Exception as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "stream_error", "error": str(exc)},
            )
            return

        if proc.returncode != 0:
            stderr = b""
            if proc.stderr:
                stderr = await proc.stderr.read()
            yield WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "nonzero_exit",
                    "error": f"opencode exited {proc.returncode}: {stderr.decode(errors='replace')[:200]}",
                },
            )
            return

        summary = "".join(collected).strip()
        if not summary:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "opencode produced no output"},
            )
            return

        yield WorkerEvent(type="attempt.completed", payload={"summary": summary})

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        binary = shutil.which(self._binary)
        if not binary:
            return WorkerHealth(
                worker_id=self._worker_id,
                healthy=False,
                detail="opencode not found in PATH. Install: npm install -g opencode-ai",
            )
        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}")
```

- [ ] **Step 4: Run worker tests to verify they pass**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_workers_cli.py -v -k "opencode"
```

Expected: 7 tests pass, 0 fail

- [ ] **Step 5: Write failing adapter tests**

Append to `tests/orchestrator_v2/test_adapters.py`:

```python

# ── OpenCodeAdapter ───────────────────────────────────────────────────────────

def test_opencode_adapter_declares_capabilities():
    from backend.orchestrator.adapters.opencode_adapter import OpenCodeAdapter
    adapter = OpenCodeAdapter()
    cap_names = {c.name for c in adapter.capabilities}
    assert "code" in cap_names
    assert "refactor" in cap_names
    assert "general" in cap_names


def test_opencode_adapter_cost_no_model_is_free():
    from backend.orchestrator.adapters.opencode_adapter import OpenCodeAdapter
    adapter = OpenCodeAdapter()
    task = Task.new(run_id="r1", title="t", goal="write code")
    est = adapter.estimate_cost(task)
    assert est.estimated_cost_usd == 0.0


def test_opencode_adapter_cost_with_flash_model_is_cheap():
    from backend.orchestrator.adapters.opencode_adapter import OpenCodeAdapter
    adapter = OpenCodeAdapter(model="google/gemini-2.0-flash")
    task = Task.new(run_id="r1", title="t", goal="write code")
    est = adapter.estimate_cost(task)
    assert est.estimated_cost_usd <= 0.002


@pytest.mark.asyncio
async def test_opencode_adapter_health_not_installed():
    from backend.orchestrator.adapters.opencode_adapter import OpenCodeAdapter
    import shutil as _shutil
    with patch.object(_shutil, "which", return_value=None):
        adapter = OpenCodeAdapter()
        status = await adapter.health_check()
    assert status.available is False
    assert "opencode" in status.detail.lower()
```

(Add `from unittest.mock import patch` to the top of `test_adapters.py` if not already there.)

- [ ] **Step 6: Run adapter tests to verify they fail**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_adapters.py -v -k "opencode"
```

Expected: `ModuleNotFoundError: No module named 'backend.orchestrator.adapters.opencode_adapter'`

- [ ] **Step 7: Implement OpenCodeAdapter**

Create `backend/orchestrator/adapters/opencode_adapter.py`:

```python
"""OpenCodeAdapter — AgentAdapter interface for the OpenCode CLI worker."""
from __future__ import annotations
import shutil
import logging
from typing import TYPE_CHECKING

from .base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)

_CAPABILITIES = [
    AgentCapability("code",     confidence=0.92),
    AgentCapability("refactor", confidence=0.88),
    AgentCapability("test",     confidence=0.82),
    AgentCapability("explain",  confidence=0.75),
    AgentCapability("general",  confidence=0.70),
]


class OpenCodeAdapter(AgentAdapter):
    """Routes tasks to OpenCodeWorker — open-source Claude Code alternative with 75+ LLM providers."""

    def __init__(
        self,
        binary_path: str = "opencode",
        model: str | None = None,
    ) -> None:
        self._binary = binary_path
        self._model = model

    @property
    def name(self) -> str:
        return "opencode"

    @property
    def worker_id(self) -> str:
        return "opencode:cli"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return _CAPABILITIES

    def estimate_cost(self, task: "Task") -> CostEstimate:
        model = (self._model or "").lower()
        if not model or "ollama" in model:
            return CostEstimate(
                estimated_cost_usd=0.0,
                model=self._model or "auto",
                notes="No model specified or Ollama — free",
            )
        if "flash" in model:
            return CostEstimate(
                estimated_cost_usd=0.001,
                model=self._model,
                notes="Gemini Flash — low cost",
            )
        return CostEstimate(
            estimated_cost_usd=0.005,
            model=self._model,
            notes="API cost varies by configured provider",
        )

    async def health_check(self) -> AgentStatus:
        binary = shutil.which(self._binary)
        if not binary:
            return AgentStatus(
                name=self.name, available=False,
                detail="opencode not found. Install: npm install -g opencode-ai",
            )
        return AgentStatus(name=self.name, available=True, detail=f"binary={binary}")
```

- [ ] **Step 8: Run adapter tests to verify they pass**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_adapters.py -v -k "opencode"
```

Expected: 4 tests pass

- [ ] **Step 9: Run full test suite to check for regressions**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/ -x -q
```

Expected: all previously-passing tests still pass

- [ ] **Step 10: Commit**

```bash
cd ~/Projects/Mahoraga
git add \
  backend/orchestrator/workers/opencode.py \
  backend/orchestrator/adapters/opencode_adapter.py \
  tests/orchestrator_v2/test_workers_cli.py \
  tests/orchestrator_v2/test_adapters.py
git commit -m "feat: add OpenCode worker and adapter"
```

---

## Task 2: Gemini CLI Worker + Adapter

**Files:**
- Modify: `tests/orchestrator_v2/test_workers_cli.py` (append Gemini tests)
- Create: `backend/orchestrator/workers/gemini.py`
- Modify: `tests/orchestrator_v2/test_adapters.py` (append Gemini tests)
- Create: `backend/orchestrator/adapters/gemini_adapter.py`

---

- [ ] **Step 1: Write failing Gemini worker tests**

Append to `tests/orchestrator_v2/test_workers_cli.py`:

```python

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
    import shutil as _shutil
    with patch.object(_shutil, "which", return_value=None):
        w = GeminiWorker()
        h = await w.health()
    assert h.healthy is False
    assert "gemini" in h.detail.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_workers_cli.py -v -k "gemini"
```

Expected: `ModuleNotFoundError: No module named 'backend.orchestrator.workers.gemini'`

- [ ] **Step 3: Implement GeminiWorker**

Create `backend/orchestrator/workers/gemini.py`:

```python
"""GeminiWorker — subprocess-based WorkerAdapter for Gemini CLI.

Requirements: npm install -g @google/gemini-cli
              gemini auth login  (one-time Google OAuth, or set GEMINI_API_KEY env var)
Free tier: 60 requests/minute, 1000 requests/day.

NOTE: Verify non-interactive flags with `gemini --help` after installation.
The -p flag follows the same convention as Claude Code and OpenCode.
"""
from __future__ import annotations
import asyncio
import logging
import shutil
from typing import AsyncGenerator

from .base import WorkerAdapter, WorkerEvent, WorkerHealth
from ..domain.models import Task, TaskAttempt

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120


class GeminiWorker(WorkerAdapter):
    def __init__(
        self,
        worker_id: str = "gemini:cli",
        binary_path: str = "gemini",
        model: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._worker_id = worker_id
        self._binary = binary_path
        self._model = model
        self._timeout = timeout

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[str]:
        return ["code", "research", "explain", "general"]

    async def execute(
        self,
        attempt: TaskAttempt,
        task: Task,
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]:
        binary = shutil.which(self._binary) or self._binary
        message = f"{task.title}\n\n{task.goal}"
        if task.done_criteria:
            message += f"\n\nDone when: {task.done_criteria}"
        if feedback:
            message += f"\n\nFeedback: {feedback}"

        # -p: non-interactive prompt flag (same convention as Claude Code / OpenCode)
        # If `gemini --help` shows a different flag (e.g. --prompt), update here.
        cmd = [binary, "-p", message]
        if self._model:
            cmd.extend(["--model", self._model])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "binary_not_found",
                    "error": "gemini binary not found. Install: npm install -g @google/gemini-cli",
                },
            )
            return

        collected: list[str] = []
        try:
            async with asyncio.timeout(self._timeout):
                assert proc.stdout is not None
                async for line in proc.stdout:
                    text = line.decode("utf-8", errors="replace")
                    collected.append(text)
                await proc.wait()
        except TimeoutError:
            proc.kill()
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "timeout", "error": f"gemini timed out after {self._timeout}s"},
            )
            return
        except Exception as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "stream_error", "error": str(exc)},
            )
            return

        if proc.returncode != 0:
            stderr = b""
            if proc.stderr:
                stderr = await proc.stderr.read()
            yield WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "nonzero_exit",
                    "error": f"gemini exited {proc.returncode}: {stderr.decode(errors='replace')[:200]}",
                },
            )
            return

        summary = "".join(collected).strip()
        if not summary:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "gemini produced no output"},
            )
            return

        yield WorkerEvent(type="attempt.completed", payload={"summary": summary})

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        binary = shutil.which(self._binary)
        if not binary:
            return WorkerHealth(
                worker_id=self._worker_id,
                healthy=False,
                detail="gemini not found in PATH. Install: npm install -g @google/gemini-cli",
            )
        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}")
```

- [ ] **Step 4: Run Gemini worker tests to verify they pass**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_workers_cli.py -v -k "gemini"
```

Expected: 5 tests pass

- [ ] **Step 5: Write failing Gemini adapter tests**

Append to `tests/orchestrator_v2/test_adapters.py`:

```python

# ── GeminiCLIAdapter ──────────────────────────────────────────────────────────

def test_gemini_adapter_declares_capabilities():
    from backend.orchestrator.adapters.gemini_adapter import GeminiCLIAdapter
    adapter = GeminiCLIAdapter()
    cap_names = {c.name for c in adapter.capabilities}
    assert "code" in cap_names
    assert "research" in cap_names


def test_gemini_adapter_flash_cost_is_free():
    from backend.orchestrator.adapters.gemini_adapter import GeminiCLIAdapter
    adapter = GeminiCLIAdapter()  # defaults to flash model
    task = Task.new(run_id="r1", title="t", goal="write code")
    est = adapter.estimate_cost(task)
    assert est.estimated_cost_usd == 0.0


def test_gemini_adapter_pro_cost_is_nonzero():
    from backend.orchestrator.adapters.gemini_adapter import GeminiCLIAdapter
    adapter = GeminiCLIAdapter(model="gemini-2.0-pro")
    task = Task.new(run_id="r1", title="t", goal="write code")
    est = adapter.estimate_cost(task)
    assert est.estimated_cost_usd > 0.0


@pytest.mark.asyncio
async def test_gemini_adapter_health_not_installed():
    from backend.orchestrator.adapters.gemini_adapter import GeminiCLIAdapter
    import shutil as _shutil
    with patch.object(_shutil, "which", return_value=None):
        adapter = GeminiCLIAdapter()
        status = await adapter.health_check()
    assert status.available is False
    assert "gemini" in status.detail.lower()
```

- [ ] **Step 6: Run adapter tests to verify they fail**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_adapters.py -v -k "gemini"
```

Expected: `ModuleNotFoundError: No module named 'backend.orchestrator.adapters.gemini_adapter'`

- [ ] **Step 7: Implement GeminiCLIAdapter**

Create `backend/orchestrator/adapters/gemini_adapter.py`:

```python
"""GeminiCLIAdapter — AgentAdapter interface for the Gemini CLI worker."""
from __future__ import annotations
import shutil
import logging
from typing import TYPE_CHECKING

from .base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)

_CAPABILITIES = [
    AgentCapability("code",     confidence=0.85),
    AgentCapability("explain",  confidence=0.88),
    AgentCapability("research", confidence=0.82),
    AgentCapability("general",  confidence=0.80),
]


class GeminiCLIAdapter(AgentAdapter):
    """Routes tasks to GeminiWorker — Google's CLI with free tier and web search grounding."""

    def __init__(
        self,
        binary_path: str = "gemini",
        model: str | None = None,
    ) -> None:
        self._binary = binary_path
        self._model = model  # None → gemini picks default (usually 2.0-flash on free tier)

    @property
    def name(self) -> str:
        return "gemini-cli"

    @property
    def worker_id(self) -> str:
        return "gemini:cli"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return _CAPABILITIES

    def estimate_cost(self, task: "Task") -> CostEstimate:
        model = (self._model or "flash").lower()
        if "flash" in model:
            return CostEstimate(
                estimated_cost_usd=0.0,
                model=self._model or "gemini-2.0-flash",
                notes="Gemini Flash free tier: 60 RPM, 1000 req/day",
            )
        return CostEstimate(
            estimated_cost_usd=0.002,
            model=self._model or "gemini-pro",
            notes="Gemini Pro — paid tier",
        )

    async def health_check(self) -> AgentStatus:
        binary = shutil.which(self._binary)
        if not binary:
            return AgentStatus(
                name=self.name, available=False,
                detail="gemini not found. Install: npm install -g @google/gemini-cli",
            )
        return AgentStatus(name=self.name, available=True, detail=f"binary={binary}")
```

- [ ] **Step 8: Run adapter tests to verify they pass**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_adapters.py -v -k "gemini"
```

Expected: 4 tests pass

- [ ] **Step 9: Run full test suite**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/ -x -q
```

Expected: all previously-passing tests still pass

- [ ] **Step 10: Commit**

```bash
cd ~/Projects/Mahoraga
git add \
  backend/orchestrator/workers/gemini.py \
  backend/orchestrator/adapters/gemini_adapter.py \
  tests/orchestrator_v2/test_workers_cli.py \
  tests/orchestrator_v2/test_adapters.py
git commit -m "feat: add Gemini CLI worker and adapter"
```

---

## Task 3: Goose Worker + Adapter

**Files:**
- Modify: `tests/orchestrator_v2/test_workers_cli.py` (append Goose tests)
- Create: `backend/orchestrator/workers/goose.py`
- Modify: `tests/orchestrator_v2/test_adapters.py` (append Goose tests)
- Create: `backend/orchestrator/adapters/goose_adapter.py`

---

- [ ] **Step 1: Write failing Goose worker tests**

Append to `tests/orchestrator_v2/test_workers_cli.py`:

```python

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
    import shutil as _shutil
    with patch.object(_shutil, "which", return_value=None):
        w = GooseWorker()
        h = await w.health()
    assert h.healthy is False
    assert "goose" in h.detail.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_workers_cli.py -v -k "goose"
```

Expected: `ModuleNotFoundError: No module named 'backend.orchestrator.workers.goose'`

- [ ] **Step 3: Implement GooseWorker**

Create `backend/orchestrator/workers/goose.py`:

```python
"""GooseWorker — subprocess-based WorkerAdapter for Goose CLI (Block/Square).

Requirements: brew install goose
              OR: curl -fsSL https://github.com/block/goose/releases/latest/download/install.sh | bash
General-purpose agent — not code-specific. Best for research, writing, automation.

NOTE: Goose's CLI is actively evolving. Verify `goose run` syntax with `goose --help`.
Some versions may use `goose session --non-interactive` instead.
"""
from __future__ import annotations
import asyncio
import logging
import shutil
from typing import AsyncGenerator

from .base import WorkerAdapter, WorkerEvent, WorkerHealth
from ..domain.models import Task, TaskAttempt

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 180


class GooseWorker(WorkerAdapter):
    def __init__(
        self,
        worker_id: str = "goose:default",
        binary_path: str = "goose",
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._worker_id = worker_id
        self._binary = binary_path
        self._timeout = timeout

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[str]:
        return ["research", "general", "explain"]

    async def execute(
        self,
        attempt: TaskAttempt,
        task: Task,
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]:
        binary = shutil.which(self._binary) or self._binary
        message = f"{task.title}\n\n{task.goal}"
        if task.done_criteria:
            message += f"\n\nDone when: {task.done_criteria}"
        if feedback:
            message += f"\n\nFeedback: {feedback}"

        # `goose run` is the non-interactive single-shot mode.
        # If this fails, try: goose session --non-interactive --prompt "..."
        cmd = [binary, "run", message]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "binary_not_found",
                    "error": "goose binary not found. Install: brew install goose",
                },
            )
            return

        collected: list[str] = []
        try:
            async with asyncio.timeout(self._timeout):
                assert proc.stdout is not None
                async for line in proc.stdout:
                    text = line.decode("utf-8", errors="replace")
                    collected.append(text)
                await proc.wait()
        except TimeoutError:
            proc.kill()
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "timeout", "error": f"goose timed out after {self._timeout}s"},
            )
            return
        except Exception as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "stream_error", "error": str(exc)},
            )
            return

        if proc.returncode != 0:
            stderr = b""
            if proc.stderr:
                stderr = await proc.stderr.read()
            yield WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "nonzero_exit",
                    "error": f"goose exited {proc.returncode}: {stderr.decode(errors='replace')[:200]}",
                },
            )
            return

        summary = "".join(collected).strip()
        if not summary:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "goose produced no output"},
            )
            return

        yield WorkerEvent(type="attempt.completed", payload={"summary": summary})

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        binary = shutil.which(self._binary)
        if not binary:
            return WorkerHealth(
                worker_id=self._worker_id,
                healthy=False,
                detail="goose not found in PATH. Install: brew install goose",
            )
        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}")
```

- [ ] **Step 4: Run Goose worker tests to verify they pass**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_workers_cli.py -v -k "goose"
```

Expected: 5 tests pass

- [ ] **Step 5: Write failing Goose adapter tests**

Append to `tests/orchestrator_v2/test_adapters.py`:

```python

# ── GooseAdapter ──────────────────────────────────────────────────────────────

def test_goose_adapter_declares_capabilities():
    from backend.orchestrator.adapters.goose_adapter import GooseAdapter
    adapter = GooseAdapter()
    cap_names = {c.name for c in adapter.capabilities}
    assert "research" in cap_names
    assert "general" in cap_names


def test_goose_adapter_cost_is_zero():
    from backend.orchestrator.adapters.goose_adapter import GooseAdapter
    adapter = GooseAdapter()
    task = Task.new(run_id="r1", title="t", goal="research something")
    est = adapter.estimate_cost(task)
    assert est.estimated_cost_usd == 0.0


def test_goose_adapter_worker_id():
    from backend.orchestrator.adapters.goose_adapter import GooseAdapter
    adapter = GooseAdapter()
    assert adapter.worker_id == "goose:default"
    assert adapter.name == "goose"


@pytest.mark.asyncio
async def test_goose_adapter_health_not_installed():
    from backend.orchestrator.adapters.goose_adapter import GooseAdapter
    import shutil as _shutil
    with patch.object(_shutil, "which", return_value=None):
        adapter = GooseAdapter()
        status = await adapter.health_check()
    assert status.available is False
    assert "goose" in status.detail.lower()
```

- [ ] **Step 6: Run adapter tests to verify they fail**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_adapters.py -v -k "goose"
```

Expected: `ModuleNotFoundError: No module named 'backend.orchestrator.adapters.goose_adapter'`

- [ ] **Step 7: Implement GooseAdapter**

Create `backend/orchestrator/adapters/goose_adapter.py`:

```python
"""GooseAdapter — AgentAdapter interface for the Goose CLI worker."""
from __future__ import annotations
import shutil
import logging
from typing import TYPE_CHECKING

from .base import AgentAdapter, AgentCapability, AgentStatus, CostEstimate

if TYPE_CHECKING:
    from ..domain.models import Task

logger = logging.getLogger(__name__)

_CAPABILITIES = [
    AgentCapability("research", confidence=0.85),
    AgentCapability("general",  confidence=0.82),
    AgentCapability("explain",  confidence=0.78),
]


class GooseAdapter(AgentAdapter):
    """Routes tasks to GooseWorker — Block's general-purpose open-source AI agent."""

    def __init__(self, binary_path: str = "goose") -> None:
        self._binary = binary_path

    @property
    def name(self) -> str:
        return "goose"

    @property
    def worker_id(self) -> str:
        return "goose:default"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return _CAPABILITIES

    def estimate_cost(self, task: "Task") -> CostEstimate:
        # Goose cost depends on its configured provider; default Ollama = free
        return CostEstimate(
            estimated_cost_usd=0.0,
            model="goose-provider",
            notes="Cost depends on Goose's configured provider (Ollama = free)",
        )

    async def health_check(self) -> AgentStatus:
        binary = shutil.which(self._binary)
        if not binary:
            return AgentStatus(
                name=self.name, available=False,
                detail="goose not found. Install: brew install goose",
            )
        return AgentStatus(name=self.name, available=True, detail=f"binary={binary}")
```

- [ ] **Step 8: Run adapter tests to verify they pass**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_adapters.py -v -k "goose"
```

Expected: 4 tests pass

- [ ] **Step 9: Run full test suite**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/ -x -q
```

Expected: all tests pass

- [ ] **Step 10: Commit**

```bash
cd ~/Projects/Mahoraga
git add \
  backend/orchestrator/workers/goose.py \
  backend/orchestrator/adapters/goose_adapter.py \
  tests/orchestrator_v2/test_workers_cli.py \
  tests/orchestrator_v2/test_adapters.py
git commit -m "feat: add Goose worker and adapter"
```

---

## Task 4: Wire All Three into app.py

**Files:**
- Modify: `backend/orchestrator/service/app.py:32-38` (add imports)
- Modify: `backend/orchestrator/service/app.py:148-162` (register workers + adapters)

---

- [ ] **Step 1: Add imports to app.py**

In `backend/orchestrator/service/app.py`, after line 32 (`from ..workers.aider import AiderWorker`), add:

```python
from ..workers.opencode import OpenCodeWorker
from ..workers.gemini import GeminiWorker
from ..workers.goose import GooseWorker
```

After line 38 (`from ..adapters.aider_adapter import AiderAdapter`), add:

```python
from ..adapters.opencode_adapter import OpenCodeAdapter
from ..adapters.gemini_adapter import GeminiCLIAdapter
from ..adapters.goose_adapter import GooseAdapter
```

- [ ] **Step 2: Register workers in lifespan**

In `backend/orchestrator/service/app.py`, after line 148 (`_registry.register(_aider_worker)`), add:

```python
    # ── Register OpenCode worker ──────────────────────────────────────────────
    _opencode_worker = OpenCodeWorker()
    _registry.register(_opencode_worker)

    # ── Register Gemini CLI worker ────────────────────────────────────────────
    _gemini_worker = GeminiWorker()
    _registry.register(_gemini_worker)

    # ── Register Goose worker ─────────────────────────────────────────────────
    _goose_worker = GooseWorker()
    _registry.register(_goose_worker)
```

- [ ] **Step 3: Register adapters in lifespan**

In `backend/orchestrator/service/app.py`, after line 162 (`_adapter_registry.register(AiderAdapter(model=_aider_model))`), add:

```python
    _adapter_registry.register(OpenCodeAdapter())
    _adapter_registry.register(GeminiCLIAdapter())
    _adapter_registry.register(GooseAdapter())
```

- [ ] **Step 4: Run full test suite**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/ -x -q
```

Expected: all tests pass (app.py changes don't break existing tests since workers are registered at startup, not imported at test time)

- [ ] **Step 5: Smoke test the /api/agents/status endpoint**

```bash
cd ~/Projects/Mahoraga
python -m backend.orchestrator.service.app &
sleep 2
curl -s http://localhost:8000/api/agents/status | python3 -m json.tool | grep '"name"'
```

Expected output includes all 7 agents:
```
"name": "ollama",
"name": "claude",        # only if ANTHROPIC_API_KEY is set
"name": "codex-cli",
"name": "aider",
"name": "opencode",
"name": "gemini-cli",
"name": "goose",
```

(Agents without the binary installed will appear with `"available": false` — that's correct behavior.)

```bash
kill %1  # stop the server
```

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/Mahoraga
git add backend/orchestrator/service/app.py
git commit -m "feat: register OpenCode, Gemini CLI, and Goose workers and adapters at startup"
```

---

## Task 5: README Update

**Files:**
- Modify: `README.md`

---

- [ ] **Step 1: Find the Supported Agents table in README.md**

```bash
grep -n "Supported Agents\|Agent.*Type.*Cost\|\| Ollama" ~/Projects/Mahoraga/README.md | head -20
```

- [ ] **Step 2: Replace the Supported Agents table**

Find the existing table (it covers Ollama, Claude, Codex, Aider) and replace with:

```markdown
## Supported Agents

| Agent | Type | Cost | Capabilities | Status |
|-------|------|------|-------------|--------|
| Ollama (Qwen3 4B) | Local inference | Free | Fast Q&A, coding | ✅ Active |
| Claude (Haiku→Opus) | Cloud API | Per-token | Complex reasoning | ✅ Active |
| Codex CLI | Cloud CLI (OpenAI) | Free/$20 | Code generation | ✅ Active |
| Aider | CLI (model-agnostic) | Free + LLM | Git-native coding, refactoring | ✅ Active |
| OpenCode | CLI (75+ providers) | Free + LLM | Full coding agent (Claude Code alternative) | ✅ Active |
| Gemini CLI | Cloud CLI (Google) | Free tier | Code + web search grounding | ✅ Active |
| Goose | CLI (15+ providers) | Free + LLM | Research, writing, general-purpose | ✅ Active |
```

- [ ] **Step 3: Commit**

```bash
cd ~/Projects/Mahoraga
git add README.md
git commit -m "docs: update Supported Agents table with OpenCode, Gemini CLI, and Goose"
```

---

## Self-Review

### Spec Coverage

| Spec requirement | Task covering it |
|-----------------|-----------------|
| AiderAdapter already in spec | Confirmed already done — skipped |
| OpenCode worker (subprocess) | Task 1, Step 3 |
| OpenCode adapter | Task 1, Step 7 |
| Gemini CLI worker | Task 2, Step 3 |
| Gemini CLI adapter | Task 2, Step 7 |
| Goose worker | Task 3, Step 3 |
| Goose adapter | Task 3, Step 7 |
| Register all in registry at startup | Task 4 |
| `health_check()` graceful when not installed | Covered in all adapters |
| `estimate_cost()` by model | Covered in all adapters |
| ≥3 unit tests per adapter | ✅ 4 tests per adapter in plan |
| README Supported Agents table | Task 5 |
| Routing — research tasks go to Goose/Gemini | GooseAdapter/GeminiCLIAdapter declare `research` capability; existing `AdapterRegistry.route()` handles this via capability matching |
| /api/agents/status shows all agents | Task 4, Step 5 smoke test |

### Type Consistency Check

All workers and adapters use:
- `worker_id` in workers matches `adapter.worker_id` in adapters: ✅ `"opencode:cli"` / `"gemini:cli"` / `"goose:default"` consistent throughout
- `WorkerEvent(type="attempt.completed", payload={"summary": ...})` — matches ClaudeWorker/AiderWorker pattern ✅
- `WorkerEvent(type="attempt.failed", payload={"error_code": ..., "error": ...})` — matches pattern ✅
- `WorkerHealth(worker_id=..., healthy=..., detail=...)` — matches base class ✅
- `AgentStatus(name=..., available=..., detail=...)` — matches CodexAdapter/AiderAdapter pattern ✅
- `CostEstimate(estimated_cost_usd=..., model=..., notes=...)` — matches base class fields ✅

### Placeholder Scan

No TBDs, TODOs, or "similar to Task N" references. All code blocks are complete and self-contained.
