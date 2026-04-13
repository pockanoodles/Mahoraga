# Ollama Backend + Multi-Model Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Ollama as a fully supported backend with keyword-based task routing, toggled from the chat header.

**Architecture:** A new `MahoragaConfig` persists `active_backend` to `~/.mahoraga/config.json`. When `active_backend == "ollama"`, the Gateway applies `TaskRouter` (pure keyword heuristic) to set `preferred_worker_type` on each task before execution — the existing `assign_worker` logic in the executor picks it up. Four `OllamaWorker` instances (planner, fast, coder, general) are registered at startup alongside Claude workers.

**Tech Stack:** Python/FastAPI backend, httpx async streaming, Ollama `/api/chat` API, vanilla JS frontend.

---

## File Map

### New
| Path | Responsibility |
|------|----------------|
| `backend/orchestrator/config.py` | `MahoragaConfig`: reads/writes `~/.mahoraga/config.json` |
| `backend/orchestrator/workers/ollama.py` | `OllamaWorker` implementing `WorkerAdapter` |
| `backend/orchestrator/workers/router.py` | `TaskRouter`: keyword heuristic → worker_id |
| `tests/orchestrator_v2/test_mahoraga_config.py` | Config layer tests |
| `tests/orchestrator_v2/test_ollama_worker.py` | OllamaWorker unit tests |
| `tests/orchestrator_v2/test_task_router.py` | TaskRouter routing logic tests |
| `tests/orchestrator_v2/test_backend_settings.py` | `/settings/backend` endpoint tests |

### Modified
| Path | What changes |
|------|-------------|
| `backend/orchestrator/gateway.py:25-46` | Accept `MahoragaConfig`; route tasks when `active_backend == "ollama"` |
| `backend/orchestrator/service/app.py:72-125` | Register 4 Ollama workers at lifespan; add `/settings/backend` GET + POST |
| `static/index.html:66-69` | Add `#backend-chip` button between title and gear |
| `static/app.js` | Chip load + toggle logic |
| `static/settings.js` | Three-section drawer: BACKEND / CLAUDE / OLLAMA |
| `static/style.css` | `.backend-chip` and `.chip-active` styles |

---

## Task 1: Config Layer

**Files:**
- Create: `backend/orchestrator/config.py`
- Test: `tests/orchestrator_v2/test_mahoraga_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/orchestrator_v2/test_mahoraga_config.py
import json
import pytest
from pathlib import Path
from backend.orchestrator.config import MahoragaConfig


def test_defaults_when_no_file(tmp_path):
    cfg = MahoragaConfig(path=tmp_path / "config.json")
    assert cfg.get("active_backend") == "claude"
    assert cfg.get("ollama_base_url") == "http://localhost:11434"


def test_set_persists_to_disk(tmp_path):
    path = tmp_path / "config.json"
    cfg = MahoragaConfig(path=path)
    cfg.set("active_backend", "ollama")
    assert json.loads(path.read_text())["active_backend"] == "ollama"


def test_get_reads_persisted_value(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"active_backend": "ollama", "ollama_base_url": "http://localhost:11434"}))
    cfg = MahoragaConfig(path=path)
    assert cfg.get("active_backend") == "ollama"


def test_all_returns_full_dict(tmp_path):
    cfg = MahoragaConfig(path=tmp_path / "config.json")
    result = cfg.all()
    assert "active_backend" in result
    assert "ollama_base_url" in result


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("not valid json {{{")
    cfg = MahoragaConfig(path=path)
    assert cfg.get("active_backend") == "claude"


def test_set_creates_nested_dirs(tmp_path):
    path = tmp_path / "nested" / "deep" / "config.json"
    cfg = MahoragaConfig(path=path)
    cfg.set("active_backend", "ollama")
    assert path.exists()
```

- [ ] **Step 2: Run to verify tests fail**

```
cd /Users/kaitosoeno/Projects/Mahoraga
pytest tests/orchestrator_v2/test_mahoraga_config.py -v
```
Expected: `ModuleNotFoundError` or `ImportError` — `config` doesn't exist yet.

- [ ] **Step 3: Implement `backend/orchestrator/config.py`**

```python
# backend/orchestrator/config.py
from __future__ import annotations
import json
from pathlib import Path

_DEFAULTS: dict = {
    "active_backend": "claude",
    "ollama_base_url": "http://localhost:11434",
}


class MahoragaConfig:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (Path.home() / ".mahoraga" / "config.json")

    def _load(self) -> dict:
        if not self._path.exists():
            return dict(_DEFAULTS)
        try:
            return {**_DEFAULTS, **json.loads(self._path.read_text())}
        except (json.JSONDecodeError, OSError):
            return dict(_DEFAULTS)

    def get(self, key: str):
        return self._load()[key]

    def set(self, key: str, value) -> None:
        data = self._load()
        data[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))

    def all(self) -> dict:
        return self._load()


# Module-level singleton used by app.py and gateway.py
config = MahoragaConfig()
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/orchestrator_v2/test_mahoraga_config.py -v
```
Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/config.py tests/orchestrator_v2/test_mahoraga_config.py
git commit -m "feat: add MahoragaConfig for persistent backend settings"
```

---

## Task 2: OllamaWorker

**Files:**
- Create: `backend/orchestrator/workers/ollama.py`
- Test: `tests/orchestrator_v2/test_ollama_worker.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/orchestrator_v2/test_ollama_worker.py
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.orchestrator.workers.ollama import OllamaWorker
from backend.orchestrator.domain.models import Task, TaskAttempt


def _task(**kwargs) -> Task:
    return Task.new(
        run_id="run-1",
        title=kwargs.get("title", "Write fibonacci"),
        goal=kwargs.get("goal", "Implement the fibonacci function in Python"),
        done_criteria=kwargs.get("done_criteria", ""),
    )


def _attempt() -> TaskAttempt:
    return TaskAttempt.new(task_id="task-1", worker_id="ollama:coder")


def _make_stream_mock(lines: list[str], status_code: int = 200):
    """Build the nested context-manager mock that httpx.AsyncClient.stream() needs."""

    async def fake_aiter_lines():
        for line in lines:
            yield line

    mock_response = MagicMock()
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    mock_response.status_code = status_code
    mock_response.aiter_lines = fake_aiter_lines

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.stream = MagicMock(return_value=mock_response)

    return mock_client


@pytest.mark.asyncio
async def test_execute_completed_on_success():
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    lines = [
        json.dumps({"message": {"content": "def fib"}, "done": False}),
        json.dumps({"message": {"content": "(n): ..."}, "done": True}),
    ]
    mock_client = _make_stream_mock(lines)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=mock_client):
        events = [ev async for ev in worker.execute(_attempt(), _task())]

    assert len(events) == 1
    assert events[0].type == "attempt.completed"
    assert events[0].payload["summary"] == "def fib(n): ..."


@pytest.mark.asyncio
async def test_execute_failed_on_connect_error():
    import httpx
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.stream = MagicMock(side_effect=httpx.ConnectError("refused"))

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=mock_client):
        events = [ev async for ev in worker.execute(_attempt(), _task())]

    assert events[0].type == "attempt.failed"
    assert events[0].payload["error_code"] == "ollama_unreachable"


@pytest.mark.asyncio
async def test_execute_failed_on_empty_response():
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    lines = [json.dumps({"message": {"content": ""}, "done": True})]
    mock_client = _make_stream_mock(lines)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=mock_client):
        events = [ev async for ev in worker.execute(_attempt(), _task())]

    assert events[0].type == "attempt.failed"
    assert events[0].payload["error_code"] == "empty_response"


@pytest.mark.asyncio
async def test_execute_failed_on_http_error():
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    mock_client = _make_stream_mock([], status_code=500)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=mock_client):
        events = [ev async for ev in worker.execute(_attempt(), _task())]

    assert events[0].type == "attempt.failed"
    assert events[0].payload["error_code"] == "http_error"


@pytest.mark.asyncio
async def test_execute_appends_feedback_to_prompt():
    """Verify feedback is appended when provided."""
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    lines = [json.dumps({"message": {"content": "fixed"}, "done": True})]

    captured_payload = {}

    async def fake_aiter_lines():
        for line in lines:
            yield line

    mock_response = MagicMock()
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    mock_response.status_code = 200
    mock_response.aiter_lines = fake_aiter_lines

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    def capture_stream(method, url, json=None, **kwargs):
        captured_payload.update(json or {})
        return mock_response

    mock_client.stream = MagicMock(side_effect=capture_stream)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=mock_client):
        events = [ev async for ev in worker.execute(_attempt(), _task(), feedback="output was wrong")]

    messages = captured_payload.get("messages", [])
    user_msg = next(m for m in messages if m["role"] == "user")
    assert "output was wrong" in user_msg["content"]


@pytest.mark.asyncio
async def test_health_healthy_when_model_available():
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={
        "models": [{"name": "qwen2.5-coder:latest"}]
    })

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=mock_client):
        health = await worker.health()

    assert health.healthy is True


@pytest.mark.asyncio
async def test_health_unhealthy_when_model_missing():
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={"models": [{"name": "llama3:latest"}]})

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=mock_client):
        health = await worker.health()

    assert health.healthy is False
    assert "ollama pull" in health.detail


@pytest.mark.asyncio
async def test_health_unhealthy_on_connect_error():
    import httpx
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=mock_client):
        health = await worker.health()

    assert health.healthy is False
    assert "not running" in health.detail
```

- [ ] **Step 2: Run to verify tests fail**

```
pytest tests/orchestrator_v2/test_ollama_worker.py -v
```
Expected: `ModuleNotFoundError` — `ollama.py` doesn't exist yet.

- [ ] **Step 3: Implement `backend/orchestrator/workers/ollama.py`**

```python
# backend/orchestrator/workers/ollama.py
from __future__ import annotations
import json
import logging
from typing import AsyncGenerator

import httpx

from .base import WorkerAdapter, WorkerEvent, WorkerHealth
from ..domain.models import Task, TaskAttempt

logger = logging.getLogger(__name__)

_SYSTEM_PROMPTS: dict[str, str] = {
    "ollama:planner": (
        "You are a task-planning assistant. Decompose the given task into clear, ordered steps. "
        "Be concise and structured."
    ),
    "ollama:fast": "You are a quick-answer assistant. Answer directly and concisely.",
    "ollama:coder": (
        "You are an expert software engineer. Write clean, correct code. "
        "Explain your implementation briefly."
    ),
    "ollama:general": "You are a knowledgeable assistant. Provide clear, thorough answers.",
}


class OllamaWorker(WorkerAdapter):
    def __init__(
        self,
        model: str,
        worker_id: str,
        base_url: str = "http://localhost:11434",
    ) -> None:
        self._model = model
        self._worker_id = worker_id
        self._base_url = base_url.rstrip("/")
        self._system_prompt = _SYSTEM_PROMPTS.get(worker_id, "You are a helpful assistant.")

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[str]:
        return ["general", "code_generation", "analysis"]

    async def execute(
        self,
        attempt: TaskAttempt,
        task: Task,
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]:
        user_content = f"Task: {task.title}\n\nGoal: {task.goal}"
        if task.done_criteria:
            user_content += f"\n\nDone when: {task.done_criteria}"
        if task.context_refs:
            user_content += "\n\nContext:\n" + "\n".join(task.context_refs)
        if feedback:
            user_content += f"\n\nFeedback on previous attempt: {feedback}"

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_content},
        ]

        full_response: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/api/chat",
                    json={"model": self._model, "messages": messages, "stream": True},
                ) as response:
                    if response.status_code != 200:
                        yield WorkerEvent(
                            type="attempt.failed",
                            payload={
                                "error_code": "http_error",
                                "error": f"Ollama returned HTTP {response.status_code}",
                            },
                        )
                        return
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            full_response.append(content)
                        if chunk.get("done"):
                            break
        except httpx.ConnectError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={
                    "error_code": "ollama_unreachable",
                    "error": f"Ollama is not running at {self._base_url}",
                },
            )
            return
        except Exception as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "stream_error", "error": f"[ERROR] {exc}"},
            )
            return

        summary = "".join(full_response)
        if not summary:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "Ollama returned empty response"},
            )
            return

        yield WorkerEvent(type="attempt.completed", payload={"summary": summary})

    async def cancel(self, attempt_id: str) -> None:
        pass  # Ollama HTTP streaming cannot be cancelled mid-flight; no-op

    async def health(self) -> WorkerHealth:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/api/tags")
            if response.status_code != 200:
                return WorkerHealth(
                    worker_id=self._worker_id,
                    healthy=False,
                    detail="Ollama returned non-200 on /api/tags",
                )
            model_names = [m["name"] for m in response.json().get("models", [])]
            model_base = self._model.split(":")[0]
            if not any(m.startswith(model_base) for m in model_names):
                return WorkerHealth(
                    worker_id=self._worker_id,
                    healthy=False,
                    detail=f"Model {self._model!r} not pulled. Run: ollama pull {self._model}",
                )
            return WorkerHealth(worker_id=self._worker_id, healthy=True)
        except (httpx.ConnectError, httpx.TimeoutException):
            return WorkerHealth(
                worker_id=self._worker_id,
                healthy=False,
                detail=f"Ollama is not running at {self._base_url}",
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/orchestrator_v2/test_ollama_worker.py -v
```
Expected: 8 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/workers/ollama.py tests/orchestrator_v2/test_ollama_worker.py
git commit -m "feat: add OllamaWorker with httpx streaming and health check"
```

---

## Task 3: TaskRouter

**Files:**
- Create: `backend/orchestrator/workers/router.py`
- Test: `tests/orchestrator_v2/test_task_router.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/orchestrator_v2/test_task_router.py
import pytest
from backend.orchestrator.workers.router import TaskRouter
from backend.orchestrator.domain.models import Task


def _task(title: str, goal: str) -> Task:
    return Task.new(run_id="r", title=title, goal=goal)


router = TaskRouter()


def test_code_keywords_route_to_coder():
    t = _task("Write a function", "implement a fibonacci function in Python")
    assert router.route(t, "ollama") == "ollama:coder"


def test_debug_keyword_routes_to_coder():
    t = _task("Fix bug", "debug the authentication module")
    assert router.route(t, "ollama") == "ollama:coder"


def test_refactor_keyword_routes_to_coder():
    t = _task("Refactor", "refactor the database layer")
    assert router.route(t, "ollama") == "ollama:coder"


def test_planning_keyword_routes_to_planner():
    t = _task("Plan the approach", "outline the steps needed to build this feature")
    assert router.route(t, "ollama") == "ollama:planner"


def test_short_task_routes_to_fast():
    t = _task("What is Python", "What is Python")
    assert router.route(t, "ollama") == "ollama:fast"


def test_what_is_phrase_routes_to_fast():
    t = _task("Explain", "what is the difference between TCP and UDP")
    assert router.route(t, "ollama") == "ollama:fast"


def test_how_many_phrase_routes_to_fast():
    t = _task("Count", "how many items are in the list")
    assert router.route(t, "ollama") == "ollama:fast"


def test_general_task_routes_to_general():
    t = _task("Write a blog post", "write a persuasive essay about climate change")
    assert router.route(t, "ollama") == "ollama:general"


def test_analysis_task_routes_to_general():
    t = _task("Summarize findings", "analyze the quarterly revenue data and write a summary")
    assert router.route(t, "ollama") == "ollama:general"


def test_wrong_backend_raises():
    t = _task("anything", "anything")
    with pytest.raises(ValueError, match="ollama"):
        router.route(t, "claude")


def test_import_keyword_routes_to_coder():
    t = _task("Fix imports", "fix the import statements in the module")
    assert router.route(t, "ollama") == "ollama:coder"


def test_api_keyword_routes_to_coder():
    t = _task("Build endpoint", "build a REST API endpoint for user authentication")
    assert router.route(t, "ollama") == "ollama:coder"
```

- [ ] **Step 2: Run to verify tests fail**

```
pytest tests/orchestrator_v2/test_task_router.py -v
```
Expected: `ModuleNotFoundError` — `router.py` doesn't exist yet.

- [ ] **Step 3: Implement `backend/orchestrator/workers/router.py`**

```python
# backend/orchestrator/workers/router.py
from __future__ import annotations
from ..domain.models import Task

_CODE_KEYWORDS = frozenset({
    "code", "function", "implement", "debug", "refactor",
    "script", "class", "test", "fix", "bug", "api", "import",
    "program", "method", "algorithm",
})

_PLANNING_KEYWORDS = frozenset({
    "plan", "outline", "break down", "breakdown", "strategy",
    "approach", "steps", "decompose", "structure", "organize",
})

_FAST_PHRASES = frozenset({"what is", "define", "how many", "what are", "who is"})


class TaskRouter:
    def route(self, task: Task, backend: str) -> str:
        """Return the worker_id for a task given the active backend.

        Raises ValueError if backend is not "ollama".
        """
        if backend != "ollama":
            raise ValueError(f"TaskRouter only routes for ollama backend, got {backend!r}")

        text = f"{task.title} {task.goal}".lower()
        words = text.split()

        # Planning-type task (checked first — takes priority)
        for kw in _PLANNING_KEYWORDS:
            if kw in text:
                return "ollama:planner"

        # Fast: short task or simple Q&A phrase
        if len(words) <= 8 or any(phrase in text for phrase in _FAST_PHRASES):
            return "ollama:fast"

        # Code task
        if any(kw in words for kw in _CODE_KEYWORDS):
            return "ollama:coder"

        return "ollama:general"
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/orchestrator_v2/test_task_router.py -v
```
Expected: 12 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/workers/router.py tests/orchestrator_v2/test_task_router.py
git commit -m "feat: add TaskRouter with keyword-based ollama worker selection"
```

---

## Task 4: Gateway Routing Integration

**Files:**
- Modify: `backend/orchestrator/gateway.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/orchestrator_v2/test_gateway.py` (append, don't replace):

```python
# Add these imports at the top of test_gateway.py if not already present:
# from unittest.mock import AsyncMock, MagicMock, patch
# from backend.orchestrator.config import MahoragaConfig
# from backend.orchestrator.workers.router import TaskRouter

@pytest.mark.asyncio
async def test_gateway_sets_preferred_worker_for_ollama_backend(store, tmp_path):
    """When active_backend is ollama, gateway sets preferred_worker_type on tasks."""
    from backend.orchestrator.config import MahoragaConfig
    from backend.orchestrator.workers.registry import WorkerRegistry
    from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
    from backend.orchestrator.domain.models import Task, TaskAttempt
    from backend.orchestrator.gateway import Gateway
    from backend.orchestrator.verifier.verifier import Verifier, VerificationResult
    from backend.orchestrator.channels.base import ChannelMessage
    from typing import AsyncIterator

    # Config pointing to ollama
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"active_backend": "ollama", "ollama_base_url": "http://localhost:11434"}')
    cfg = MahoragaConfig(path=cfg_path)

    # Worker that accepts any task
    class _AnyWorker(WorkerAdapter):
        @property
        def id(self): return "ollama:general"
        @property
        def capabilities(self): return ["general", "code_generation", "analysis"]
        async def execute(self, attempt, task, feedback=None) -> AsyncIterator[WorkerEvent]:
            yield WorkerEvent("attempt.completed", {"summary": "done"})
        async def cancel(self, attempt_id): pass
        async def health(self): return WorkerHealth(worker_id="ollama:general", healthy=True)

    registry = WorkerRegistry()
    registry.register(_AnyWorker())

    verifier = MagicMock(spec=Verifier)
    verifier.verify = AsyncMock(
        return_value=VerificationResult(score=9, passed=True, feedback="", action="pass")
    )

    saved_tasks: list[Task] = []
    original_save = store.tasks.save

    async def capture_save(task):
        saved_tasks.append(task)
        return await original_save(task)

    store.tasks.save = capture_save

    # Patch generate_tasks to return a single code task
    with patch(
        "backend.orchestrator.gateway.generate_tasks",
        new_callable=AsyncMock,
        return_value=[
            Task.new(run_id="__pending__", title="Write function", goal="implement fibonacci")
        ],
    ):
        gw = Gateway(store=store, registry=registry, verifier=verifier, config=cfg)
        msg = ChannelMessage.new(user_id="test", channel="web", text="write fibonacci")
        chunks = [c async for c in gw.handle_message(msg)]

    assert any(t.preferred_worker_type is not None for t in saved_tasks), \
        "Expected gateway to set preferred_worker_type for ollama tasks"
    assert saved_tasks[0].preferred_worker_type == "ollama:coder"
```

- [ ] **Step 2: Run to verify test fails**

```
pytest tests/orchestrator_v2/test_gateway.py::test_gateway_sets_preferred_worker_for_ollama_backend -v
```
Expected: FAIL — `Gateway.__init__` doesn't accept `config` yet.

- [ ] **Step 3: Modify `backend/orchestrator/gateway.py`**

Add the two imports after the existing imports (around line 20):

```python
from .config import MahoragaConfig
from .workers.router import TaskRouter
```

Change `__init__` signature (lines 28-35) to:

```python
    def __init__(
        self,
        store: Store,
        registry: WorkerRegistry,
        verifier: Verifier,
        adaptive_store=None,
        cost_ledger=None,
        config: MahoragaConfig | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._verifier = verifier
        self._adaptive = adaptive_store
        self._cost_ledger = cost_ledger
        self._learner = Learner()
        self._config = config or MahoragaConfig()
        self._router = TaskRouter()
```

Insert the routing block after the planner call (after line 79, before "# ── 4. Create Plan + Run"):

```python
        # ── 3b. Route tasks to Ollama workers if ollama backend ───────────────
        active_backend = self._config.get("active_backend")
        if active_backend == "ollama":
            tasks = [
                dataclasses.replace(t, preferred_worker_type=self._router.route(t, "ollama"))
                for t in tasks
            ]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/orchestrator_v2/test_gateway.py -v
```
Expected: all existing gateway tests still pass + new test PASSES.

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/gateway.py tests/orchestrator_v2/test_gateway.py
git commit -m "feat: gateway routes tasks to ollama workers when active_backend is ollama"
```

---

## Task 5: App — Register Ollama Workers + Backend Settings Endpoints

**Files:**
- Modify: `backend/orchestrator/service/app.py`
- Test: `tests/orchestrator_v2/test_backend_settings.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/orchestrator_v2/test_backend_settings.py
import json
import pytest
from unittest.mock import patch, MagicMock
from httpx import AsyncClient, ASGITransport
from backend.orchestrator.service.app import app


@pytest.mark.asyncio
async def test_get_backend_settings_returns_defaults():
    with patch("backend.orchestrator.service.app._config") as mock_cfg:
        mock_cfg.all.return_value = {
            "active_backend": "claude",
            "ollama_base_url": "http://localhost:11434",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/settings/backend")

    assert response.status_code == 200
    body = response.json()
    assert body["active_backend"] == "claude"
    assert body["ollama_base_url"] == "http://localhost:11434"


@pytest.mark.asyncio
async def test_post_backend_settings_switches_to_ollama():
    with patch("backend.orchestrator.service.app._config") as mock_cfg:
        mock_cfg.all.return_value = {
            "active_backend": "ollama",
            "ollama_base_url": "http://localhost:11434",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/settings/backend",
                json={"active_backend": "ollama"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["active_backend"] == "ollama"
    mock_cfg.set.assert_called_once_with("active_backend", "ollama")


@pytest.mark.asyncio
async def test_post_backend_settings_rejects_invalid_backend():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/settings/backend",
            json={"active_backend": "openai"},
        )
    assert response.status_code == 422
```

- [ ] **Step 2: Run to verify tests fail**

```
pytest tests/orchestrator_v2/test_backend_settings.py -v
```
Expected: FAIL — endpoints don't exist, `_config` not defined in app.py.

- [ ] **Step 3: Add imports to `backend/orchestrator/service/app.py`**

Near the top of app.py, after the existing imports, add:

```python
from ..config import MahoragaConfig
from ..workers.ollama import OllamaWorker
```

Add `_config` to the globals block (after the existing `_cost_ledger: CostLedger | None = None` line):

```python
_config: MahoragaConfig | None = None
```

- [ ] **Step 4: Register Ollama workers and init config in `lifespan`**

Inside the `lifespan` function, after the Claude workers block (after line 90 `capabilities=["complex_reasoning", "deep_reasoning", "general"],`), add:

```python
    # Always register Ollama workers — they're available regardless of active_backend
    _config = MahoragaConfig()
    ollama_url = _config.get("ollama_base_url")
    _registry.register(OllamaWorker(model="qwen3.5:2b",        worker_id="ollama:planner", base_url=ollama_url))
    _registry.register(OllamaWorker(model="qwen3.5:2b",        worker_id="ollama:fast",    base_url=ollama_url))
    _registry.register(OllamaWorker(model="qwen2.5-coder:7b",  worker_id="ollama:coder",   base_url=ollama_url))
    _registry.register(OllamaWorker(model="qwen3.5:9b",        worker_id="ollama:general", base_url=ollama_url))
```

Pass `config=_config` when constructing Gateway (modify line ~113):

```python
    _gateway = Gateway(
        store=_store,
        registry=_registry,
        verifier=_verifier,
        adaptive_store=_adaptive_store,
        cost_ledger=_cost_ledger,
        config=_config,
    )
```

- [ ] **Step 5: Add `/settings/backend` endpoints**

Add these two endpoints after the existing `GET /settings` endpoint (after line ~471):

```python
class _BackendSettings(BaseModel):
    active_backend: str

    @field_validator("active_backend")
    @classmethod
    def validate_backend(cls, v: str) -> str:
        if v not in ("claude", "ollama"):
            raise ValueError("active_backend must be 'claude' or 'ollama'")
        return v


@app.get("/settings/backend")
async def get_backend_settings():
    """Return current backend config (active_backend + ollama_base_url)."""
    return _config.all()


@app.post("/settings/backend")
async def set_backend_settings(req: _BackendSettings):
    """Switch the active backend. Takes effect on the next request — no restart needed."""
    _config.set("active_backend", req.active_backend)
    return _config.all()
```

Add `field_validator` to the Pydantic imports at the top of app.py:

```python
from pydantic import BaseModel, field_validator
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/orchestrator_v2/test_backend_settings.py -v
```
Expected: 3 PASSED.

- [ ] **Step 7: Run full test suite to catch regressions**

```
pytest tests/orchestrator_v2/ -v --tb=short 2>&1 | tail -30
```
Expected: no new failures.

- [ ] **Step 8: Commit**

```bash
git add backend/orchestrator/service/app.py tests/orchestrator_v2/test_backend_settings.py
git commit -m "feat: register ollama workers at startup and add /settings/backend endpoints"
```

---

## Task 6: Frontend Toggle Chip

**Files:**
- Modify: `static/index.html:66-69`
- Modify: `static/app.js`
- Modify: `static/style.css`

- [ ] **Step 1: Add chip element to `static/index.html`**

Replace lines 66–69:
```html
      <div class="chat-header">
        <span class="chat-header-title">Mahoraga</span>
        <button class="icon-btn" id="settings-btn" title="Settings">⚙</button>
      </div>
```
With:
```html
      <div class="chat-header">
        <span class="chat-header-title">Mahoraga</span>
        <button class="backend-chip chip-active" id="backend-chip">Claude ▾</button>
        <button class="icon-btn" id="settings-btn" title="Settings">⚙</button>
      </div>
```

- [ ] **Step 2: Add chip styles to `static/style.css`**

Append after the `.icon-btn:hover` rule (after line 211):

```css
/* ── Backend toggle chip ── */
.backend-chip {
  font-family: var(--font-ui);
  font-size: 12px;
  font-weight: 500;
  padding: 4px 10px;
  border-radius: 12px;
  border: 1px solid var(--divider);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  background: var(--bg-surface);
  color: var(--text-muted);
}
.backend-chip.chip-active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}
.backend-chip:hover {
  opacity: 0.85;
}
```

- [ ] **Step 3: Add chip logic to `static/app.js`**

Append at the end of `static/app.js`:

```javascript
// ── Backend toggle chip ───────────────────────────────────────────────────────
(function () {
  const chip = document.getElementById('backend-chip');
  if (!chip) return;

  let currentBackend = 'claude';

  async function loadBackend() {
    try {
      const res = await fetch('/settings/backend');
      const data = await res.json();
      currentBackend = data.active_backend;
      chip.textContent = currentBackend === 'claude' ? 'Claude ▾' : 'Ollama ▾';
      chip.classList.toggle('chip-active', currentBackend === 'claude');
    } catch (_) {
      // Silently ignore — chip stays in default state
    }
  }

  chip.addEventListener('click', async () => {
    const next = currentBackend === 'claude' ? 'ollama' : 'claude';
    try {
      await fetch('/settings/backend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active_backend: next }),
      });
      currentBackend = next;
      chip.textContent = next === 'claude' ? 'Claude ▾' : 'Ollama ▾';
      chip.classList.toggle('chip-active', next === 'claude');
    } catch (_) {
      // Silently ignore on network error
    }
  });

  loadBackend();
})();
```

- [ ] **Step 4: Verify visually**

Start the server: `uvicorn backend.orchestrator.service.app:app --reload`

Open `http://localhost:8000`. Confirm:
- Chip labeled "Claude ▾" appears between the title and ⚙ button
- Chip has accent background (blue)
- Clicking once changes label to "Ollama ▾" and chip goes muted
- Clicking again restores "Claude ▾" with accent background

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/app.js static/style.css
git commit -m "feat: add backend toggle chip to chat header"
```

---

## Task 7: Settings Drawer Update

**Files:**
- Modify: `static/settings.js`

- [ ] **Step 1: Replace `static/settings.js`**

Replace the entire file contents with:

```javascript
(() => {
  const settingsBtn = document.getElementById('settings-btn');
  const drawer = document.getElementById('settings-drawer');
  const overlay = document.getElementById('drawer-overlay');
  const drawerBody = document.getElementById('drawer-body');
  const closeBtn = document.getElementById('drawer-close-btn');

  function openDrawer() {
    drawer.style.display = 'flex';
    overlay.style.display = 'block';
    loadSettings();
  }

  function closeDrawer() {
    drawer.style.display = 'none';
    overlay.style.display = 'none';
  }

  async function loadSettings() {
    drawerBody.innerHTML = '<p class="drawer-loading">Loading…</p>';
    try {
      const [sRes, bRes] = await Promise.all([
        fetch('/settings'),
        fetch('/settings/backend'),
      ]);
      const s = await sRes.json();
      const b = await bRes.json();

      drawerBody.innerHTML = `
        <div class="drawer-section">
          <div class="drawer-section-label">BACKEND</div>
          <div class="drawer-row"><span>Active</span><span>${b.active_backend === 'claude' ? 'Claude' : 'Ollama'}</span></div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-label">CLAUDE</div>
          <div class="drawer-row"><span>API Key</span><span class="drawer-mono">${s.anthropic_api_key}</span></div>
          <div class="drawer-row"><span>Planner</span><span class="drawer-mono">claude-haiku-4-5</span></div>
          <div class="drawer-row"><span>Executor</span><span class="drawer-mono">claude-sonnet-4-6</span></div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-label">OLLAMA</div>
          <div class="drawer-row"><span>URL</span><span class="drawer-mono">${b.ollama_base_url}</span></div>
          <div class="drawer-section-label drawer-sub-label">ROUTING TABLE</div>
          <div class="drawer-row"><span>planner</span><span class="drawer-mono">qwen3.5:2b</span></div>
          <div class="drawer-row"><span>fast</span><span class="drawer-mono">qwen3.5:2b</span></div>
          <div class="drawer-row"><span>coder</span><span class="drawer-mono">qwen2.5-coder:7b</span></div>
          <div class="drawer-row"><span>general</span><span class="drawer-mono">qwen3.5:9b</span></div>
        </div>
        <p class="drawer-hint">To change settings, edit your .env file and restart Mahoraga.</p>
      `;
    } catch (err) {
      drawerBody.innerHTML = `<p class="drawer-loading">Failed to load settings.</p>`;
    }
  }

  settingsBtn.addEventListener('click', openDrawer);
  closeBtn.addEventListener('click', closeDrawer);
  overlay.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDrawer();
  });
})();
```

- [ ] **Step 2: Add drawer styles to `static/style.css`**

Append after the `.backend-chip:hover` rule:

```css
/* ── Settings drawer sections ── */
.drawer-section {
  margin-bottom: 20px;
}
.drawer-section-label {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--text-muted);
  text-transform: uppercase;
  margin-bottom: 6px;
}
.drawer-sub-label {
  margin-top: 12px;
}
.drawer-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 5px 0;
  font-size: 13px;
  color: var(--text-primary);
  border-bottom: 1px solid var(--divider);
}
.drawer-row:last-child {
  border-bottom: none;
}
.drawer-mono {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  color: var(--text-muted);
}
.drawer-hint {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 16px;
}
```

- [ ] **Step 3: Verify visually**

Open the settings drawer. Confirm:
- Three labeled sections: BACKEND, CLAUDE, OLLAMA
- BACKEND shows "Active: Claude" (or "Ollama" if toggled)
- CLAUDE section shows masked API key, planner model, executor model
- OLLAMA section shows the URL and routing table (planner/fast/coder/general → model names)
- No inputs or save buttons anywhere

- [ ] **Step 4: Commit**

```bash
git add static/settings.js static/style.css
git commit -m "feat: update settings drawer with BACKEND/CLAUDE/OLLAMA sections"
```

---

## Spec Coverage Check

| Spec Requirement | Covered By |
|-----------------|-----------|
| Ollama backend toggle | Task 5 (endpoint) + Task 6 (chip) |
| 4 Ollama workers registered | Task 5 lifespan |
| Keyword routing heuristic | Task 3 TaskRouter |
| Gateway reads active_backend | Task 4 |
| `~/.mahoraga/config.json` persistence | Task 1 MahoragaConfig |
| `GET /settings/backend` | Task 5 |
| `POST /settings/backend` | Task 5 |
| Chat header toggle chip | Task 6 |
| Settings drawer BACKEND/CLAUDE/OLLAMA | Task 7 |
| Ollama not running → error message | Task 2 OllamaWorker health + error payload |
| Model not pulled → error message | Task 2 OllamaWorker health |
| Mid-stream failure → `[ERROR]` sentinel | Task 2 OllamaWorker execute |
| No restart required to switch backends | Task 4 gateway reads config per request |
