# backend/orchestrator/workers/router.py
from __future__ import annotations
from ..domain.models import Task

_CODE_KEYWORDS = frozenset({
    "code", "function", "implement", "debug", "refactor",
    "script", "class", "test", "fix", "bug", "api", "import",
    "program", "method", "algorithm",
})

_PLANNING_KEYWORDS = frozenset({
    "plan", "outline", "break down", "breakdown", "strategy",
    "approach", "steps", "decompose", "structure", "organize",
})

_FAST_PHRASES = frozenset({"what is", "define", "how many", "what are", "who is"})


class TaskRouter:
    def route(self, task: Task, backend: str) -> str:
        """Return the worker_id for a task given the active backend.

        Raises ValueError if backend is not "ollama".
        """
        if backend != "ollama":
            raise ValueError(f"TaskRouter only routes for ollama backend, got {backend!r}")

        text = f"{task.title} {task.goal}".lower()
        words = text.split()

        # Planning-type task (checked first — takes priority)
        for kw in _PLANNING_KEYWORDS:
            if kw in text:
                return "ollama:planner"

        # Code task (checked before fast — keywords beat length heuristic)
        if any(kw in words for kw in _CODE_KEYWORDS):
            return "ollama:coder"

        # Fast: short task or simple Q&A phrase
        if len(words) <= 8 or any(phrase in text for phrase in _FAST_PHRASES):
            return "ollama:fast"

        return "ollama:general"
