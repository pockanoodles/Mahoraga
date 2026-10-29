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


@pytest.fixture(autouse=True)
def _no_live_escalation(monkeypatch):
    """The escalation cascade (default on) spawns the real `claude` CLI.

    Turning the reward judge off is NOT enough: the cascade also fires on an
    execution-gate failure, which needs no judge verdict at all. Without this,
    any test whose fixture output fails to compile bills a real cloud call —
    which is exactly what happened when the exec-gate trigger landed. Cascade
    tests delete this variable and patch `escalate` with a fake.
    """
    monkeypatch.setenv("MAHORAGA_CASCADE", "off")


@pytest.fixture(autouse=True)
def _local_ollama_url(monkeypatch):
    """Every Ollama call reads OLLAMA_BASE_URL when it runs, so a developer's own
    setting must not leak into tests that expect the local default. Tests of the
    variable itself set it back."""
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)


@pytest.fixture(autouse=True)
def _default_state_home(monkeypatch):
    """paths.home() reads MAHORAGA_HOME, so a developer's own setting must not
    send a test's state into their real directory. Tests of the variable set it."""
    monkeypatch.delenv("MAHORAGA_HOME", raising=False)
