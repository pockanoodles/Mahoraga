from __future__ import annotations
import time
import uuid
from dataclasses import dataclass
from enum import Enum


class MissionStatus(str, Enum):
    active = "active"
    completed = "completed"
    cancelled = "cancelled"


class PlanStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    superseded = "superseded"


class RunMode(str, Enum):
    plan_first = "plan_first"
    direct = "direct"
    review_loop = "review_loop"


class RunStatus(str, Enum):
    active = "active"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class TaskStatus(str, Enum):
    pending = "pending"
    ready = "ready"
    in_progress = "in_progress"
    blocked = "blocked"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class AttemptStatus(str, Enum):
    assigned = "assigned"
    running = "running"
    completed = "completed"
    failed = "failed"
    blocked = "blocked"
    escalated = "escalated"
    cancelled = "cancelled"


class DependencyType(str, Enum):
    completion = "completion"
    artifact = "artifact"
    approval = "approval"


@dataclass
class Dependency:
    task_id: str
    type: DependencyType

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "type": self.type.value}

    @classmethod
    def from_dict(cls, d: dict) -> Dependency:
        return cls(task_id=d["task_id"], type=DependencyType(d["type"]))


@dataclass
class Mission:
    id: str
    title: str
    goal: str
    background: str
    success_condition: str
    context_refs: list[str]
    global_constraints: list[str]
    preferences: dict
    status: MissionStatus
    created_at: float
    updated_at: float

    @staticmethod
    def new(title: str, goal: str, background: str = "",
            success_condition: str = "", **kwargs) -> Mission:
        now = time.time()
        return Mission(
            id=str(uuid.uuid4()),
            title=title,
            goal=goal,
            background=background,
            success_condition=success_condition,
            context_refs=kwargs.get("context_refs", []),
            global_constraints=kwargs.get("global_constraints", []),
            preferences=kwargs.get("preferences", {}),
            status=MissionStatus.active,
            created_at=now,
            updated_at=now,
        )


@dataclass
class Plan:
    id: str
    mission_id: str
    version: int
    phases: list[str]
    worker_strategy: dict
    validation_strategy: dict
    task_graph_shape: str
    status: PlanStatus
    created_at: float

    @staticmethod
    def new(mission_id: str, **kwargs) -> Plan:
        return Plan(
            id=str(uuid.uuid4()),
            mission_id=mission_id,
            version=kwargs.get("version", 1),
            phases=kwargs.get("phases", []),
            worker_strategy=kwargs.get("worker_strategy", {}),
            validation_strategy=kwargs.get("validation_strategy", {}),
            task_graph_shape=kwargs.get("task_graph_shape", "linear"),
            status=PlanStatus.draft,
            created_at=time.time(),
        )


@dataclass
class Run:
    id: str
    mission_id: str
    plan_id: str
    mode: RunMode
    status: RunStatus
    created_at: float
    updated_at: float

    @staticmethod
    def new(mission_id: str, plan_id: str, mode: RunMode) -> Run:
        now = time.time()
        return Run(
            id=str(uuid.uuid4()),
            mission_id=mission_id,
            plan_id=plan_id,
            mode=mode,
            status=RunStatus.paused,  # Runs start paused; explicit orch run start required
            created_at=now,
            updated_at=now,
        )


@dataclass
class Task:
    id: str
    run_id: str
    parent_task_id: str | None
    title: str
    goal: str
    scope: list[str]
    context_refs: list[str]
    done_criteria: str
    dependencies: list[Dependency]
    constraints: list[str]
    preferred_worker_type: str | None
    required_capabilities: list[str]
    escalation_count: int
    status: TaskStatus
    created_at: float
    updated_at: float

    @staticmethod
    def new(run_id: str, title: str, goal: str, **kwargs) -> Task:
        now = time.time()
        return Task(
            id=str(uuid.uuid4()),
            run_id=run_id,
            parent_task_id=kwargs.get("parent_task_id"),
            title=title,
            goal=goal,
            scope=kwargs.get("scope", []),
            context_refs=kwargs.get("context_refs", []),
            done_criteria=kwargs.get("done_criteria", ""),
            dependencies=kwargs.get("dependencies", []),
            constraints=kwargs.get("constraints", []),
            preferred_worker_type=kwargs.get("preferred_worker_type"),
            required_capabilities=kwargs.get("required_capabilities", []),
            escalation_count=0,
            status=TaskStatus.pending,
            created_at=now,
            updated_at=now,
        )


@dataclass
class TaskAttempt:
    id: str
    task_id: str
    worker_id: str
    status: AttemptStatus
    error_code: str
    blocking_reason: str
    started_at: float | None
    ended_at: float | None
    summary: str
    artifact_refs: list[str]
    validator_refs: list[str]

    @staticmethod
    def new(task_id: str, worker_id: str) -> TaskAttempt:
        return TaskAttempt(
            id=str(uuid.uuid4()),
            task_id=task_id,
            worker_id=worker_id,
            status=AttemptStatus.assigned,
            error_code="",
            blocking_reason="",
            started_at=None,
            ended_at=None,
            summary="",
            artifact_refs=[],
            validator_refs=[],
        )


@dataclass
class Artifact:
    id: str
    run_id: str
    task_id: str
    attempt_id: str
    type: str
    location: dict
    created_at: float

    @staticmethod
    def new(run_id: str, task_id: str, attempt_id: str,
            type: str, location: dict) -> Artifact:
        return Artifact(
            id=str(uuid.uuid4()),
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            type=type,
            location=location,
            created_at=time.time(),
        )


@dataclass
class Event:
    id: str
    run_id: str
    task_id: str | None
    attempt_id: str | None
    type: str
    payload: dict
    ts: float

    @staticmethod
    def new(run_id: str, type: str, payload: dict | None = None,
            task_id: str | None = None,
            attempt_id: str | None = None) -> Event:
        return Event(
            id=str(uuid.uuid4()),
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            type=type,
            payload=payload or {},
            ts=time.time(),
        )
