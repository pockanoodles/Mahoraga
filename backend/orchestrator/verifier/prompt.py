# backend/orchestrator/verifier/prompt.py
from __future__ import annotations
from ..domain.models import Task

SYSTEM_PROMPT = """\
You are a strict task evaluator. You receive a task goal, done criteria, and a worker's output.
Score the output 0-10 based on how well it satisfies the done criteria.

Scoring guide:
- 8-10: Output fully satisfies the done criteria
- 4-7: Output partially satisfies the done criteria but has notable gaps or errors
- 0-3: Output does not satisfy the done criteria or addresses the wrong problem

Respond with JSON only, no other text:
{"score": <integer 0-10>, "feedback": "<what is missing or wrong; empty string if score >= 8>"}
"""


def build_verify_message(task: Task, output: str) -> str:
    lines = [
        f"## Task Goal\n{task.goal}",
        f"## Done Criteria\n{task.done_criteria or '(none specified — score based on goal completion)'}",
        f"## Worker Output\n{output}",
    ]
    return "\n\n".join(lines)
