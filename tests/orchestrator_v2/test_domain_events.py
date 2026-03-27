import pytest
from backend.orchestrator.domain.events import (
    make_event, ALL_EVENT_TYPES,
    TASK_CREATED, ATTEMPT_ESCALATED, APPROVAL_GRANTED, ARTIFACT_CREATED,
)


def test_make_event_known_type():
    e = make_event(run_id="r1", type=TASK_CREATED, payload={"title": "T"}, task_id="t1")
    assert e.type == TASK_CREATED
    assert e.payload == {"title": "T"}
    assert e.task_id == "t1"
    assert e.attempt_id is None
    assert e.id


def test_make_event_unknown_type_raises():
    with pytest.raises(ValueError, match="unknown.event"):
        make_event(run_id="r1", type="unknown.event")


def test_make_event_no_payload_defaults_to_empty_dict():
    e = make_event(run_id="r1", type=ARTIFACT_CREATED)
    assert e.payload == {}


def test_make_event_with_attempt_id():
    e = make_event(run_id="r1", type=ATTEMPT_ESCALATED,
                   task_id="t1", attempt_id="a1")
    assert e.attempt_id == "a1"


def test_all_event_types_are_valid():
    # Smoke test: every constant in ALL_EVENT_TYPES can be used in make_event
    for event_type in ALL_EVENT_TYPES:
        e = make_event(run_id="r1", type=event_type)
        assert e.type == event_type
