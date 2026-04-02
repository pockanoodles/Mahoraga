from __future__ import annotations
import asyncio
import json
from dataclasses import dataclass

import anthropic

from ..domain.models import Task
from .config import PASS_THRESHOLD, RETRY_THRESHOLD, VERIFIER_MODEL
from .prompt import SYSTEM_PROMPT, build_verify_message


class VerifierError(RuntimeError):
    """Raised when Haiku returns unparseable output or the API call fails."""


@dataclass
class VerificationResult:
    score: int       # 0-10
    passed: bool     # score >= PASS_THRESHOLD
    feedback: str    # populated when not passed
    action: str      # "pass" | "retry" | "escalate"

    @classmethod
    def from_score(cls, score: int, feedback: str) -> "VerificationResult":
        passed = score >= PASS_THRESHOLD
        if passed:
            action = "pass"
        elif score >= RETRY_THRESHOLD:
            action = "retry"
        else:
            action = "escalate"
        return cls(score=score, passed=passed, feedback=feedback, action=action)


class Verifier:
    def __init__(self, client: anthropic.Anthropic, model: str = VERIFIER_MODEL) -> None:
        self._client = client
        self._model = model

    async def verify(self, task: Task, output: str) -> VerificationResult:
        """Call Haiku to score worker output against task done_criteria.

        Raises VerifierError on API failure or unparseable response.
        """
        user_msg = build_verify_message(task, output)
        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                model=self._model,
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
        except Exception as exc:
            raise VerifierError(f"Haiku API call failed: {exc}") from exc

        raw = response.content[0].text if response.content else ""
        try:
            data = json.loads(raw)
            score = int(data["score"])
            feedback = data.get("feedback", "")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise VerifierError(
                f"Haiku returned unparseable output: {raw!r}"
            ) from exc

        return VerificationResult.from_score(score, feedback)
