import pytest
from backend.orchestrator.planning.validator import validate_raw_tasks, ValidationError


def test_valid_task_list_passes():
    tasks = [
        {"title": "Set up project", "goal": "Create directory structure", "dependencies": []},
        {"title": "Write code", "goal": "Implement the feature", "dependencies": ["Set up project"]},
    ]
    validate_raw_tasks(tasks)  # should not raise


def test_empty_title_raises():
    tasks = [{"title": "", "goal": "Do something", "dependencies": []}]
    with pytest.raises(ValidationError, match="title"):
        validate_raw_tasks(tasks)


def test_missing_title_raises():
    tasks = [{"goal": "Do something", "dependencies": []}]
    with pytest.raises(ValidationError, match="title"):
        validate_raw_tasks(tasks)


def test_empty_goal_raises():
    tasks = [{"title": "Do something", "goal": "", "dependencies": []}]
    with pytest.raises(ValidationError, match="goal"):
        validate_raw_tasks(tasks)


def test_missing_goal_raises():
    tasks = [{"title": "Do something", "dependencies": []}]
    with pytest.raises(ValidationError, match="goal"):
        validate_raw_tasks(tasks)


def test_unknown_dependency_raises():
    tasks = [
        {"title": "Task A", "goal": "Do A", "dependencies": ["Nonexistent Task"]},
    ]
    with pytest.raises(ValidationError, match="Nonexistent Task"):
        validate_raw_tasks(tasks)


def test_cycle_raises():
    tasks = [
        {"title": "Task A", "goal": "Do A", "dependencies": ["Task B"]},
        {"title": "Task B", "goal": "Do B", "dependencies": ["Task A"]},
    ]
    with pytest.raises(ValidationError, match="[Cc]ycle"):
        validate_raw_tasks(tasks)


def test_empty_task_list_passes():
    validate_raw_tasks([])  # degenerate case — planner returned nothing


def test_self_dependency_raises():
    tasks = [
        {"title": "Task A", "goal": "Do A", "dependencies": ["Task A"]},
    ]
    with pytest.raises(ValidationError, match="[Cc]ycle"):
        validate_raw_tasks(tasks)
