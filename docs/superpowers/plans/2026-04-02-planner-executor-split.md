# Planner-Executor Split (Option C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the orchestrator to a three-agent pipeline — PLANNER (qwen3:8b, already done), SENIOR_WORKER (Claude Sonnet), VERIFIER (Claude Haiku with score-based retry), and ESCALATED_WORKER (Claude Opus).

**Architecture:** Workers become stateful per task, maintaining conversation history so verifier feedback can be injected into retry prompts. A new `verifier/` module calls Haiku to score worker output 0–10 against task `done_criteria`; scores 8+ pass, 4–7 trigger up to 2 soft retries with feedback, 0–3 escalate immediately. The executor soft-retry loop drives same-worker retries before hard escalation to Opus.

**Tech Stack:** Python 3.12+, anthropic SDK (`asyncio.to_thread`), pytest-asyncio, FastAPI (lifespan wiring)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/orchestrator/verifier/__init__.py` | Create | Module export |
| `backend/orchestrator/verifier/config.py` | Create | `PASS_THRESHOLD=8`, `RETRY_THRESHOLD=4`, `MAX_SOFT_RETRIES=2`, model strings |
| `backend/orchestrator/verifier/prompt.py` | Create | Haiku system prompt + `build_verify_message()` |
| `backend/orchestrator/verifier/verifier.py` | Create | `VerificationResult`, `VerifierError`, `Verifier` class |
| `backend/orchestrator/workers/base.py` | Modify | Add `feedback` param to `execute()` ABC; add `clear_history()` no-op default |
| `backend/orchestrator/workers/claude.py` | Modify | Stateful history; configurable model/id/caps; sonnet + opus registrations |
| `backend/orchestrator/workers/ollama.py` | Modify | Add `feedback` param + history (interface compliance) |
| `backend/orchestrator/service/executor.py` | Modify | Replace `verify_done_criteria` with `Verifier`; add soft retry loop |
| `backend/orchestrator/service/run_executor.py` | Modify | Pass `verifier` through to `run_task()` |
| `backend/orchestrator/service/app.py` | Modify | Haiku `Verifier` singleton; register `claude:sonnet` + `claude:opus` |
| `tests/orchestrator_v2/test_verifier.py` | Create | Unit tests for `Verifier` |
| `tests/orchestrator_v2/test_claude_worker.py` | Modify | Update for new interface; add history tests |
| `tests/orchestrator_v2/test_executor.py` | Modify | Update `MockWorker`; add verifier-driven retry tests |
| `tests/orchestrator_v2/test_ollama_worker.py` | Modify | Update `execute()` call signatures |

---

### Task 1: Verifier config and prompt

**Files:**
- Create: `backend/orchestrator/verifier/__init__.py`
- Create: `backend/orchestrator/verifier/config.py`
- Create: `backend/orchestrator/verifier/prompt.py`

- [ ] **Step 1: Create the verifier package**

```python
# backend/orchestrator/verifier/__init__.py
from .verifier import Verifier, VerificationResult, VerifierError

__all__ = ["Verifier", "VerificationResult", "VerifierError"]
```

- [ ] **Step 2: Create config.py**

```python
# backend/orchestrator/verifier/config.py
PASS_THRESHOLD = 8       # score >= 8 → pass
RETRY_THRESHOLD = 4      # score 4-7 → soft retry; score 0-3 → hard escalate
MAX_SOFT_RETRIES = 2     # max same-worker retries before hard escalation

VERIFIER_MODEL = "claude-haiku-4-5-20251001"
```

- [ ] **Step 3: Create prompt.py**

```python
# backend/orchestrator/verifier/prompt.py
from __future__ import annotations
from ..domain.models import Task

SYSTEM_PROMPT = """\
You are a strict task evaluator. You receive a task goal, done criteria, and a worker's output.
Score the output 0-10 based on how well it satisfies the done criteria.

Scoring guide:
- 8-10: Output fully satisfies the done criteria
- 4-7: Output partially satisfies the done criteria but has notable gaps or errors
- 0-3: Output does not satisfy the done criteria or addresses the wrong problem

Respond with JSON only, no other text:
{"score": <integer 0-10>, "feedback": "<what is missing or wrong; empty string if score >= 8>"}
"""


def build_verify_message(task: Task, output: str) -> str:
    lines = [
        f"## Task Goal\n{task.goal}",
        f"## Done Criteria\n{task.done_criteria or '(none specified — score based on goal completion)'}",
        f"## Worker Output\n{output}",
    ]
    return "\n\n".join(lines)
```

- [ ] **Step 4: Commit**

```bash
git add backend/orchestrator/verifier/
git commit -m "feat(verifier): add verifier package, config, and prompt"
```

---

### Task 2: VerificationResult and Verifier class

**Files:**
- Create: `backend/orchestrator/verifier/verifier.py`
- Create: `tests/orchestrator_v2/test_verifier.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/orchestrator_v2/test_verifier.py
import dataclasses
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from backend.orchestrator.verifier.verifier import Verifier, VerificationResult, VerifierError
from backend.orchestrator.domain.models import Task


def make_task(goal="Fix auth", done_criteria="All auth tests pass") -> Task:
    t = Task.new(run_id="r1", title="T", goal=goal)
    return dataclasses.replace(t, done_criteria=done_criteria)


def _mock_client(score: int, feedback: str = "") -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps({"score": score, "feedback": feedback}))]
    client = MagicMock()
    client.messages.create = MagicMock(return_value=resp)
    return client


async def test_verify_score_8_returns_pass():
    client = _mock_client(score=8)
    v = Verifier(client)
    result = await v.verify(make_task(), "output text")
    assert result.passed is True
    assert result.action == "pass"
    assert result.score == 8


async def test_verify_score_10_returns_pass():
    client = _mock_client(score=10)
    v = Verifier(client)
    result = await v.verify(make_task(), "output")
    assert result.passed is True
    assert result.action == "pass"


async def test_verify_score_7_returns_retry():
    client = _mock_client(score=7, feedback="missing edge case")
    v = Verifier(client)
    result = await v.verify(make_task(), "output")
    assert result.passed is False
    assert result.action == "retry"
    assert result.feedback == "missing edge case"


async def test_verify_score_4_returns_retry():
    client = _mock_client(score=4, feedback="incomplete")
    v = Verifier(client)
    result = await v.verify(make_task(), "output")
    assert result.action == "retry"


async def test_verify_score_3_returns_escalate():
    client = _mock_client(score=3, feedback="wrong direction")
    v = Verifier(client)
    result = await v.verify(make_task(), "output")
    assert result.passed is False
    assert result.action == "escalate"


async def test_verify_score_0_returns_escalate():
    client = _mock_client(score=0, feedback="completely wrong")
    v = Verifier(client)
    result = await v.verify(make_task(), "output")
    assert result.action == "escalate"


async def test_verify_bad_json_raises_verifier_error():
    resp = MagicMock()
    resp.content = [MagicMock(text="not json at all")]
    client = MagicMock()
    client.messages.create = MagicMock(return_value=resp)
    v = Verifier(client)
    with pytest.raises(VerifierError):
        await v.verify(make_task(), "output")


async def test_verify_missing_score_key_raises_verifier_error():
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps({"feedback": "ok"}))]
    client = MagicMock()
    client.messages.create = MagicMock(return_value=resp)
    v = Verifier(client)
    with pytest.raises(VerifierError):
        await v.verify(make_task(), "output")


async def test_verify_api_exception_raises_verifier_error():
    client = MagicMock()
    client.messages.create = MagicMock(side_effect=Exception("API down"))
    v = Verifier(client)
    with pytest.raises(VerifierError):
        await v.verify(make_task(), "output")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/kaitosoeno/Projects/ollama-runtime
python -m pytest tests/orchestrator_v2/test_verifier.py -v 2>&1 | head -30
```

Expected: `ImportError` or `ModuleNotFoundError` — `verifier.py` doesn't exist yet.

- [ ] **Step 3: Implement verifier.py**

```python
# backend/orchestrator/verifier/verifier.py
from __future__ import annotations
import asyncio
import json
from dataclasses import dataclass

import anthropic

from ..domain.models import Task
from .config import PASS_THRESHOLD, RETRY_THRESHOLD, VERIFIER_MODEL
from .prompt import SYSTEM_PROMPT, build_verify_message


class VerifierError(RuntimeError):
    """Raised when Haiku returns unparseable output or the API call fails."""


@dataclass
class VerificationResult:
    score: int       # 0-10
    passed: bool     # score >= PASS_THRESHOLD
    feedback: str    # populated when not passed
    action: str      # "pass" | "retry" | "escalate"

    @classmethod
    def from_score(cls, score: int, feedback: str) -> "VerificationResult":
        passed = score >= PASS_THRESHOLD
        if passed:
            action = "pass"
        elif score >= RETRY_THRESHOLD:
            action = "retry"
        else:
            action = "escalate"
        return cls(score=score, passed=passed, feedback=feedback, action=action)


class Verifier:
    def __init__(self, client: anthropic.Anthropic, model: str = VERIFIER_MODEL) -> None:
        self._client = client
        self._model = model

    async def verify(self, task: Task, output: str) -> VerificationResult:
        """Call Haiku to score worker output against task done_criteria.

        Raises VerifierError on API failure or unparseable response.
        """
        user_msg = build_verify_message(task, output)
        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                model=self._model,
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
        except Exception as exc:
            raise VerifierError(f"Haiku API call failed: {exc}") from exc

        raw = response.content[0].text if response.content else ""
        try:
            data = json.loads(raw)
            score = int(data["score"])
            feedback = data.get("feedback", "")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise VerifierError(
                f"Haiku returned unparseable output: {raw!r}"
            ) from exc

        return VerificationResult.from_score(score, feedback)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/orchestrator_v2/test_verifier.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator/verifier/verifier.py tests/orchestrator_v2/test_verifier.py
git commit -m "feat(verifier): add Verifier, VerificationResult, VerifierError with tests"
```

---

### Task 3: WorkerAdapter base interface update

**Files:**
- Modify: `backend/orchestrator/workers/base.py`

- [ ] **Step 1: Update base.py**

Replace the `execute` abstract method signature and add `clear_history` default:

```python
# backend/orchestrator/workers/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, TYPE_CHECKING

if TYPE_CHECKING:
    from ..domain.models import Task, TaskAttempt


@dataclass
class WorkerEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerHealth:
    worker_id: str
    healthy: bool
    detail: str = ""


def _build_prompt(task: "Task") -> str:
    """Build a focused prompt from task fields. Selective context injection."""
    lines = [f"# Task: {task.title}", f"\n## Goal\n{task.goal}"]
    if task.context_refs:
        lines.append("\n## Context\n" + "\n".join(f"- {ref}" for ref in task.context_refs))
    if task.constraints:
        lines.append("\n## Constraints\n" + "\n".join(f"- {c}" for c in task.constraints))
    if task.done_criteria:
        lines.append(f"\n## Done Criteria\n{task.done_criteria}")
    return "\n".join(lines)


class WorkerAdapter(ABC):
    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> list[str]: ...

    @abstractmethod
    async def execute(
        self,
        attempt: "TaskAttempt",
        task: "Task",
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]: ...

    @abstractmethod
    async def cancel(self, attempt_id: str) -> None: ...

    @abstractmethod
    async def health(self) -> WorkerHealth: ...

    def clear_history(self, task_id: str) -> None:
        """Clear per-task conversation state. Stateless workers ignore this."""
        pass
```

- [ ] **Step 2: Run existing tests to confirm nothing broke**

```bash
python -m pytest tests/orchestrator_v2/ -v --tb=short 2>&1 | tail -20
```

Expected: same tests pass as before (base.py change is additive — default param + no-op method).

- [ ] **Step 3: Commit**

```bash
git add backend/orchestrator/workers/base.py
git commit -m "feat(workers): add feedback param to execute() ABC and clear_history() default"
```

---

### Task 4: ClaudeWorker — stateful history + sonnet/opus split

**Files:**
- Modify: `backend/orchestrator/workers/claude.py`
- Modify: `tests/orchestrator_v2/test_claude_worker.py`

- [ ] **Step 1: Write new/updated tests first**

Replace the entire contents of `tests/orchestrator_v2/test_claude_worker.py`:

```python
# tests/orchestrator_v2/test_claude_worker.py
import dataclasses
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call
from backend.orchestrator.workers.claude import ClaudeWorker
from backend.orchestrator.workers.base import WorkerEvent, _build_prompt
from backend.orchestrator.domain.models import Task, TaskAttempt


def make_task(**kwargs) -> Task:
    t = Task.new(run_id="r1", title="Fix auth", goal="Fix the login bug")
    return dataclasses.replace(t, **kwargs) if kwargs else t


def make_attempt(worker_id="claude:sonnet") -> TaskAttempt:
    return TaskAttempt.new(task_id="t1", worker_id=worker_id)


def _mock_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


# ── identity ──────────────────────────────────────────────────────────────────

def test_sonnet_worker_id():
    w = ClaudeWorker(api_key="fake")
    assert w.id == "claude:sonnet"


def test_opus_worker_id():
    w = ClaudeWorker(api_key="fake", model="claude-opus-4-6", worker_id="claude:opus",
                     capabilities=["complex_reasoning", "deep_reasoning", "general"])
    assert w.id == "claude:opus"


def test_sonnet_default_capabilities():
    w = ClaudeWorker(api_key="fake")
    assert "deep_reasoning" in w.capabilities
    assert "general" in w.capabilities


def test_opus_capabilities():
    w = ClaudeWorker(api_key="fake", model="claude-opus-4-6", worker_id="claude:opus",
                     capabilities=["complex_reasoning", "deep_reasoning", "general"])
    assert "complex_reasoning" in w.capabilities


# ── _build_prompt (still exported from base, re-tested here for regression) ──

def test_build_prompt_includes_goal():
    task = make_task(goal="Fix the login redirect bug")
    prompt = _build_prompt(task)
    assert "Fix the login redirect bug" in prompt


def test_build_prompt_includes_done_criteria():
    task = make_task(done_criteria="All auth tests pass")
    prompt = _build_prompt(task)
    assert "All auth tests pass" in prompt


# ── first execute (no feedback) ───────────────────────────────────────────────

async def test_execute_yields_completed_on_success():
    with patch("backend.orchestrator.workers.claude.asyncio.to_thread",
               new=AsyncMock(return_value=_mock_response("I fixed the bug"))):
        w = ClaudeWorker(api_key="fake")
        events = [ev async for ev in w.execute(make_attempt(), make_task())]
    completed = [e for e in events if e.type == "attempt.completed"]
    assert len(completed) == 1
    assert "I fixed the bug" in completed[0].payload["summary"]


async def test_execute_first_call_sends_single_user_message():
    captured = {}
    async def fake_to_thread(fn, **kwargs):
        captured.update(kwargs)
        return _mock_response("result")

    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", side_effect=fake_to_thread):
        w = ClaudeWorker(api_key="fake")
        task = make_task()
        _ = [ev async for ev in w.execute(make_attempt(), task)]

    messages = captured["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert task.goal in messages[0]["content"]


# ── retry with feedback ───────────────────────────────────────────────────────

async def test_execute_retry_appends_feedback_to_history():
    call_messages = []

    async def fake_to_thread(fn, **kwargs):
        call_messages.append(list(kwargs["messages"]))
        return _mock_response("attempt output")

    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", side_effect=fake_to_thread):
        w = ClaudeWorker(api_key="fake")
        task = make_task()
        attempt1 = make_attempt()
        attempt2 = make_attempt()

        # First call
        _ = [ev async for ev in w.execute(attempt1, task, feedback=None)]
        # Retry call with feedback
        _ = [ev async for ev in w.execute(attempt2, task, feedback="Missing X, add Y")]

    # First call: 1 message
    assert len(call_messages[0]) == 1
    # Retry call: 3 messages [user:prompt, assistant:prior_output, user:feedback]
    assert len(call_messages[1]) == 3
    assert call_messages[1][1]["role"] == "assistant"
    assert call_messages[1][1]["content"] == "attempt output"
    assert call_messages[1][2]["role"] == "user"
    assert "Missing X, add Y" in call_messages[1][2]["content"]


async def test_execute_second_retry_has_five_messages():
    """Two retries → history grows: [user, assistant, user, assistant, user]."""
    call_messages = []
    call_count = [0]

    async def fake_to_thread(fn, **kwargs):
        call_messages.append(list(kwargs["messages"]))
        call_count[0] += 1
        return _mock_response(f"output {call_count[0]}")

    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", side_effect=fake_to_thread):
        w = ClaudeWorker(api_key="fake")
        task = make_task()
        _ = [ev async for ev in w.execute(make_attempt(), task)]
        _ = [ev async for ev in w.execute(make_attempt(), task, feedback="first feedback")]
        _ = [ev async for ev in w.execute(make_attempt(), task, feedback="second feedback")]

    assert len(call_messages[2]) == 5


# ── clear_history ─────────────────────────────────────────────────────────────

async def test_clear_history_resets_task_state():
    async def fake_to_thread(fn, **kwargs):
        return _mock_response("output")

    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", side_effect=fake_to_thread):
        w = ClaudeWorker(api_key="fake")
        task = make_task()
        _ = [ev async for ev in w.execute(make_attempt(), task)]
        w.clear_history(task.id)
        # After clear, retry with feedback should be treated as first call (1 message)
        call_messages = []
        async def capture(fn, **kwargs):
            call_messages.append(kwargs["messages"])
            return _mock_response("fresh")
        with patch("backend.orchestrator.workers.claude.asyncio.to_thread", side_effect=capture):
            _ = [ev async for ev in w.execute(make_attempt(), task, feedback="ignored after clear")]
    assert len(call_messages[0]) == 1


# ── error paths ───────────────────────────────────────────────────────────────

async def test_execute_yields_failed_on_empty_response():
    resp = MagicMock()
    resp.content = []
    with patch("backend.orchestrator.workers.claude.asyncio.to_thread", new=AsyncMock(return_value=resp)):
        w = ClaudeWorker(api_key="fake")
        events = [ev async for ev in w.execute(make_attempt(), make_task())]
    failed = [e for e in events if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].payload["error_code"] == "empty_response"


async def test_execute_yields_failed_on_api_error():
    with patch("backend.orchestrator.workers.claude.asyncio.to_thread",
               new=AsyncMock(side_effect=Exception("API error"))):
        w = ClaudeWorker(api_key="fake")
        events = [ev async for ev in w.execute(make_attempt(), make_task())]
    failed = [e for e in events if e.type == "attempt.failed"]
    assert failed[0].payload["error_code"] == "api_error"


async def test_health_returns_healthy():
    w = ClaudeWorker(api_key="fake")
    h = await w.health()
    assert h.healthy is True
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/orchestrator_v2/test_claude_worker.py -v 2>&1 | tail -20
```

Expected: multiple failures — `claude:sonnet` id, history methods don't exist yet.

- [ ] **Step 3: Rewrite claude.py**

```python
# backend/orchestrator/workers/claude.py
from __future__ import annotations
import asyncio
from typing import AsyncGenerator

import anthropic

from ..domain.models import Task, TaskAttempt
from .base import WorkerAdapter, WorkerEvent, WorkerHealth, _build_prompt


class ClaudeWorker(WorkerAdapter):
    """Worker backed by the Anthropic API with stateful per-task conversation history."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        worker_id: str = "claude:sonnet",
        capabilities: list[str] | None = None,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._worker_id = worker_id
        self._capabilities = capabilities or ["general", "deep_reasoning"]
        # Per-task conversation history keyed by task_id
        self._history: dict[str, list[dict[str, str]]] = {}
        self._last_output: dict[str, str] = {}

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[str]:
        return self._capabilities

    async def execute(
        self,
        attempt: TaskAttempt,
        task: Task,
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]:
        task_id = task.id

        if task_id not in self._history or feedback is None:
            # First call for this task: build fresh history
            self._history[task_id] = [{"role": "user", "content": _build_prompt(task)}]
        else:
            # Retry: append prior assistant output + verifier feedback
            prior = self._last_output.get(task_id, "")
            self._history[task_id].append({"role": "assistant", "content": prior})
            self._history[task_id].append({"role": "user", "content": feedback})

        messages = self._history[task_id]
        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                model=self._model,
                max_tokens=8192,
                messages=messages,
            )
        except Exception as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "api_error", "error": str(exc)},
            )
            return

        content = response.content[0].text if response.content else ""
        if content:
            self._last_output[task_id] = content
            yield WorkerEvent(type="attempt.completed", payload={"summary": content})
        else:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "Claude returned empty content"},
            )

    def clear_history(self, task_id: str) -> None:
        """Clear conversation history for a task after it reaches terminal state."""
        self._history.pop(task_id, None)
        self._last_output.pop(task_id, None)

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id=self.id, healthy=True)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/orchestrator_v2/test_claude_worker.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run full test suite to check no regressions**

```bash
python -m pytest tests/orchestrator_v2/ --tb=short -q 2>&1 | tail -10
```

Expected: same pass count as before (existing tests may need signature update — fix inline if any fail).

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/workers/claude.py tests/orchestrator_v2/test_claude_worker.py
git commit -m "feat(workers): ClaudeWorker stateful history, configurable model/id/caps, sonnet+opus support"
```

---

### Task 5: OllamaWorker — add feedback param and history

**Files:**
- Modify: `backend/orchestrator/workers/ollama.py`
- Modify: `tests/orchestrator_v2/test_ollama_worker.py`

- [ ] **Step 1: Read the current test file to understand what needs updating**

Run:
```bash
grep -n "async def execute\|def execute\|feedback" tests/orchestrator_v2/test_ollama_worker.py
```

- [ ] **Step 2: Update ollama.py**

```python
# backend/orchestrator/workers/ollama.py
from __future__ import annotations
from typing import AsyncGenerator

import httpx

from ..domain.models import Task, TaskAttempt
from .base import WorkerAdapter, WorkerEvent, WorkerHealth, _build_prompt


class OllamaWorker(WorkerAdapter):
    """Worker backed by a local Ollama instance via /api/chat.

    Maintains per-task conversation history to support feedback injection on retries.
    """

    def __init__(self, model: str = "qwen3:8b", base_url: str = "http://127.0.0.1:11434") -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._history: dict[str, list[dict[str, str]]] = {}
        self._last_output: dict[str, str] = {}

    @property
    def id(self) -> str:
        return f"ollama:{self._model}"

    @property
    def capabilities(self) -> list[str]:
        return ["file_editing", "general", "cheap_repetitive"]

    async def execute(
        self,
        attempt: TaskAttempt,
        task: Task,
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]:
        task_id = task.id

        if task_id not in self._history or feedback is None:
            self._history[task_id] = [{"role": "user", "content": _build_prompt(task)}]
        else:
            prior = self._last_output.get(task_id, "")
            self._history[task_id].append({"role": "assistant", "content": prior})
            self._history[task_id].append({"role": "user", "content": feedback})

        payload = {
            "model": self._model,
            "messages": self._history[task_id],
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=600.0) as client:
                resp = await client.post("/api/chat", json=payload)
                resp.raise_for_status()
        except httpx.TimeoutException as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "timeout", "error": f"Ollama inference timed out: {exc}"},
            )
            return
        except httpx.HTTPError as exc:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "http_error", "error": str(exc)},
            )
            return

        content = resp.json().get("message", {}).get("content", "")
        if content:
            self._last_output[task_id] = content
            yield WorkerEvent(type="attempt.completed", payload={"summary": content})
        else:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "empty_response", "error": "Ollama returned empty content"},
            )

    def clear_history(self, task_id: str) -> None:
        self._history.pop(task_id, None)
        self._last_output.pop(task_id, None)

    async def cancel(self, attempt_id: str) -> None:
        pass

    async def health(self) -> WorkerHealth:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=5.0) as client:
                resp = await client.get("/")
                resp.raise_for_status()
            return WorkerHealth(worker_id=self.id, healthy=True)
        except httpx.HTTPError as exc:
            return WorkerHealth(worker_id=self.id, healthy=False, detail=str(exc))
```

- [ ] **Step 3: Run ollama worker tests**

```bash
python -m pytest tests/orchestrator_v2/test_ollama_worker.py -v --tb=short
```

Expected: all pass. If any test calls `execute(attempt, task)` without feedback it still works (default None).

- [ ] **Step 4: Commit**

```bash
git add backend/orchestrator/workers/ollama.py
git commit -m "feat(workers): OllamaWorker stateful history and feedback param"
```

---

### Task 6: Executor — replace verify_done_criteria with Verifier + soft retry loop

**Files:**
- Modify: `backend/orchestrator/service/executor.py`
- Modify: `tests/orchestrator_v2/test_executor.py`

- [ ] **Step 1: Add verifier-driven tests to test_executor.py**

Append to the end of `tests/orchestrator_v2/test_executor.py`:

```python
# ── imports needed for verifier tests ────────────────────────────────────────
from unittest.mock import AsyncMock, MagicMock
from backend.orchestrator.verifier.verifier import Verifier, VerificationResult


def _make_verifier(action: str, score: int = 9, feedback: str = "") -> Verifier:
    """Return a Verifier that always returns the given action."""
    result = VerificationResult(score=score, passed=(action == "pass"), feedback=feedback, action=action)
    v = MagicMock(spec=Verifier)
    v.verify = AsyncMock(return_value=result)
    return v


# ── update MockWorker to accept feedback param ────────────────────────────────
# NOTE: The existing MockWorker in this file needs its execute() signature updated.
# Find the MockWorker class and change:
#   async def execute(self, attempt: TaskAttempt, task: Task) -> AsyncIterator[WorkerEvent]:
# to:
#   async def execute(self, attempt: TaskAttempt, task: Task, feedback: str | None = None) -> AsyncIterator[WorkerEvent]:
# (Just add the feedback param — the body is unchanged.)


# ── verifier-driven tests ─────────────────────────────────────────────────────

async def test_executor_passes_on_score_8(store):
    worker = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "good output"}),
    ])
    reg = _reg(worker)
    _, task_id = await _setup(store)
    verifier = _make_verifier("pass", score=9)

    await run_task(task_id, store, reg, verifier)

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
    assert verifier.verify.call_count == 1


async def test_executor_soft_retry_on_score_5(store):
    """score=5 → soft retry → second attempt passes."""
    call_count = [0]

    class RetryThenPassWorker(WorkerAdapter):
        @property
        def id(self): return "extension"
        @property
        def capabilities(self): return ["file_editing"]
        async def execute(self, attempt, task, feedback=None):
            call_count[0] += 1
            yield WorkerEvent("attempt.completed", {"summary": f"output {call_count[0]}"})
        async def cancel(self, attempt_id): pass
        async def health(self): return WorkerHealth(worker_id=self.id, healthy=True)

    # First verify: retry. Second verify: pass.
    retry_result = VerificationResult(score=5, passed=False, feedback="needs more detail", action="retry")
    pass_result = VerificationResult(score=9, passed=True, feedback="", action="pass")
    verifier = MagicMock(spec=Verifier)
    verifier.verify = AsyncMock(side_effect=[retry_result, pass_result])

    reg = _reg(RetryThenPassWorker())
    _, task_id = await _setup(store)
    await run_task(task_id, store, reg, verifier)

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
    assert call_count[0] == 2  # worker called twice (original + 1 retry)
    assert verifier.verify.call_count == 2


async def test_executor_escalates_immediately_on_score_2(store):
    """score=2 → skip soft retry, escalate immediately to next worker."""
    extension = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "wrong output"}),
    ])
    claude = MockWorker("claude:sonnet", ["file_editing", "deep_reasoning"], [
        WorkerEvent("attempt.completed", {"summary": "correct output"}),
    ])
    reg = _reg(extension, claude)
    _, task_id = await _setup(store)

    escalate_result = VerificationResult(score=2, passed=False, feedback="wrong direction", action="escalate")
    pass_result = VerificationResult(score=9, passed=True, feedback="", action="pass")
    verifier = MagicMock(spec=Verifier)
    verifier.verify = AsyncMock(side_effect=[escalate_result, pass_result])

    await run_task(task_id, store, reg, verifier)

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
    assert extension.execute_called == 1  # no retry on extension
    assert claude.execute_called == 1


async def test_executor_respects_max_soft_retries(store):
    """After MAX_SOFT_RETRIES=2 soft retries, escalate even if score is in retry range."""
    call_count = [0]

    class AlwaysRetryWorker(WorkerAdapter):
        @property
        def id(self): return "extension"
        @property
        def capabilities(self): return ["file_editing"]
        async def execute(self, attempt, task, feedback=None):
            call_count[0] += 1
            yield WorkerEvent("attempt.completed", {"summary": "still not good enough"})
        async def cancel(self, attempt_id): pass
        async def health(self): return WorkerHealth(worker_id=self.id, healthy=True)

    # All verifications return retry (score 5) → after 2 retries, escalate
    retry_result = VerificationResult(score=5, passed=False, feedback="still missing X", action="retry")
    # 3 calls: original + 2 retries → all return retry → then hard escalate → no more workers → block
    verifier = MagicMock(spec=Verifier)
    verifier.verify = AsyncMock(return_value=retry_result)

    reg = _reg(AlwaysRetryWorker())  # only one worker, so escalation leads to block
    _, task_id = await _setup(store)
    await run_task(task_id, store, reg, verifier)

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.blocked
    assert call_count[0] == 3  # original + 2 retries


async def test_executor_verifier_error_escalates(store):
    """VerifierError is treated as escalate — never silently passes bad output."""
    from backend.orchestrator.verifier.verifier import VerifierError
    extension = MockWorker("extension", ["file_editing"], [
        WorkerEvent("attempt.completed", {"summary": "output"}),
    ])
    claude = MockWorker("claude:sonnet", ["file_editing", "deep_reasoning"], [
        WorkerEvent("attempt.completed", {"summary": "better output"}),
    ])
    reg = _reg(extension, claude)
    _, task_id = await _setup(store)

    verifier = MagicMock(spec=Verifier)
    verifier.verify = AsyncMock(side_effect=[
        VerifierError("haiku down"),
        VerificationResult(score=9, passed=True, feedback="", action="pass"),
    ])

    await run_task(task_id, store, reg, verifier)

    task = await store.tasks.get(task_id)
    assert task.status == TaskStatus.completed
```

- [ ] **Step 2: Also update MockWorker.execute() signature in test_executor.py**

Find the `MockWorker` class in `tests/orchestrator_v2/test_executor.py` and update the execute method signature:

```python
async def execute(self, attempt: TaskAttempt, task: Task, feedback: str | None = None) -> AsyncIterator[WorkerEvent]:
    self.execute_called += 1
    for ev in self._events:
        yield ev
```

Also update `CapturingWorker.execute()` the same way:

```python
async def execute(self, attempt: TaskAttempt, task: Task, feedback: str | None = None) -> AsyncIterator[WorkerEvent]:
    self.received_tasks.append(task)
    for ev in self._events:
        yield ev
```

- [ ] **Step 3: Update all existing `run_task()` calls in test_executor.py**

All existing `await run_task(task_id, store, reg)` calls need a `verifier` argument. Use a passing verifier:

```python
_PASS_VERIFIER = _make_verifier("pass")
```

Add this after `_make_verifier` is defined, then replace every `await run_task(task_id, store, reg)` with `await run_task(task_id, store, reg, _PASS_VERIFIER)` and every `await run_task(x, store, _reg(y))` with `await run_task(x, store, _reg(y), _PASS_VERIFIER)`.

Also update the two-task pair tests (`test_executor_injects_upstream_output_into_dependent_task`, `test_executor_no_upstream_leaves_context_refs_unchanged`, `test_executor_unlocks_downstream_on_completion`) similarly.

- [ ] **Step 4: Run tests to confirm they fail for the right reason**

```bash
python -m pytest tests/orchestrator_v2/test_executor.py -v --tb=short 2>&1 | tail -20
```

Expected: failures because `run_task` doesn't accept `verifier` param yet.

- [ ] **Step 5: Rewrite executor.py**

```python
# backend/orchestrator/service/executor.py
"""Lobster-style deterministic executor for driving tasks through their lifecycle."""
from __future__ import annotations
import dataclasses

from ..domain import events as ev_types
from ..domain import dependencies
from ..domain.models import Artifact, Task, TaskAttempt, TaskStatus, AttemptStatus
from ..domain.transitions import transition_task
from ..store.base import Store
from ..verifier.verifier import Verifier, VerifierError
from ..verifier.config import MAX_SOFT_RETRIES
from ..workers.base import WorkerEvent
from ..workers.registry import WorkerRegistry
from ..routing.router import assign_worker, NoCapableWorker
from ..routing.escalation import should_escalate
from . import approvals

_TERMINAL = frozenset({"attempt.completed", "attempt.failed", "attempt.blocked"})


async def run_task(
    task_id: str,
    store: Store,
    registry: WorkerRegistry,
    verifier: Verifier,
) -> None:
    """Drive one task from ready → terminal using a Lobster-style deterministic loop.

    Steps per attempt: assign → dispatch → stream → verify → soft-retry/escalate/complete/block
    """
    task = await store.tasks.get(task_id)
    if task is None:
        raise ValueError(f"Task {task_id!r} not found")

    attempted: set[str] = set()
    soft_retry_count: dict[str, int] = {}
    _retry_worker_id: str | None = None   # set on soft retry to force same worker
    _retry_feedback: str | None = None    # verifier feedback to inject on retry

    while True:
        # ── ASSIGN ──────────────────────────────────────────────────────────
        if _retry_worker_id:
            worker_id = _retry_worker_id
        else:
            try:
                worker_id = assign_worker(task, registry, exclude=attempted)
            except NoCapableWorker:
                if task.status == TaskStatus.ready:
                    task = transition_task(task, TaskStatus.in_progress)
                    await store.tasks.update_status(task.id, task.status)
                task = transition_task(task, TaskStatus.blocked)
                await store.tasks.update_status(task.id, task.status)
                await store.events.append(
                    ev_types.make_event(task.run_id, ev_types.TASK_BLOCKED, task_id=task.id)
                )
                await approvals.request_approval(task.run_id, task.id, "", store)
                return

        attempt = TaskAttempt.new(task_id=task.id, worker_id=worker_id)
        await store.tasks.save_attempt(attempt)

        if task.status != TaskStatus.in_progress:
            task = transition_task(task, TaskStatus.in_progress)
            await store.tasks.update_status(task.id, task.status)
        await store.events.append(
            ev_types.make_event(
                task.run_id, ev_types.ATTEMPT_ASSIGNED,
                task_id=task.id, attempt_id=attempt.id,
                payload={"worker_id": worker_id},
            )
        )

        # ── DISPATCH ────────────────────────────────────────────────────────
        upstream = await _collect_upstream_outputs(task, store)
        dispatch_task = dataclasses.replace(task, context_refs=task.context_refs + upstream) if upstream else task

        worker = registry.get(worker_id)
        await store.tasks.update_attempt_status(attempt.id, AttemptStatus.running)
        await store.events.append(
            ev_types.make_event(
                task.run_id, ev_types.ATTEMPT_STARTED,
                task_id=task.id, attempt_id=attempt.id,
            )
        )

        # ── STREAM ──────────────────────────────────────────────────────────
        outcome: WorkerEvent | None = None
        async for w_ev in worker.execute(attempt, dispatch_task, feedback=_retry_feedback):
            if w_ev.type in _TERMINAL:
                outcome = w_ev
                break
            if w_ev.type in ev_types.ALL_EVENT_TYPES:
                await store.events.append(
                    ev_types.make_event(
                        task.run_id, w_ev.type,
                        payload=w_ev.payload,
                        task_id=task.id, attempt_id=attempt.id,
                    )
                )

        # Reset retry state after consuming it
        _retry_feedback = None
        _retry_worker_id = None

        if outcome is None:
            outcome = WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "stream_ended", "error": "worker stream ended without terminal event"},
            )

        # ── VERIFY ──────────────────────────────────────────────────────────
        if outcome.type == "attempt.completed":
            summary = outcome.payload.get("summary", "")

            try:
                result = await verifier.verify(task, summary)
                result_action = result.action
                result_feedback = result.feedback
            except VerifierError:
                # Treat verifier failure as escalation — never silently pass bad output
                result_action = "escalate"
                result_feedback = "verifier error — escalating to next worker"

            if result_action == "pass":
                await store.tasks.update_attempt_result(
                    attempt.id, AttemptStatus.completed, summary=summary,
                )
                task = transition_task(task, TaskStatus.completed)
                await store.tasks.update_status(task.id, task.status)
                await store.artifacts.save(Artifact.new(
                    run_id=task.run_id, task_id=task.id, attempt_id=attempt.id,
                    type="text_output", location={"content": summary},
                ))
                await store.events.append(
                    ev_types.make_event(task.run_id, ev_types.TASK_COMPLETED, task_id=task.id)
                )
                worker.clear_history(task.id)
                await _unlock_downstream(task, store)
                return

            if result_action == "retry" and soft_retry_count.get(worker_id, 0) < MAX_SOFT_RETRIES:
                await store.tasks.update_attempt_result(
                    attempt.id, AttemptStatus.failed,
                    summary="", error_code="verification_retry",
                    blocking_reason=result_feedback,
                )
                soft_retry_count[worker_id] = soft_retry_count.get(worker_id, 0) + 1
                _retry_worker_id = worker_id
                _retry_feedback = result_feedback
                continue  # loop back — same worker, feedback injected via history

            # Verification failed (score 0-3 or retries exhausted) → treat as attempt.failed
            worker.clear_history(task.id)
            soft_retry_count = {}
            outcome = WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "verification_failed", "error": result_feedback},
            )

        # ── ESCALATE or BLOCK ────────────────────────────────────────────────
        error_code = outcome.payload.get("error_code", "")
        blocking_reason = outcome.payload.get("error", outcome.payload.get("reason", ""))

        if outcome.type == "attempt.blocked":
            await store.tasks.update_attempt_result(
                attempt.id, AttemptStatus.blocked,
                summary="", error_code=error_code, blocking_reason=blocking_reason,
            )
            task = transition_task(task, TaskStatus.blocked)
            await store.tasks.update_status(task.id, task.status)
            await store.events.append(
                ev_types.make_event(
                    task.run_id, ev_types.TASK_BLOCKED,
                    task_id=task.id, attempt_id=attempt.id,
                )
            )
            await approvals.request_approval(task.run_id, task.id, attempt.id, store)
            return

        escalating = should_escalate(task, registry, attempted)
        final_attempt_status = AttemptStatus.escalated if escalating else AttemptStatus.failed
        await store.tasks.update_attempt_result(
            attempt.id, final_attempt_status,
            summary="", error_code=error_code, blocking_reason=blocking_reason,
        )

        if escalating:
            attempted.add(worker_id)
            await store.tasks.increment_escalation(task.id)
            task = await store.tasks.get(task.id)
            await store.events.append(
                ev_types.make_event(
                    task.run_id, ev_types.ATTEMPT_ESCALATED,
                    task_id=task.id, attempt_id=attempt.id,
                )
            )
            continue

        task = transition_task(task, TaskStatus.blocked)
        await store.tasks.update_status(task.id, task.status)
        await store.events.append(
            ev_types.make_event(task.run_id, ev_types.TASK_BLOCKED, task_id=task.id)
        )
        await approvals.request_approval(task.run_id, task.id, attempt.id, store)
        return


async def _collect_upstream_outputs(task: Task, store: Store) -> list[str]:
    results = []
    for dep in task.dependencies:
        for artifact in await store.artifacts.list_by_task(dep.task_id):
            if artifact.type == "text_output":
                content = artifact.location.get("content", "")
                if content:
                    results.append(content)
    return results


async def _unlock_downstream(completed_task: Task, store: Store) -> None:
    all_tasks = await store.tasks.list_by_run(completed_task.run_id)
    artifacts = await store.artifacts.list_by_run(completed_task.run_id)
    artifact_task_ids = {a.task_id for a in artifacts}
    approval_events = await store.events.list_by_type(completed_task.run_id, ev_types.APPROVAL_GRANTED)
    approval_task_ids = {e.task_id for e in approval_events if e.task_id}

    newly_ready = dependencies.check_ready(all_tasks, artifact_task_ids, approval_task_ids)
    for task in newly_ready:
        task = transition_task(task, TaskStatus.ready)
        await store.tasks.update_status(task.id, task.status)
        await store.events.append(
            ev_types.make_event(task.run_id, ev_types.TASK_READY, task_id=task.id)
        )
```

- [ ] **Step 6: Run executor tests**

```bash
python -m pytest tests/orchestrator_v2/test_executor.py -v --tb=short 2>&1 | tail -30
```

Expected: all tests pass including new verifier-driven tests.

- [ ] **Step 7: Run full suite**

```bash
python -m pytest tests/orchestrator_v2/ --tb=short -q 2>&1 | tail -10
```

Expected: same number of passes as before + new tests added.

- [ ] **Step 8: Commit**

```bash
git add backend/orchestrator/service/executor.py tests/orchestrator_v2/test_executor.py
git commit -m "feat(executor): replace verify_done_criteria with Verifier; add soft retry loop"
```

---

### Task 7: App wiring — Verifier singleton, run_executor update, worker registration

**Files:**
- Modify: `backend/orchestrator/service/run_executor.py`
- Modify: `backend/orchestrator/service/app.py`

- [ ] **Step 1: Update run_executor.py to pass verifier through**

Read the current `run_executor.py` first, then add `verifier: Verifier` to `run_run()` and thread it through to `run_task()` calls:

```python
# In run_executor.py: find the run_run() function signature and update it.
# Current:
#   async def run_run(run_id: str, store: Store, registry: WorkerRegistry) -> None:
# New:
#   async def run_run(run_id: str, store: Store, registry: WorkerRegistry, verifier: "Verifier") -> None:
#
# And update the internal run_task() calls:
#   await asyncio.gather(*[run_task(t.id, store, registry) for t in ready])
# to:
#   await asyncio.gather(*[run_task(t.id, store, registry, verifier) for t in ready])
#
# Add import at top:
#   from ..verifier.verifier import Verifier
```

- [ ] **Step 2: Update app.py**

```python
# backend/orchestrator/service/app.py
from __future__ import annotations
import os
from contextlib import asynccontextmanager
from typing import Annotated

import anthropic
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from pydantic import BaseModel

from ..domain.models import Mission, Plan, Run, RunMode, RunStatus, TaskStatus
from ..domain.transitions import IllegalTransition
from ..store.base import Store
from ..verifier.verifier import Verifier
from ..workers.claude import ClaudeWorker
from ..workers.extension import ExtensionWorker
from ..workers.registry import WorkerRegistry
from .approvals import grant_approval, reject_approval
from .executor import run_task as _run_task
from ..workers.ollama import OllamaWorker
from .run_executor import run_run as _run_run
from ..planning.planner import generate_tasks, OllamaUnavailable, PlannerError

# ── singletons (replaced via dependency_overrides in tests) ──────────────────

_store: Store | None = None
_registry: WorkerRegistry | None = None
_verifier: Verifier | None = None


def get_store() -> Store:
    assert _store is not None, "Store not initialised"
    return _store


def get_registry() -> WorkerRegistry:
    assert _registry is not None, "Registry not initialised"
    return _registry


def get_verifier() -> Verifier:
    assert _verifier is not None, "Verifier not initialised"
    return _verifier


StoreDep = Annotated[Store, Depends(get_store)]
RegistryDep = Annotated[WorkerRegistry, Depends(get_registry)]
VerifierDep = Annotated[Verifier, Depends(get_verifier)]


# ── lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store, _registry, _verifier
    _store = await Store.connect()
    _registry = WorkerRegistry()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        _registry.register(ClaudeWorker(api_key=api_key))  # claude:sonnet
        _registry.register(ClaudeWorker(
            api_key=api_key,
            model="claude-opus-4-6",
            worker_id="claude:opus",
            capabilities=["complex_reasoning", "deep_reasoning", "general"],
        ))
        _verifier = Verifier(anthropic.Anthropic(api_key=api_key))
    else:
        # No API key: use a pass-through verifier that always passes
        # (allows running without Anthropic key for local Ollama-only mode)
        from ..verifier.verifier import VerificationResult

        class _PassthroughVerifier(Verifier):
            def __init__(self):
                pass
            async def verify(self, task, output):
                return VerificationResult(score=10, passed=True, feedback="", action="pass")

        _verifier = _PassthroughVerifier()

    _registry.register(ExtensionWorker(
        base_url=os.getenv("EXTENSION_URL", "http://localhost:3000")
    ))
    _registry.register(OllamaWorker(
        model=os.getenv("OLLAMA_MODEL", "qwen3:8b"),
        base_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
    ))

    for orphan in await _store.tasks.list_by_status(TaskStatus.in_progress):
        await _store.tasks.update_status(orphan.id, TaskStatus.failed)

    yield
    await _store.close()


app = FastAPI(title="Orchestrator v2", lifespan=lifespan)
```

- [ ] **Step 3: Update all `_run_task` and `_run_run` calls in app.py to pass `_verifier`**

Find every call like:
```python
background_tasks.add_task(_run_task, task_id, store, registry)
```
and update to:
```python
background_tasks.add_task(_run_task, task_id, store, registry, _verifier)
```

And:
```python
background_tasks.add_task(_run_run, run_id, store, registry)
```
→
```python
background_tasks.add_task(_run_run, run_id, store, registry, _verifier)
```

- [ ] **Step 4: Run the app tests**

```bash
python -m pytest tests/orchestrator_v2/test_app.py tests/orchestrator_v2/test_app_runs.py -v --tb=short 2>&1 | tail -20
```

Fix any failures from the signature change (app tests likely use `dependency_overrides` — they may need `_verifier` patched in too).

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/orchestrator_v2/ --tb=short -q 2>&1 | tail -10
```

Expected: all 293+ tests pass.

- [ ] **Step 6: Final commit**

```bash
git add backend/orchestrator/service/app.py backend/orchestrator/service/run_executor.py
git commit -m "feat(app): wire Verifier singleton, register claude:sonnet + claude:opus workers"
```

---

### Task 8: Full suite green check + memory update

- [ ] **Step 1: Run all tests**

```bash
python -m pytest tests/orchestrator_v2/ tests/orchestrator/ -v --tb=short -q 2>&1 | tail -15
```

Expected: all tests pass. Count should be ≥ 293 + new tests added in this plan.

- [ ] **Step 2: Push branch**

```bash
git push origin feat/orchestrator-domain-store
```
