from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, TYPE_CHECKING

if TYPE_CHECKING:
    from ..domain.models import Task, TaskAttempt


@dataclass
class WorkerEvent:
    type: str
    payload: dict = field(default_factory=dict)


@dataclass
class WorkerHealth:
    worker_id: str
    healthy: bool
    detail: str = ""


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
    ) -> AsyncIterator[WorkerEvent]: ...

    @abstractmethod
    async def cancel(self, attempt_id: str) -> None: ...

    @abstractmethod
    async def health(self) -> WorkerHealth: ...
