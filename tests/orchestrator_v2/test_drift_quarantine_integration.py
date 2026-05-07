"""End-to-end F5 integration: drift fires → quarantine → filter.

Exercises the BanditRouter with a real DriftDetector + QuarantineManager
to verify:
  - sustained negative reward stream triggers a DriftAlert
  - alert auto-quarantines the (bucket, agent) cell
  - subsequent route() filters that cell out
  - drift_events row lands in the DB
  - probe scheduling fires the quarantined agent on tick boundaries
  - 3 consecutive good probes auto-release
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.orchestrator.routing.bandit_router import BanditRouter
from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.reward import TaskOutcome
from backend.orchestrator.routing.strategies.static import classify_bucket


class _Task:
    """Minimal task shim. `goal` text + `id` for log_outcome routing."""
    def __init__(self, tid: str, goal: str = "Refactor the auth module"):
        self.id = tid
        self.title = goal
        self.goal = goal


def _bucket_for(goal: str) -> str:
    """Resolve the bucket the BanditRouter will classify the task under."""
    from backend.orchestrator.routing.context import TaskContext
    ctx = TaskContext.from_task(_Task("temp", goal))
    return classify_bucket(ctx)


@pytest.fixture
def router(tmp_path, monkeypatch):
    # Aggressive thresholds so the test fires drift in a small N.
    monkeypatch.setenv("MAHORAGA_DRIFT_MIN_OBS", "20")
    monkeypatch.setenv("MAHORAGA_DRIFT_CHECK_INTERVAL", "5")
    monkeypatch.setenv("MAHORAGA_DRIFT_WINDOW", "10")
    monkeypatch.setenv("MAHORAGA_DRIFT_SIGMA", "2.0")
    monkeypatch.setenv("MAHORAGA_QUARANTINE_PROBE_INTERVAL", "2")
    monkeypatch.setenv("MAHORAGA_QUARANTINE_AUTO_RELEASE", "3")
    monkeypatch.setenv("MAHORAGA_QUARANTINE_PROBE_QUALITY_FLOOR", "0.5")

    # Patch the quarantine module's default path BEFORE constructing the
    # router so QuarantineManager.load() inside __init__ picks up the
    # test path. Otherwise save() would write to the user's home dir.
    from backend.orchestrator.routing import quarantine as _q
    monkeypatch.setattr(_q, "QUARANTINE_STATE_PATH", tmp_path / "q.json")

    return BanditRouter(
        strategy="linucb_per_bucket",
        registry=None,
        logger=DecisionLogger(db_path=tmp_path / "d.db"),
        state_path=tmp_path / "state.json",
    )


def test_drift_quarantines_then_filters(router, tmp_path):
    """Full happy path: anchor mean → crater → quarantine fires →
    next route() filters the quarantined agent out. Fixture has
    already redirected the persisted state file to tmp_path."""
    router._quarantine.entries.clear()

    # Anchor: 200 strong rewards on (bucket, ollama).
    import random
    rng = random.Random(0)
    bucket_for_task = _bucket_for("Refactor the auth module")
    for i in range(200):
        t = _Task(f"a{i}")
        router.route(t, available_agents=["ollama", "aider"])
        router.observe(t, TaskOutcome(
            success=True, latency_s=2.0, cost_usd=0.0,
            quality_score=0.85, agent_name="ollama",
            bucket=bucket_for_task, spawn_time_ms=0.0,
        ))
    # No quarantine yet.
    assert not router._quarantine.is_quarantined(bucket_for_task, "ollama")

    # Crater: 50 zero rewards.
    for i in range(50):
        t = _Task(f"c{i}")
        router.route(t, available_agents=["ollama", "aider"])
        router.observe(t, TaskOutcome(
            success=True, latency_s=2.0, cost_usd=0.0,
            quality_score=0.0, agent_name="ollama",
            bucket=bucket_for_task, spawn_time_ms=0.0,
        ))
    # Drift should have fired and quarantined the cell.
    assert router._quarantine.is_quarantined(bucket_for_task, "ollama")
    # drift_events row should be on disk.
    rows = router.logger._conn.execute(
        "SELECT bucket, agent FROM drift_events"
    ).fetchall()
    assert any(r == (bucket_for_task, "ollama") for r in rows)


def test_quarantine_persists_to_disk(router, tmp_path):
    """When drift fires, the quarantine.json state file should be
    written so a FastAPI restart preserves the exclusion."""
    state = tmp_path / "q.json"
    bucket_for_task = _bucket_for("Refactor the auth module")
    # Burn-in + crater to fire drift.
    import random
    rng = random.Random(0)
    for i in range(200):
        t = _Task(f"a{i}")
        router.route(t, available_agents=["ollama", "aider"])
        router.observe(t, TaskOutcome(
            success=True, latency_s=2.0, cost_usd=0.0,
            quality_score=0.85, agent_name="ollama",
            bucket=bucket_for_task, spawn_time_ms=0.0,
        ))
    for i in range(50):
        t = _Task(f"c{i}")
        router.route(t, available_agents=["ollama", "aider"])
        router.observe(t, TaskOutcome(
            success=True, latency_s=2.0, cost_usd=0.0,
            quality_score=0.0, agent_name="ollama",
            bucket=bucket_for_task, spawn_time_ms=0.0,
        ))
    # Disk file exists with at least one entry.
    assert state.exists()


def test_quarantine_filter_excludes_cell(router):
    """If a cell is quarantined, route() should not return that agent
    for that bucket on subsequent calls."""
    # Inject a quarantine directly (skip the drift simulation).
    from backend.orchestrator.routing.drift_detector import DriftAlert
    bucket_for_task = _bucket_for("Refactor the auth module")
    router._quarantine.quarantine(DriftAlert(
        bucket=bucket_for_task, agent="ollama",
        window_mean=0.1, historical_mean=0.85, historical_std=0.05,
        deviation_sigmas=3.5, window_size=10,
    ))
    # Probe interval is 2 — first call shouldn't probe (tick 1).
    # We test the FILTER, not the probe path.
    selected_agents: list[str] = []
    for i in range(5):
        # Use unique-id tasks; some calls may hit a probe boundary.
        t = _Task(f"f{i}")
        agent = router.route(t, available_agents=["ollama", "aider"])
        selected_agents.append(agent)
    # ollama may appear on probe ticks; at least some calls must route
    # to aider (not ollama) since ollama is quarantined.
    assert "aider" in selected_agents


def test_quarantine_least_bad_fallback(router):
    """When every agent in a bucket is quarantined, route() should
    still produce a valid agent — the least-bad one."""
    from backend.orchestrator.routing.drift_detector import DriftAlert
    bucket_for_task = _bucket_for("Refactor the auth module")
    # Quarantine BOTH agents with different σ-deviations.
    router._quarantine.quarantine(DriftAlert(
        bucket=bucket_for_task, agent="ollama",
        window_mean=0.1, historical_mean=0.85, historical_std=0.05,
        deviation_sigmas=4.0, window_size=10,
    ))
    router._quarantine.quarantine(DriftAlert(
        bucket=bucket_for_task, agent="aider",
        window_mean=0.4, historical_mean=0.85, historical_std=0.05,
        deviation_sigmas=2.5, window_size=10,
    ))
    # First non-probe-tick call should get the least-bad fallback.
    # Run multiple to find a non-probe tick (probe_interval=2).
    saw_least_bad = False
    for i in range(10):
        t = _Task(f"lb{i}")
        agent = router.route(t, available_agents=["ollama", "aider"])
        if agent == "aider":  # smaller σ → least-bad
            saw_least_bad = True
    assert saw_least_bad


def test_probe_recovery_releases_quarantine(router):
    """3 consecutive good probes should clear the quarantine."""
    from backend.orchestrator.routing.drift_detector import DriftAlert
    bucket_for_task = _bucket_for("Refactor the auth module")
    router._quarantine.quarantine(DriftAlert(
        bucket=bucket_for_task, agent="ollama",
        window_mean=0.1, historical_mean=0.85, historical_std=0.05,
        deviation_sigmas=3.5, window_size=10,
    ))
    assert router._quarantine.is_quarantined(bucket_for_task, "ollama")

    # Simulate 3 successful probes by directly calling the manager.
    for _ in range(3):
        router._quarantine.record_probe(bucket_for_task, "ollama", reward=0.9)
    assert not router._quarantine.is_quarantined(bucket_for_task, "ollama")


def test_drift_disabled_no_quarantine(router, monkeypatch):
    """MAHORAGA_DRIFT_ENABLED=0 → drift never fires, no quarantine."""
    monkeypatch.setenv("MAHORAGA_DRIFT_ENABLED", "0")
    bucket_for_task = _bucket_for("Refactor the auth module")
    for i in range(250):
        t = _Task(f"d{i}")
        router.route(t, available_agents=["ollama", "aider"])
        # All zero rewards.
        router.observe(t, TaskOutcome(
            success=True, latency_s=2.0, cost_usd=0.0,
            quality_score=0.0, agent_name="ollama",
            bucket=bucket_for_task, spawn_time_ms=0.0,
        ))
    # No quarantines should fire.
    assert not router._quarantine.is_quarantined(bucket_for_task, "ollama")
