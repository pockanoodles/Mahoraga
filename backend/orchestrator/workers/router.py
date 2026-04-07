# backend/orchestrator/workers/router.py
from __future__ import annotations
from ..domain.models import Task
from ..planning.classifier import TIER3_KEYWORDS  # re-exported for callers

_CODE_KEYWORDS = frozenset({
    "code", "function", "implement", "debug",
    "script", "class", "test", "fix", "bug", "api", "import",
    "program", "method", "algorithm",
})

_PLANNING_KEYWORDS = frozenset({
    "plan", "outline", "break down", "breakdown", "strategy",
    "approach", "decompose", "organize",
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
        words = set(text.split())

        # Code task first — takes priority over everything else
        if any(kw in words for kw in _CODE_KEYWORDS):
            return "ollama:coder"

        # Planning-type task — whole-word match only (also catches "break down" as phrase)
        if any(kw in words for kw in _PLANNING_KEYWORDS) or "break down" in text:
            return "ollama:planner"

        # Fast: short task or simple Q&A phrase
        if len(words) <= 8 or any(phrase in text for phrase in _FAST_PHRASES):
            return "ollama:fast"

        return "ollama:general"
