import json
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.server import app


@pytest.fixture(autouse=True)
def clear_sessions():
    """Ensure _sessions is empty at the start of each test."""
    import backend.server as srv
    srv._sessions.clear()
    yield
    srv._sessions.clear()


client = TestClient(app)


async def _fake_run_accept(message, workspace, history):
    yield {"type": "model", "model": "qwen2.5-coder:7b"}
    yield {"type": "token", "content": "Hello"}
    yield {"type": "token", "content": " world"}
    yield {"type": "done"}


async def _fake_run_error(message, workspace, history):
    raise RuntimeError("ollama not available")
    yield  # make it a generator


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_status_returns_ok():
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "qwen2.5-coder:7b" in data["models"]


def test_chat_streams_sse_events():
    with patch("backend.server.orchestrate", _fake_run_accept):
        resp = client.post("/chat", json={"message": "hi", "workspace": "/tmp/ws"})

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    events = _parse_sse(resp.text)
    assert events[0] == {"type": "model", "model": "qwen2.5-coder:7b"}
    token_events = [e for e in events if e["type"] == "token"]
    assert "".join(e["content"] for e in token_events) == "Hello world"
    assert events[-1]["type"] == "done"


def test_chat_returns_error_event_on_exception():
    with patch("backend.server.orchestrate", _fake_run_error):
        resp = client.post("/chat", json={"message": "hi", "workspace": "/tmp/ws"})

    events = _parse_sse(resp.text)
    assert any(e["type"] == "error" for e in events)


def test_clear_removes_session_history():
    # First, do a chat to populate session history
    with patch("backend.server.orchestrate", _fake_run_accept):
        client.post("/chat", json={"message": "hello", "workspace": "/tmp/test_clear"})

    # Clear it
    resp = client.post("/clear", json={"message": "", "workspace": "/tmp/test_clear"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_chat_accumulates_history_across_requests():
    """Second request should include first message+response in history."""
    call_histories = []

    async def capture_history(message, workspace, history):
        call_histories.append(list(history))
        yield {"type": "token", "content": "ok"}
        yield {"type": "done"}

    with patch("backend.server.orchestrate", capture_history):
        client.post("/chat", json={"message": "first", "workspace": "/tmp/hist"})
        client.post("/chat", json={"message": "second", "workspace": "/tmp/hist"})

    # Second call's history should include first message and response
    assert len(call_histories[0]) == 0  # first call: empty history
    assert len(call_histories[1]) == 2  # second call: user + assistant from first
    assert call_histories[1][0]["content"] == "first"
