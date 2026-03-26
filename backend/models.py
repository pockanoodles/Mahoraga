from dataclasses import dataclass
from enum import Enum


FAST_WORKER = "qwen2.5-coder:7b"
SENIOR_WORKER = "qwen2.5-coder:14b"
PLANNER = "qwen3:14b"


class Complexity(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class TaskType(str, Enum):
    CODE = "code"
    DEBUG = "debug"
    REFACTOR = "refactor"
    PLAN = "plan"
    EXPLAIN = "explain"


@dataclass
class Classification:
    complexity: Complexity
    task_type: TaskType


def route(classification: Classification) -> str:
    """Route to the correct execution model based on complexity."""
    if classification.complexity == Complexity.SIMPLE:
        return FAST_WORKER
    # medium and complex both execute with the senior coder
    # (for complex: qwen3 plans, but 14b-coder does the actual coding)
    return SENIOR_WORKER


def escalate(model: str) -> str:
    """Return the next model tier up. PLANNER is the ceiling."""
    if model == FAST_WORKER:
        return SENIOR_WORKER
    if model == SENIOR_WORKER:
        return PLANNER
    return PLANNER
