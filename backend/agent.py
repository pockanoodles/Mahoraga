import json
from typing import AsyncGenerator

import httpx

from .models import OLLAMA_URL, NUM_CTX, KEEP_ALIVE
from .prompts import CODER_SYSTEM
from .tools import TOOL_DEFINITIONS, dispatch


def _extract_tool_call(content: str):
    """Extract tool call JSON embedded in text output."""
    decoder = json.JSONDecoder()
    idx = 0
    _TOOL_NAMES = {t["function"]["name"] for t in TOOL_DEFINITIONS}
    while idx < len(content):
        start = content.find("{", idx)
        if start == -1:
            break
        try:
            obj, _ = decoder.raw_decode(content, start)
            if obj.get("name") in _TOOL_NAMES and "arguments" in obj:
                return {"function": {"name": obj["name"], "arguments": obj["arguments"]}}
        except json.JSONDecodeError:
            pass
        idx = start + 1
    return None


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
                    "keep_alive": KEEP_ALIVE,
                    "options": {"num_ctx": NUM_CTX},
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

        # Emit plan event if PLAN block detected in content
        joined = "".join(content_parts)
        if "PLAN" in joined[:1500] and "- Read:" in joined:
            plan_start = joined.find("PLAN")
            verify_idx = joined.find("- Verify:")
            if verify_idx != -1:
                end = joined.find("\n", verify_idx + len("- Verify:"))
                plan_text = joined[plan_start:end].strip() if end != -1 else joined[plan_start:].strip()
            else:
                plan_text = joined[plan_start:].strip()
            yield {"type": "plan", "content": plan_text}

        # Text-mode tool call fallback for models that embed JSON in content
        if not tool_calls:
            joined = "".join(content_parts)
            parsed = _extract_tool_call(joined)
            if parsed:
                tool_calls = [parsed]
                content_parts = []  # suppress raw JSON from being shown as a token

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
