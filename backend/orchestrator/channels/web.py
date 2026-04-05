from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .base import ChannelAdapter, ChannelMessage

# Resolve static dir: walk up from this file until we find the static/ directory
def _find_static_dir() -> Path:
    current = Path(__file__).resolve().parent
    for _ in range(10):
        candidate = current / "static"
        if candidate.is_dir():
            return candidate
        current = current.parent
    # Fallback to repo-root-relative guess
    return Path(__file__).resolve().parents[3] / "static"

_STATIC_DIR = _find_static_dir()


class _ChatRequest(BaseModel):
    message: str
    user_id: str = "web-user"


class WebChannel(ChannelAdapter):
    """Channel adapter for the built-in web chat UI."""

    def __init__(
        self,
        on_message: Callable[[ChannelMessage], Awaitable[str]] | None = None,
    ) -> None:
        self._on_message = on_message
        self._app: FastAPI | None = None

    @property
    def name(self) -> str:
        return "web"

    async def send(self, user_id: str, text: str) -> None:
        # For the web channel, responses are delivered inline via SSE.
        # This no-op exists to satisfy the interface; push-style delivery
        # can be added later via a websocket / SSE broadcast map.
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def get_app(self) -> FastAPI:
        if self._app is None:
            self._app = create_web_app(on_message=self._on_message)
        return self._app


def create_web_app(
    on_message: Callable[[ChannelMessage], Awaitable[str]] | None = None,
) -> FastAPI:
    """Factory that returns a fully-configured FastAPI app for the web channel."""

    app = FastAPI(title="Mahoraga Web Chat")

    @app.get("/", response_class=HTMLResponse)
    async def serve_index() -> HTMLResponse:
        index_path = _STATIC_DIR / "index.html"
        return HTMLResponse(content=index_path.read_text())

    @app.post("/chat")
    async def chat(request: _ChatRequest) -> StreamingResponse:
        msg = ChannelMessage.new(
            user_id=request.user_id,
            channel="web",
            text=request.message,
        )

        async def event_stream():
            if on_message is not None:
                try:
                    response_text = await on_message(msg)
                    # Stream word-by-word to simulate progressive delivery
                    for chunk in response_text.split(" "):
                        yield f"data: {chunk} \n\n"
                        await asyncio.sleep(0)
                except Exception as exc:
                    yield f"data: [ERROR] {exc}\n\n"
            else:
                yield "data: (no handler configured)\n\n"

            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # Mount static assets (CSS, JS)
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app
