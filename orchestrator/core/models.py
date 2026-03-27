from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Task:
    id: str
    title: str
    goal: str
    task_type: Literal["code", "debug", "refactor", "plan", "review", "explain"]
    priority: Literal["low", "normal", "high"] = "normal"
    status: Literal[
        "pending", "assigned", "running", "retrying", "escalated",
        "completed", "failed", "blocked"
    ] = "pending"
    parent_id: str | None = None
    assigned_worker: str | None = None
    context: dict = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    validator_profile: list[str] = field(default_factory=list)
    escalation_count: int = 0


@dataclass
class WorkerResult:
    task_id: str
    worker_id: str
    status: Literal["completed", "failed", "blocked", "cancelled"]
    summary: str
    artifacts: list[dict] = field(default_factory=list)
    validator_results: list[dict] = field(default_factory=list)


@dataclass
class Event:
    type: str
    task_id: str
    ts: float
    worker_id: str | None = None
    content: dict = field(default_factory=dict)
