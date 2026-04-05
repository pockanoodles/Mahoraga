"""Tests for the web channel adapter and FastAPI app."""
import pytest
import httpx
from httpx import AsyncClient, ASGITransport

from backend.orchestrator.channels.base import ChannelAdapter, ChannelMessage
from backend.orchestrator.channels.web import create_web_app


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _echo_handler(msg: ChannelMessage) -> str:
    return f"echo: {msg.text}"


@pytest.fixture
def web_app():
    return create_web_app(on_message=_echo_handler)


@pytest.fixture
def app_no_handler():
    return create_web_app(on_message=None)


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_web_chat_serves_index(web_app):
    """GET / returns 200 and includes 'Mahoraga' in the HTML body."""
    async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert "Mahoraga" in response.text


async def test_web_chat_post_message(web_app):
    """POST /chat returns 200 with SSE stream."""
    async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as client:
        response = await client.post("/chat", json={"message": "hello", "user_id": "test-user"})
    assert response.status_code == 200


async def test_web_chat_response_contains_echo(web_app):
    """POST /chat streams an echo of the user message."""
    async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as client:
        response = await client.post("/chat", json={"message": "hello", "user_id": "test-user"})
    assert response.status_code == 200
    assert "echo:" in response.text


async def test_web_chat_stream_ends_with_done(web_app):
    """SSE stream must end with a [DONE] sentinel."""
    async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as client:
        response = await client.post("/chat", json={"message": "ping", "user_id": "u1"})
    assert "[DONE]" in response.text


async def test_web_chat_no_handler(app_no_handler):
    """POST /chat without a handler returns 200 with fallback message."""
    async with AsyncClient(transport=ASGITransport(app=app_no_handler), base_url="http://test") as client:
        response = await client.post("/chat", json={"message": "hi", "user_id": "u2"})
    assert response.status_code == 200
    assert "no handler" in response.text


# ── ChannelMessage unit tests ─────────────────────────────────────────────────

def test_channel_message_new():
    msg = ChannelMessage.new(user_id="u1", channel="web", text="hello")
    assert msg.user_id == "u1"
    assert msg.channel == "web"
    assert msg.text == "hello"
    assert msg.id  # non-empty UUID
    assert msg.timestamp > 0
    assert msg.attachments == []


def test_channel_message_with_attachments():
    att = [{"type": "image", "data": "...", "filename": "pic.png"}]
    msg = ChannelMessage.new(user_id="u1", channel="web", text="see pic", attachments=att)
    assert msg.attachments == att


# ── Abstract interface ────────────────────────────────────────────────────────

def test_channel_adapter_is_abstract():
    """ChannelAdapter cannot be instantiated directly."""
    with pytest.raises(TypeError):
        ChannelAdapter()  # type: ignore[abstract]
