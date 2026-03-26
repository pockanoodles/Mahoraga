import json
from typing import AsyncGenerator

import httpx

from .models import OLLAMA_URL
from .prompts import CODER_SYSTEM
from .tools import TOOL_DEFINITIONS, dispatch


async def run_agent(
    model: str,
    messages: list[dict],
    workspace: str,
    max_iterations: int = 20,
) -> AsyncGenerator[dict, None]:
    """
    Run a single-model agent tool loop against Ollama.
    Yields SSE event dicts: token | tool_call | done
    """
    msgs = [{"role": "system", "content": CODER_SYSTEM}] + list(messages)

    for _ in range(max_iterations):
        tool_calls = []
        content_parts = []

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": model,
                    "messages": msgs,
                    "tools": TOOL_DEFINITIONS,
                    "stream": True,
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    msg = chunk.get("message", {})

                    if msg.get("content"):
                        content_parts.append(msg["content"])
                        yield {"type": "token", "content": msg["content"]}

                    if msg.get("tool_calls"):
                        tool_calls.extend(msg["tool_calls"])

        if not tool_calls:
            yield {"type": "done"}
            return

        # Add assistant turn (with tool calls) to history
        msgs.append({
            "role": "assistant",
            "content": "".join(content_parts),
            "tool_calls": tool_calls,
        })

        # Execute each tool and add results
        for tc in tool_calls:
            fn = tc["function"]
            args = fn.get("arguments", {})
            yield {"type": "tool_call", "tool": fn["name"], **args}
            result = dispatch(workspace, fn["name"], args)
            msgs.append({"role": "tool", "content": result})

    yield {"type": "done"}
