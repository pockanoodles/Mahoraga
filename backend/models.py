from dataclasses import dataclass
from enum import Enum


FAST_WORKER = "qwen2.5-coder:7b"
SENIOR_WORKER = "qwen2.5-coder:14b"
PLANNER = "qwen3:14b"

NUM_CTX = 32768       # agent context window (workers)
CLASSIFIER_CTX = 4096  # classify + verify calls (shorter prompts)
KEEP_ALIVE = 300       # unload after 5min idle


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
    return SENIOR_WORKER


def escalate(model: str) -> str:
    """Return the next model tier up. PLANNER is the ceiling."""
    if model == FAST_WORKER:
        return SENIOR_WORKER
    if model == SENIOR_WORKER:
        return PLANNER
    return PLANNER


OLLAMA_URL = "http://localhost:11434"
