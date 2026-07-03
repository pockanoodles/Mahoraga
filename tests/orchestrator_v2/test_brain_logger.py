"""brain_logger must honor MAHORAGA_BRAIN_PATH at call time, not import time.

Regression: gateway tests exercised the real log_task_completion, and because
the brain path was frozen when the module was imported, every pytest run
appended fake "test-user" sessions to the repo's brain/journal/.
"""
from pathlib import Path

from backend.orchestrator import brain_logger


def test_log_task_completion_respects_env_path_at_call_time(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    brain.mkdir()
    monkeypatch.setenv("MAHORAGA_BRAIN_PATH", str(brain))

    written = brain_logger.log_task_completion(
        task_title="regression check", agent_used="test-arm"
    )

    assert written is not None
    assert Path(written).is_relative_to(brain)


def test_log_task_completion_noops_when_brain_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MAHORAGA_BRAIN_PATH", str(tmp_path / "nonexistent"))

    assert brain_logger.log_task_completion(task_title="x") is None
