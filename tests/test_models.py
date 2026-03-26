import pytest
from backend.models import (
    Classification, Complexity, TaskType,
    route, escalate,
    FAST_WORKER, SENIOR_WORKER, PLANNER,
)


def test_route_simple_returns_fast_worker():
    c = Classification(complexity=Complexity.SIMPLE, task_type=TaskType.CODE)
    assert route(c) == FAST_WORKER


def test_route_medium_returns_senior_worker():
    c = Classification(complexity=Complexity.MEDIUM, task_type=TaskType.CODE)
    assert route(c) == SENIOR_WORKER


def test_route_complex_returns_senior_worker():
    c = Classification(complexity=Complexity.COMPLEX, task_type=TaskType.REFACTOR)
    assert route(c) == SENIOR_WORKER


def test_escalate_fast_to_senior():
    assert escalate(FAST_WORKER) == SENIOR_WORKER


def test_escalate_senior_to_planner():
    assert escalate(SENIOR_WORKER) == PLANNER


def test_escalate_planner_is_ceiling():
    assert escalate(PLANNER) == PLANNER
