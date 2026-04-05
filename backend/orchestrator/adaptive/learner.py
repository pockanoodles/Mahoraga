from __future__ import annotations
import json
from typing import Any

import anthropic

_HAIKU_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = """\
You are an adaptive learning assistant. Analyze the conversation excerpt and extract any corrections, preferences, patterns, or style signals that should be remembered for future interactions.

Return a JSON array of objects. Each object must have:
- "category": one of "style", "tool_affinity", "preference", "pattern", "correction"
- "key": a short label for what was learned (e.g. "response_length", "preferred_tool", "correction_tone")
- "value": the learned value as a string
- "confidence": a float 0.0-1.0 representing how confident you are (corrections = 0.9, preferences = 0.8, style inferences = 0.6)

Return an empty array [] if this is a smooth interaction with nothing new to learn.
Return only valid JSON — no markdown, no explanation."""


class Learner:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic()

    async def analyze_interaction(
        self,
        user_message: str,
        assistant_response: str,
        existing_adaptations: list[Any],
    ) -> list[dict]:
        try:
            existing_summary = ""
            if existing_adaptations:
                lines = [
                    f"- {a.category.value}: {a.key} = {a.value}"
                    for a in existing_adaptations[:10]
                ]
                existing_summary = (
                    "\n\nAlready known adaptations:\n" + "\n".join(lines)
                )

            user_content = (
                f"User message:\n{user_message}\n\n"
                f"Assistant response:\n{assistant_response}"
                f"{existing_summary}"
            )

            message = self._client.messages.create(
                model=_HAIKU_MODEL,
                max_tokens=512,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )

            raw = message.content[0].text.strip()
            result = json.loads(raw)
            if not isinstance(result, list):
                return []
            return result
        except Exception:
            return []
