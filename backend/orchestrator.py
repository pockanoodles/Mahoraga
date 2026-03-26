import json
from typing import AsyncGenerator

import httpx

from .models import Classification, Complexity, TaskType, PLANNER, route, escalate
from .agent import run_agent
from .prompts import CLASSIFIER_SYSTEM, VERIFIER_SYSTEM

OLLAMA_URL = "http://localhost:11434"


async def _call_json(model: str, system: str, user: str) -> dict:
    """Non-streaming Ollama call that returns parsed JSON from the response."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            },
        )
        content = resp.json()["message"]["content"].strip()
        # Strip markdown code fences if the model wraps JSON in them
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(content)


async def classify(message: str) -> Classification:
    data = await _call_json(PLANNER, CLASSIFIER_SYSTEM, message)
    return Classification(
        complexity=Complexity(data["complexity"]),
        task_type=TaskType(data["task_type"]),
    )


async def verify(message: str, response: str) -> dict:
    prompt = f"Task: {message}\n\nAgent response:\n{response}"
    return await _call_json(PLANNER, VERIFIER_SYSTEM, prompt)


async def run(
    message: str,
    workspace: str,
    history: list[dict],
) -> AsyncGenerator[dict, None]:
    """Full orchestrator pipeline. Yields SSE event dicts."""
    # 1. Classify
    classification = await classify(message)

    # 2. Route
    model = route(classification)
    yield {"type": "model", "model": model}

    messages = list(history) + [{"role": "user", "content": message}]

    for attempt in range(3):
        # 3. Execute
        response_parts = []
        async for event in run_agent(model, messages, workspace):
            if event["type"] == "token":
                response_parts.append(event["content"])
            yield event
            if event["type"] == "done":
                break

        full_response = "".join(response_parts)

        # 4. Skip verification for simple tasks
        if classification.complexity == Complexity.SIMPLE:
            return

        # 5. Verify
        verdict = await verify(message, full_response)
        if verdict["verdict"] == "ACCEPT":
            return

        # 6. Retry or escalate
        corrections = verdict.get("corrections", "revise your answer")
        if attempt < 2:
            messages.append({"role": "user", "content": f"Revise: {corrections}"})
        else:
            # Escalation: announce the new model to the client as a signal.
            # The caller (server) can issue a follow-up /chat request which
            # will re-classify and route with fresh context.
            new_model = escalate(model)
            if new_model != model:
                model = new_model
                yield {"type": "model", "model": model}
