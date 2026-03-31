from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, TYPE_CHECKING

if TYPE_CHECKING:
    from ..domain.models import Task, TaskAttempt


@dataclass
class WorkerEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerHealth:
    worker_id: str
    healthy: bool
    detail: str = ""


def _build_prompt(task: "Task") -> str:
    """Build a focused prompt from task fields. Selective context injection."""
    lines = [f"# Task: {task.title}", f"\n## Goal\n{task.goal}"]
    if task.context_refs:
        lines.append("\n## Context\n" + "\n".join(f"- {ref}" for ref in task.context_refs))
    if task.constraints:
        lines.append("\n## Constraints\n" + "\n".join(f"- {c}" for c in task.constraints))
    if task.done_criteria:
        lines.append(f"\n## Done Criteria\n{task.done_criteria}")
    return "\n".join(lines)


class WorkerAdapter(ABC):
    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> list[str]: ...

    @abstractmethod
    async def execute(
        self, attempt: "TaskAttempt", task: "Task"
    ) -> AsyncGenerator[WorkerEvent, None]: ...

    @abstractmethod
    async def cancel(self, attempt_id: str) -> None: ...

    @abstractmethod
    async def health(self) -> WorkerHealth: ...
