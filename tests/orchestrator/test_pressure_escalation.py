# tests/orchestrator/test_pressure_escalation.py
import pytest
from backend.orchestrator_svc.models import Task
from backend.orchestrator_svc.routing import should_escalate


def test_should_escalate_when_extension_and_count_zero():
    """Extension task, count=0, status still 'running' (stale) — must escalate."""
    task = Task.new(title="T", goal="add test for login", task_type="code")
    task.assigned_worker = "extension"
    task.escalation_count = 0
    task.status = "running"  # stale value — the real bug triggers here
    assert should_escalate(task) is True


def test_should_escalate_false_when_already_claude():
    task = Task.new(title="T", goal="plan refactor", task_type="plan")
    task.assigned_worker = "claude"
    task.escalation_count = 0
    task.status = "running"
    assert should_escalate(task) is False


def test_should_escalate_false_when_cap_reached():
    task = Task.new(title="T", goal="add test", task_type="code")
    task.assigned_worker = "extension"
    task.escalation_count = 2
    task.status = "running"
    assert should_escalate(task) is False


def test_should_escalate_true_when_count_1_under_cap():
    """escalation_count=1 — still under cap of 2, should escalate again."""
    task = Task.new(title="T", goal="add test", task_type="code")
    task.assigned_worker = "extension"
    task.escalation_count = 1
    task.status = "running"
    assert should_escalate(task) is True
