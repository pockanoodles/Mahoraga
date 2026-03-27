import pytest
from backend.orchestrator.domain.artifacts import (
    create_artifact, VALID_ARTIFACT_TYPES, InvalidArtifactType,
)


def test_create_artifact_file_type():
    art = create_artifact(
        run_id="r1", task_id="t1", attempt_id="a1",
        type="file", location={"path": "/tmp/result.py"},
    )
    assert art.type == "file"
    assert art.location == {"path": "/tmp/result.py"}
    assert art.run_id == "r1"
    assert art.id


def test_create_artifact_all_valid_types():
    for t in VALID_ARTIFACT_TYPES:
        art = create_artifact(
            run_id="r1", task_id="t1", attempt_id="a1",
            type=t, location={"ref": "x"},
        )
        assert art.type == t


def test_create_artifact_invalid_type_raises():
    with pytest.raises(InvalidArtifactType, match="unknown_type"):
        create_artifact(
            run_id="r1", task_id="t1", attempt_id="a1",
            type="unknown_type", location={"path": "/x"},
        )


def test_create_artifact_empty_location_raises():
    with pytest.raises(ValueError, match="location"):
        create_artifact(
            run_id="r1", task_id="t1", attempt_id="a1",
            type="file", location={},
        )
