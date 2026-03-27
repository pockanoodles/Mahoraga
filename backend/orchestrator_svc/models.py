from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    title: str
    goal: str
    task_type: str   # code | debug | refactor | plan | explain | review
    status: str = "pending"  # pending | assigned | running | completed | failed | blocked | cancelled
    priority: str = "normal"  # low | normal | high
    parent_id: str | None = None
    assigned_worker: str | None = None
    context: dict = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    validator_profile: list[str] = field(default_factory=list)
    escalation_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @staticmethod
    def new(title: str, goal: str, task_type: str, **kwargs) -> Task:
        return Task(id=str(uuid.uuid4()), title=title, goal=goal, task_type=task_type, **kwargs)


@dataclass
class WorkerResult:
    task_id: str
    worker_id: str
    status: str  # completed | failed | blocked | cancelled
    summary: str
    artifacts: list[dict] = field(default_factory=list)
    validator_results: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass
class Event:
    type: str
    task_id: str
    content: dict = field(default_factory=dict)
    worker_id: str | None = None
    ts: float = field(default_factory=time.time)
