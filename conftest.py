import pytest


@pytest.fixture(autouse=True)
def _isolate_brain_path(tmp_path, monkeypatch):
    """Keep test traffic out of the repo's brain/ journals and decisions."""
    monkeypatch.setenv("MAHORAGA_BRAIN_PATH", str(tmp_path / "brain"))
