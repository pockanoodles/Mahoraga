import pytest


@pytest.fixture(autouse=True)
def _isolate_brain_path(tmp_path, monkeypatch):
    """Keep test traffic out of the repo's brain/ journals and decisions."""
    monkeypatch.setenv("MAHORAGA_BRAIN_PATH", str(tmp_path / "brain"))


@pytest.fixture(autouse=True)
def _no_live_reward_judge(monkeypatch):
    """The reward judge (default on) calls a live Ollama model; tests must
    never do real inference. Judge tests re-enable it explicitly and patch
    _get_judge_worker with a fake."""
    monkeypatch.setenv("MAHORAGA_REWARD_JUDGE", "off")
