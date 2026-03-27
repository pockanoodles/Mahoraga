from backend.orchestrator_svc.models import Task
from backend.orchestrator_svc.routing import route, should_escalate


def _task(
    goal: str,
    task_type: str = "code",
    escalation_count: int = 0,
    assigned_worker: str | None = None,
    status: str = "pending",
) -> Task:
    t = Task.new(title="Test", goal=goal, task_type=task_type)
    t.escalation_count = escalation_count
    t.assigned_worker = assigned_worker
    t.status = status
    return t


# route()

def test_plan_type_goes_to_claude():
    assert route(_task("Plan the auth module refactor", task_type="plan")) == "claude"

def test_explain_type_goes_to_claude():
    assert route(_task("Explain the session flow", task_type="explain")) == "claude"

def test_review_type_goes_to_claude():
    assert route(_task("Review this diff", task_type="review")) == "claude"

def test_redesign_keyword_goes_to_claude():
    assert route(_task("Redesign the database module")) == "claude"

def test_architecture_keyword_goes_to_claude():
    assert route(_task("Rethink the architecture of the auth system")) == "claude"

def test_add_test_goes_to_extension():
    assert route(_task("Add test for the login function")) == "extension"

def test_fix_import_goes_to_extension():
    assert route(_task("Fix import for utils module")) == "extension"

def test_rename_goes_to_extension():
    assert route(_task("Rename the function handle_request to handle_http")) == "extension"

def test_code_type_defaults_to_extension():
    assert route(_task("Update the timeout config value", task_type="code")) == "extension"

def test_non_code_type_defaults_to_claude():
    assert route(_task("Investigate why this is slow", task_type="debug")) == "claude"


# should_escalate()

def test_escalates_on_failure():
    t = _task("Fix bug", assigned_worker="extension", status="failed")
    assert should_escalate(t) is True

def test_escalates_when_blocked():
    t = _task("Fix bug", assigned_worker="extension", status="blocked")
    assert should_escalate(t) is True

def test_no_escalate_if_already_claude():
    t = _task("Fix bug", assigned_worker="claude", status="failed")
    assert should_escalate(t) is False

def test_no_escalate_beyond_two():
    t = _task("Fix bug", assigned_worker="extension", status="failed", escalation_count=2)
    assert should_escalate(t) is False

def test_no_escalate_completed_task():
    t = _task("Fix bug", assigned_worker="extension", status="completed")
    assert should_escalate(t) is False
