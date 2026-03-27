import json
import logging
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .models import FAST_WORKER, OLLAMA_URL, KEEP_ALIVE, CLASSIFIER_CTX
from .orchestrator import run as orchestrate

logger = logging.getLogger(__name__)
app = FastAPI()

_SESSION_FILE = Path.home() / ".ollama-runtime" / "session.json"
_start_time = datetime.now()

# Per-workspace conversation history: workspace_path → list of messages
_sessions: dict[str, list[dict]] = {}


def _load_sessions() -> None:
    if _SESSION_FILE.exists():
        try:
            _sessions.update(json.loads(_SESSION_FILE.read_text()))
            logger.info("loaded session from %s", _SESSION_FILE)
        except Exception as e:
            logger.warning("could not load session file: %s", e)


def _save_sessions() -> None:
    try:
        _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SESSION_FILE.write_text(json.dumps(_sessions))
    except Exception as e:
        logger.warning("could not save session: %s", e)


async def _warmup_model(client: httpx.AsyncClient, model: str, ctx: int) -> None:
    try:
        await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "keep_alive": KEEP_ALIVE,
                "options": {"num_ctx": ctx},
            },
            timeout=120,
        )
        logger.info("warmed up %s", model)
    except Exception as e:
        logger.warning("warmup failed for %s: %s", model, e)


@app.on_event("startup")
async def startup() -> None:
    _load_sessions()
    async with httpx.AsyncClient() as client:
        await _warmup_model(client, FAST_WORKER, CLASSIFIER_CTX)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    workspace: str


async def _event_stream(message: str, workspace: str):
    history = _sessions.get(workspace, [])
    response_parts = []

    try:
        async for event in orchestrate(message, workspace, history):
            if event["type"] == "token":
                response_parts.append(event["content"])
            yield f"data: {json.dumps(event)}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        return

    if response_parts:
        history = list(_sessions.get(workspace, []))
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": "".join(response_parts)})
        _sessions[workspace] = history
        _save_sessions()


@app.post("/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(
        _event_stream(req.message, req.workspace),
        media_type="text/event-stream",
    )


@app.get("/history")
async def history(workspace: str):
    return {"messages": _sessions.get(workspace, [])}


@app.get("/status")
async def status():
    return {"status": "ok", "models": [FAST_WORKER]}


@app.get("/health")
async def health():
    uptime = (datetime.now() - _start_time).total_seconds()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{OLLAMA_URL}/api/tags")
            models = [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        models = []
    return {"uptime": uptime, "models": models}


@app.post("/clear")
async def clear(req: ChatRequest):
    _sessions.pop(req.workspace, None)
    _save_sessions()
    return {"status": "ok"}
