from __future__ import annotations
from .models import Task

_CLAUDE_TASK_TYPES = frozenset({"plan", "explain", "review"})

_CLAUDE_KEYWORDS = frozenset({
    "redesign", "architecture", "rethink", "migrate", "flaky",
    "subtle bug", "cross-file", "multi-file", "large refactor",
    "dangerous", "risky",
})

_EXTENSION_KEYWORDS = frozenset({
    "add test", "patch", "rename", "fix import", "update config",
    "small refactor", "bounded", "format", "lint", "update dependency",
    "add docstring",
})


def route(task: Task) -> str:
    """Return worker_id for a fresh unassigned task. Returns 'extension' or 'claude'."""
    if task.task_type in _CLAUDE_TASK_TYPES:
        return "claude"

    goal = task.goal.lower()

    if any(kw in goal for kw in _CLAUDE_KEYWORDS):
        return "claude"
    if any(kw in goal for kw in _EXTENSION_KEYWORDS):
        return "extension"

    return "extension" if task.task_type == "code" else "claude"


def should_escalate(task: Task) -> bool:
    """Return True if a failed task should be re-routed to Claude.

    NOTE: Callers must only invoke this on the failure path.
    Status is not checked — DB status is stale ('running') at call time.
    """
    if task.assigned_worker == "claude":
        return False
    if task.escalation_count >= 2:
        return False
    return True
