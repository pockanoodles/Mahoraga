# backend/orchestrator/workers/router.py
from __future__ import annotations
from ..domain.models import Task
from ..planning.classifier import TIER3_KEYWORDS  # re-exported for callers

_CODE_KEYWORDS = frozenset({
    "code", "function", "implement", "debug",
    "script", "class", "test", "fix", "bug", "api", "import",
    # File/code operations — specific enough to not catch essays or plans
    "create", "file", "edit", "modify", "generate", "refactor",
    "program", "method", "algorithm",
})

_PLANNING_KEYWORDS = frozenset({
    "plan", "outline", "break down", "breakdown", "strategy",
    "approach", "decompose", "organize",
})

_FAST_PHRASES = frozenset({"what is", "define", "how many", "what are", "who is"})


_DEFAULT_OLLAMA_ADAPTER = "ollama:qwen3-4b"


class TaskRouter:
    def route(self, task: Task, backend: str) -> str:
        """Return the worker_id for a task given the active backend.

        Falls back to the default Qwen3 adapter's sub-worker when the bandit
        isn't available. Multi-model routing happens through the adapter
        registry + gateway._resolve_worker_id, not here.

        Raises ValueError if backend is not "ollama".
        """
        if backend != "ollama":
            raise ValueError(f"TaskRouter only routes for ollama backend, got {backend!r}")

        text = f"{task.title} {task.goal}".lower()
        words = set(text.split())

        if any(kw in words for kw in _CODE_KEYWORDS):
            return f"{_DEFAULT_OLLAMA_ADAPTER}:coder"

        if any(kw in words for kw in _PLANNING_KEYWORDS) or "break down" in text:
            return f"{_DEFAULT_OLLAMA_ADAPTER}:planner"

        if len(words) <= 8 or any(phrase in text for phrase in _FAST_PHRASES):
            return f"{_DEFAULT_OLLAMA_ADAPTER}:fast"

        return f"{_DEFAULT_OLLAMA_ADAPTER}:general"
