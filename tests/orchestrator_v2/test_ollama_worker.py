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


def _make_response(lines: list[str], status_code: int = 200):
    """A single mock stream response (one call to client.stream())."""
    async def fake_aiter_lines():
        for line in lines:
            yield line

    mock_response = MagicMock()
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    mock_response.status_code = status_code
    mock_response.aiter_lines = fake_aiter_lines
    return mock_response


def _client_returning(*responses):
    """AsyncClient mock whose .stream() yields each response on successive calls
    (or raises, if the item is an exception) — for exercising the retry loop."""
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.stream = MagicMock(side_effect=list(responses))
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

    completed = [e for e in events if e.type == "attempt.completed"]
    assert len(completed) == 1
    assert completed[0].payload["summary"] == "def fib(n): ..."


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
    # A persistent 5xx is transient → retried _MAX_TRANSIENT_RETRIES times, then
    # surfaces as http_error. asyncio.sleep is patched so the backoff is instant.
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    mock_client = _make_stream_mock([], status_code=500)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=mock_client), \
         patch("backend.orchestrator.workers.ollama.asyncio.sleep", new=AsyncMock()):
        events = [ev async for ev in worker.execute(_attempt(), _task())]

    assert events[0].type == "attempt.failed"
    assert events[0].payload["error_code"] == "http_error"
    assert mock_client.stream.call_count == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_execute_retries_transient_5xx_then_succeeds():
    """Cold-load 5xx on the first call, success on the retry — the Phase 4 flake."""
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    good = _make_response([json.dumps({"message": {"content": "def fib(n): ..."}, "done": True})])
    client = _client_returning(_make_response([], status_code=503), good)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=client), \
         patch("backend.orchestrator.workers.ollama.asyncio.sleep", new=AsyncMock()):
        events = [ev async for ev in worker.execute(_attempt(), _task())]

    completed = [e for e in events if e.type == "attempt.completed"]
    assert len(completed) == 1
    assert completed[0].payload["summary"] == "def fib(n): ..."
    assert client.stream.call_count == 2  # one retry


@pytest.mark.asyncio
async def test_execute_retries_read_error_then_succeeds():
    """A dropped stream (ReadError) mid-batch is retried, not surfaced as failure."""
    import httpx
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    good = _make_response([json.dumps({"message": {"content": "ok"}, "done": True})])
    client = _client_returning(httpx.ReadError("stream dropped"), good)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=client), \
         patch("backend.orchestrator.workers.ollama.asyncio.sleep", new=AsyncMock()):
        events = [ev async for ev in worker.execute(_attempt(), _task())]

    assert [e for e in events if e.type == "attempt.completed"]
    assert client.stream.call_count == 2


@pytest.mark.asyncio
async def test_execute_does_not_retry_4xx():
    """Client errors (4xx) are not transient — fail fast, no retry, no backoff."""
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    client = _client_returning(_make_response([], status_code=400))

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=client), \
         patch("backend.orchestrator.workers.ollama.asyncio.sleep", new=AsyncMock()):
        events = [ev async for ev in worker.execute(_attempt(), _task())]

    assert events[0].type == "attempt.failed"
    assert events[0].payload["error_code"] == "http_error"
    assert client.stream.call_count == 1  # no retry


@pytest.mark.asyncio
async def test_execute_does_not_retry_connect_error():
    """ConnectError = server down; fail fast so the signal isn't masked by backoff."""
    import httpx
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    client = _client_returning(httpx.ConnectError("refused"), httpx.ConnectError("refused"))

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=client), \
         patch("backend.orchestrator.workers.ollama.asyncio.sleep", new=AsyncMock()):
        events = [ev async for ev in worker.execute(_attempt(), _task())]

    assert events[0].payload["error_code"] == "ollama_unreachable"
    assert client.stream.call_count == 1  # no retry


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
async def test_execute_think_false_in_top_level_payload():
    """think:false must be a top-level key, not nested in options — Ollama ignores options.think."""
    worker = OllamaWorker(model="qwen3:4b-q4_K_M", worker_id="ollama:coder")
    lines = [json.dumps({"message": {"content": "def mean(arr): ..."}, "done": True})]

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
        [ev async for ev in worker.execute(_attempt(), _task())]

    assert captured_payload.get("think") is False, "think:false must be top-level, not in options"
    assert "think" not in captured_payload.get("options", {}), "think must not be nested in options"


@pytest.mark.asyncio
async def test_execute_skips_thinking_phase_chunks():
    """Thinking-mode chunks (content='') must be skipped; real content must be collected."""
    worker = OllamaWorker(model="qwen3:4b-q4_K_M", worker_id="ollama:fast")
    lines = [
        # Thinking phase — content is empty, thinking field is populated
        json.dumps({"message": {"content": "", "thinking": "Let me reason..."}, "done": False}),
        json.dumps({"message": {"content": "", "thinking": "Almost done..."}, "done": False}),
        # Response phase — real content, thinking field is empty string
        json.dumps({"message": {"content": "Paris", "thinking": ""}, "done": False}),
        json.dumps({"message": {"content": ".", "thinking": ""}, "done": True}),
    ]
    mock_client = _make_stream_mock(lines)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=mock_client):
        events = [ev async for ev in worker.execute(_attempt(), _task())]

    completed = [e for e in events if e.type == "attempt.completed"]
    assert len(completed) == 1
    assert completed[0].payload["summary"] == "Paris."


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


@pytest.mark.asyncio
async def test_metrics_include_prompt_tokens_from_done_chunk():
    """The done chunk's prompt_eval_count/_duration must land in the metrics
    payload — without it, the input side of the cost counterfactual reads 0."""
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    lines = [
        json.dumps({"message": {"content": "def fib(n): ..."}, "done": False}),
        json.dumps({
            "message": {"content": ""},
            "done": True,
            "eval_count": 120,
            "eval_duration": 2_000_000_000,          # 2s
            "prompt_eval_count": 350,
            "prompt_eval_duration": 500_000_000,     # 0.5s
        }),
    ]
    mock_client = _make_stream_mock(lines)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=mock_client):
        events = [ev async for ev in worker.execute(_attempt(), _task())]

    metrics = [e for e in events if e.type == "metrics"]
    assert len(metrics) == 1
    m = metrics[0].payload
    assert m["tokens"] == 120
    assert m["prompt_tokens"] == 350
    assert m["prompt_eval_rate"] == pytest.approx(700.0)  # 350 / 0.5s


@pytest.mark.asyncio
async def test_metrics_prompt_eval_rate_guards_zero_duration():
    worker = OllamaWorker(model="qwen2.5-coder:7b", worker_id="ollama:coder")
    lines = [
        json.dumps({
            "message": {"content": "answer"},
            "done": True,
            "eval_count": 10,
            "eval_duration": 1_000_000_000,
            "prompt_eval_count": 42,
            "prompt_eval_duration": 0,
        }),
    ]
    mock_client = _make_stream_mock(lines)

    with patch("backend.orchestrator.workers.ollama.httpx.AsyncClient", return_value=mock_client):
        events = [ev async for ev in worker.execute(_attempt(), _task())]

    m = [e for e in events if e.type == "metrics"][0].payload
    assert m["prompt_tokens"] == 42
    assert m["prompt_eval_rate"] == 0.0
