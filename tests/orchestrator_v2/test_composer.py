"""Tests for the cross-axis decision composer (routing/composer.py)."""
from __future__ import annotations

import pytest

from backend.orchestrator.routing.brain_retrieval import BrainEntry, BrainHit
from backend.orchestrator.routing.composer import (
    ComposedDecision,
    ComposerConfig,
    compose_decision,
)
from backend.orchestrator.routing.uncertainty import UncertaintyHint, POLICY_CLAUDE


# ── helpers ───────────────────────────────────────────────────────────────────


def _hint(*, escalate: bool, reason: str = "high_variance") -> UncertaintyHint:
    return UncertaintyHint(
        should_escalate=escalate,
        policy=POLICY_CLAUDE,
        selected_agent="ollama",
        selected_variance=1.0 if escalate else 0.05,
        decision_gap=0.5,
        variance_threshold=0.5,
        gap_threshold=0.05,
        reason=reason if escalate else "confident",
        enabled=True,
    )


def _hit(sim: float, title: str = "X") -> BrainHit:
    return BrainHit(
        entry=BrainEntry(path="p", title=title, body="b", kind="other", timestamp=None),
        similarity=sim,
    )


def _enabled_cfg(**kw) -> ComposerConfig:
    base = {"enabled": True}
    base.update(kw)
    return ComposerConfig(**base)


# ── env hygiene ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (
        "MAHORAGA_COMPOSER_ENABLED",
        "MAHORAGA_COMPOSER_A3_SUPPRESS_P",
        "MAHORAGA_COMPOSER_A3_OVERRIDE_MARGIN",
        "MAHORAGA_COMPOSER_BRAIN_SUPPRESS_SIM",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


# ── pass-through (disabled) ───────────────────────────────────────────────────


def test_disabled_passes_through():
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama", "aider"],
        uncertainty=_hint(escalate=True),
        a3_predictions={"ollama": 0.9, "aider": 0.5},
        brain_hits=[_hit(0.9)],
        # config defaults to enabled=False
    )
    assert out.agent == "ollama"
    assert out.escalate is True  # signal passed through, no adjustments applied
    assert out.adjustments == []
    assert out.enabled is False


def test_disabled_records_contributors_for_telemetry():
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama", "aider"],
        uncertainty=_hint(escalate=True),
        brain_hits=[_hit(0.9)],
    )
    # contributors are recorded even when disabled — useful for shadow logging.
    assert "a4_strong_match" in out.contributors


# ── A4 suppresses ─────────────────────────────────────────────────────────────


def test_a4_strong_match_suppresses_escalation():
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama", "aider"],
        uncertainty=_hint(escalate=True),
        brain_hits=[_hit(0.7)],
        config=_enabled_cfg(brain_suppress_sim=0.55),
    )
    assert out.escalate is False
    assert any(a["kind"] == "a4_suppress" for a in out.adjustments)


def test_a4_weak_match_does_not_suppress():
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama", "aider"],
        uncertainty=_hint(escalate=True),
        brain_hits=[_hit(0.30)],
        config=_enabled_cfg(brain_suppress_sim=0.55),
    )
    assert out.escalate is True
    assert all(a["kind"] != "a4_suppress" for a in out.adjustments)


def test_a4_no_hits_no_suppress():
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama"],
        uncertainty=_hint(escalate=True),
        brain_hits=None,
        config=_enabled_cfg(),
    )
    assert out.escalate is True
    assert out.brain_top_sim is None


# ── A3 suppresses ─────────────────────────────────────────────────────────────


def test_a3_high_p_for_picked_suppresses_escalation():
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama", "aider"],
        uncertainty=_hint(escalate=True),
        a3_predictions={"ollama": 0.85, "aider": 0.5},
        config=_enabled_cfg(a3_suppress_p=0.75),
    )
    assert out.escalate is False
    assert any(a["kind"] == "a3_suppress" for a in out.adjustments)


def test_a3_low_p_does_not_suppress():
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama", "aider"],
        uncertainty=_hint(escalate=True),
        a3_predictions={"ollama": 0.5, "aider": 0.4},
        config=_enabled_cfg(a3_suppress_p=0.75),
    )
    assert out.escalate is True


# ── A3 overrides ──────────────────────────────────────────────────────────────


def test_a3_overrides_when_clear_margin():
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama", "aider"],
        uncertainty=_hint(escalate=False),
        a3_predictions={"ollama": 0.4, "aider": 0.85},
        config=_enabled_cfg(a3_override_margin=0.20),
    )
    assert out.agent == "aider"
    assert out.bandit_pick == "ollama"
    assert any(a["kind"] == "a3_override" for a in out.adjustments)


def test_a3_does_not_override_below_margin():
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama", "aider"],
        uncertainty=None,
        a3_predictions={"ollama": 0.55, "aider": 0.65},
        config=_enabled_cfg(a3_override_margin=0.20),
    )
    assert out.agent == "ollama"


def test_a3_override_restricted_to_available():
    """Even if A3 thinks ‘claude’ is best, it can't override unless claude is available."""
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama", "aider"],
        uncertainty=None,
        a3_predictions={"ollama": 0.40, "aider": 0.55, "claude": 0.99},
        config=_enabled_cfg(a3_override_margin=0.10),
    )
    assert out.agent != "claude"


# ── stacking ──────────────────────────────────────────────────────────────────


def test_a4_takes_priority_over_a3_for_suppression():
    """When both A3 and A4 would suppress, A4 fires first; both adjustments
    can land but escalate flips on the first one."""
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama", "aider"],
        uncertainty=_hint(escalate=True),
        a3_predictions={"ollama": 0.9, "aider": 0.4},
        brain_hits=[_hit(0.9)],
        config=_enabled_cfg(),
    )
    assert out.escalate is False
    kinds = [a["kind"] for a in out.adjustments]
    assert "a4_suppress" in kinds


def test_a3_override_works_alongside_escalation_signal():
    """Override and escalate signals are independent; both can apply."""
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama", "aider"],
        uncertainty=_hint(escalate=True),
        a3_predictions={"ollama": 0.30, "aider": 0.85},
        config=_enabled_cfg(a3_override_margin=0.20, a3_suppress_p=0.99, brain_suppress_sim=0.99),
    )
    assert out.agent == "aider"  # override took effect
    assert out.escalate is True  # nothing suppressed it (a3_suppress_p too high)


# ── env config ────────────────────────────────────────────────────────────────


def test_env_thresholds_resolve(monkeypatch):
    monkeypatch.setenv("MAHORAGA_COMPOSER_ENABLED", "1")
    monkeypatch.setenv("MAHORAGA_COMPOSER_A3_SUPPRESS_P", "0.50")
    monkeypatch.setenv("MAHORAGA_COMPOSER_BRAIN_SUPPRESS_SIM", "0.10")
    cfg = ComposerConfig.from_env()
    assert cfg.enabled is True
    assert cfg.a3_suppress_p == 0.50
    assert cfg.brain_suppress_sim == 0.10


def test_env_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MAHORAGA_COMPOSER_A3_SUPPRESS_P", "garbage")
    cfg = ComposerConfig.from_env()
    assert cfg.a3_suppress_p == ComposerConfig().a3_suppress_p


# ── serialisation ─────────────────────────────────────────────────────────────


def test_to_dict_roundtrips():
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama"],
        uncertainty=_hint(escalate=False),
        config=_enabled_cfg(),
    )
    d = out.to_dict()
    assert d["agent"] == "ollama"
    assert d["enabled"] is True
    assert isinstance(d["contributors"], list)


def test_no_signals_at_all():
    """Composer must work with everything None / empty."""
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama"],
        config=_enabled_cfg(),
    )
    assert out.agent == "ollama"
    assert out.escalate is False
    assert out.adjustments == []
