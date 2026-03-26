import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .orchestrator import run as orchestrate

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Per-workspace conversation history: workspace_path → list of messages
_sessions: dict[str, list[dict]] = {}


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

    # Persist turn to session history
    if response_parts:
        history = list(_sessions.get(workspace, []))
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": "".join(response_parts)})
        _sessions[workspace] = history


@app.post("/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(
        _event_stream(req.message, req.workspace),
        media_type="text/event-stream",
    )


@app.get("/status")
async def status():
    return {
        "status": "ok",
        "models": ["qwen2.5-coder:7b", "qwen2.5-coder:14b", "qwen3:14b"],
    }


@app.post("/clear")
async def clear(req: ChatRequest):
    _sessions.pop(req.workspace, None)
    return {"status": "ok"}
