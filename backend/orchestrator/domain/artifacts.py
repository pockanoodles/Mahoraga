from __future__ import annotations
from .models import Artifact

VALID_ARTIFACT_TYPES = frozenset({
    "file", "diff", "report", "test_result", "planning_output",
})


class InvalidArtifactType(ValueError):
    pass


def create_artifact(
    run_id: str, task_id: str, attempt_id: str,
    type: str, location: dict,
) -> Artifact:
    """Validate and create an Artifact domain object."""
    if type not in VALID_ARTIFACT_TYPES:
        raise InvalidArtifactType(
            f"Unknown artifact type: {type!r}. Valid: {sorted(VALID_ARTIFACT_TYPES)}"
        )
    if not location:
        raise ValueError("location must be a non-empty dict")
    return Artifact.new(
        run_id=run_id, task_id=task_id, attempt_id=attempt_id,
        type=type, location=location,
    )
