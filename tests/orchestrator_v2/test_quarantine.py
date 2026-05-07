"""Tests for F5 quarantine manager — state, probe scheduling, recovery."""
from __future__ import annotations

import pytest

from backend.orchestrator.routing.drift_detector import DriftAlert
from backend.orchestrator.routing.quarantine import (
    DEFAULT_AUTO_RELEASE,
    DEFAULT_PROBE_INTERVAL,
    QuarantineEntry,
    QuarantineManager,
    resolve_auto_release,
    resolve_enabled,
    resolve_probe_interval,
)


def _alert(bucket: str = "code", agent: str = "ollama", sigma: float = 3.0) -> DriftAlert:
    return DriftAlert(
        bucket=bucket, agent=agent,
        window_mean=0.1, historical_mean=0.85, historical_std=0.05,
        deviation_sigmas=sigma, window_size=10,
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (
        "MAHORAGA_QUARANTINE_ENABLED",
        "MAHORAGA_QUARANTINE_PROBE_INTERVAL",
        "MAHORAGA_QUARANTINE_AUTO_RELEASE",
        "MAHORAGA_QUARANTINE_PROBE_QUALITY_FLOOR",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


# ── env resolvers ─────────────────────────────────────────────────────────────


def test_default_enabled():
    assert resolve_enabled() is True


def test_default_probe_interval():
    assert resolve_probe_interval() == DEFAULT_PROBE_INTERVAL


def test_default_auto_release():
    assert resolve_auto_release() == DEFAULT_AUTO_RELEASE


def test_env_override_probe_interval(monkeypatch):
    monkeypatch.setenv("MAHORAGA_QUARANTINE_PROBE_INTERVAL", "10")
    assert resolve_probe_interval() == 10


# ── basic state ──────────────────────────────────────────────────────────────


def test_quarantine_records_entry():
    mgr = QuarantineManager()
    mgr.quarantine(_alert("code", "ollama", sigma=3.5))
    assert mgr.is_quarantined("code", "ollama")
    assert not mgr.is_quarantined("code", "aider")
    assert not mgr.is_quarantined("research", "ollama")


def test_quarantine_per_bucket_isolation():
    """Acceptance criterion 3: quarantine in one bucket doesn't affect others."""
    mgr = QuarantineManager()
    mgr.quarantine(_alert("code", "ollama"))
    assert mgr.is_quarantined("code", "ollama")
    assert not mgr.is_quarantined("research", "ollama")
    assert "ollama" in mgr.quarantined_in_bucket("code")
    assert "ollama" not in mgr.quarantined_in_bucket("research")


def test_quarantine_idempotent_preserves_probe_history():
    """Re-quarantining a flapping cell shouldn't reset probe progress."""
    mgr = QuarantineManager(probe_interval=1, auto_release=3)
    mgr.quarantine(_alert("code", "ollama"))
    mgr.record_probe("code", "ollama", reward=0.8)
    mgr.record_probe("code", "ollama", reward=0.8)
    assert mgr.entries["code::ollama"].probe_successes == 2
    # Re-quarantine: probe history preserved.
    mgr.quarantine(_alert("code", "ollama", sigma=4.5))
    assert mgr.entries["code::ollama"].probe_successes == 2


def test_release_removes_entry():
    mgr = QuarantineManager()
    mgr.quarantine(_alert("code", "ollama"))
    assert mgr.release("code", "ollama") is True
    assert not mgr.is_quarantined("code", "ollama")


def test_release_nonexistent_returns_false():
    mgr = QuarantineManager()
    assert mgr.release("code", "ollama") is False


def test_manual_quarantine():
    """Operator can quarantine without a real DriftAlert (e.g. known outage)."""
    mgr = QuarantineManager()
    mgr.manual_quarantine("code", "claude", reason="rate_limit")
    assert mgr.is_quarantined("code", "claude")
    entry = mgr.entries["code::claude"]
    assert entry.reason_kind == "rate_limit"


# ── probe scheduling ────────────────────────────────────────────────────────


def test_probe_fires_on_interval_boundary():
    """Acceptance criterion 4: every probe_interval-th tick, return the
    quarantined agent."""
    mgr = QuarantineManager(probe_interval=3, auto_release=3)
    mgr.quarantine(_alert("code", "ollama"))
    available = ["ollama", "aider"]
    # First two ticks: no probe.
    assert mgr.maybe_probe("code", available) is None
    assert mgr.maybe_probe("code", available) is None
    # Third tick (interval boundary): probe.
    assert mgr.maybe_probe("code", available) == "ollama"
    # Continue ticks; sixth = next boundary.
    assert mgr.maybe_probe("code", available) is None
    assert mgr.maybe_probe("code", available) is None
    assert mgr.maybe_probe("code", available) == "ollama"


def test_probe_skips_unavailable_agents():
    mgr = QuarantineManager(probe_interval=1)
    mgr.quarantine(_alert("code", "ollama"))
    # ollama not in available set → no probe.
    assert mgr.maybe_probe("code", ["aider", "claude"]) is None


def test_probe_isolates_per_bucket():
    """A probe scheduled on `code` ticks shouldn't fire on `research` ticks."""
    mgr = QuarantineManager(probe_interval=2)
    mgr.quarantine(_alert("code", "ollama"))
    # Ticking research bucket doesn't probe code's quarantine.
    assert mgr.maybe_probe("research", ["ollama"]) is None
    assert mgr.maybe_probe("research", ["ollama"]) is None
    # Code's own ticking does.
    assert mgr.maybe_probe("code", ["ollama"]) is None
    assert mgr.maybe_probe("code", ["ollama"]) == "ollama"


# ── probe accounting ────────────────────────────────────────────────────────


def test_record_probe_success_increments():
    mgr = QuarantineManager(auto_release=10, probe_quality_floor=0.5)
    mgr.quarantine(_alert("code", "ollama"))
    status = mgr.record_probe("code", "ollama", reward=0.9)
    assert status == "progressed"
    assert mgr.entries["code::ollama"].probe_successes == 1


def test_record_probe_below_floor_resets():
    """Acceptance criterion 6: 1 failed probe after 2 successes → reset to 0."""
    mgr = QuarantineManager(auto_release=10, probe_quality_floor=0.5)
    mgr.quarantine(_alert("code", "ollama"))
    mgr.record_probe("code", "ollama", reward=0.9)
    mgr.record_probe("code", "ollama", reward=0.9)
    assert mgr.entries["code::ollama"].probe_successes == 2
    status = mgr.record_probe("code", "ollama", reward=0.2)
    assert status == "failed"
    assert mgr.entries["code::ollama"].probe_successes == 0


def test_record_probe_auto_release_after_threshold():
    """Acceptance criterion 5: 3 consecutive successful probes → release."""
    mgr = QuarantineManager(auto_release=3, probe_quality_floor=0.5)
    mgr.quarantine(_alert("code", "ollama"))
    assert mgr.record_probe("code", "ollama", reward=0.9) == "progressed"
    assert mgr.record_probe("code", "ollama", reward=0.9) == "progressed"
    assert mgr.record_probe("code", "ollama", reward=0.9) == "released"
    assert not mgr.is_quarantined("code", "ollama")


def test_record_probe_for_nonexistent_returns_none():
    mgr = QuarantineManager()
    assert mgr.record_probe("code", "ollama", reward=0.9) is None


# ── least-bad fallback ──────────────────────────────────────────────────────


def test_least_bad_picks_smallest_deviation():
    """Acceptance criterion 9: when every agent is quarantined, return
    the one with the smallest σ-deviation."""
    mgr = QuarantineManager()
    mgr.quarantine(_alert("code", "ollama", sigma=4.5))
    mgr.quarantine(_alert("code", "aider", sigma=2.5))
    mgr.quarantine(_alert("code", "codex-cli", sigma=3.0))
    assert mgr.least_bad_in_bucket("code") == "aider"


def test_least_bad_returns_none_when_bucket_empty():
    mgr = QuarantineManager()
    assert mgr.least_bad_in_bucket("code") is None


# ── persistence ──────────────────────────────────────────────────────────────


def test_save_load_roundtrip(tmp_path):
    mgr = QuarantineManager(probe_interval=10, auto_release=3)
    mgr.quarantine(_alert("code", "ollama"))
    mgr.record_probe("code", "ollama", reward=0.9)
    state = tmp_path / "q.json"
    mgr.save(state)

    mgr2 = QuarantineManager.load(state)
    assert mgr2.is_quarantined("code", "ollama")
    assert mgr2.entries["code::ollama"].probe_successes == 1


def test_load_missing_returns_default(tmp_path):
    mgr = QuarantineManager.load(tmp_path / "no_such.json")
    assert len(mgr.entries) == 0


def test_load_corrupt_returns_default(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("not valid json {")
    mgr = QuarantineManager.load(f)
    assert len(mgr.entries) == 0
