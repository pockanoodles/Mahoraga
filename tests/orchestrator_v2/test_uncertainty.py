"""Tests for A2 — confidence-aware escalation (routing/uncertainty.py)."""
from __future__ import annotations

import importlib
import os

import pytest

import backend.orchestrator.routing.uncertainty as unc
from backend.orchestrator.routing.uncertainty import (
    DEFAULT_GAP_THRESHOLD,
    DEFAULT_POLICY,
    DEFAULT_VARIANCE_THRESHOLD,
    POLICY_CLAUDE,
    POLICY_DOUBLE_RUN,
    POLICY_NONE,
    POLICY_VERIFY,
    UncertaintyHint,
    compute_hint,
    resolve_enabled,
    resolve_gap_threshold,
    resolve_policy,
    resolve_variance_threshold,
)


# ── env hygiene ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip the four MAHORAGA_ESCALATION_* vars before each test."""
    for k in (
        "MAHORAGA_ESCALATION_ENABLED",
        "MAHORAGA_ESCALATION_VARIANCE_THRESHOLD",
        "MAHORAGA_ESCALATION_GAP_THRESHOLD",
        "MAHORAGA_ESCALATION_POLICY",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


# ── env resolvers ─────────────────────────────────────────────────────────────


def test_defaults_when_unset():
    assert resolve_enabled() is False
    assert resolve_variance_threshold() == DEFAULT_VARIANCE_THRESHOLD
    assert resolve_gap_threshold() == DEFAULT_GAP_THRESHOLD
    assert resolve_policy() == DEFAULT_POLICY


def test_enabled_env_truthy(monkeypatch):
    for raw in ("1", "true", "yes", "on", "TRUE", "  on  "):
        monkeypatch.setenv("MAHORAGA_ESCALATION_ENABLED", raw)
        assert resolve_enabled() is True, raw


def test_enabled_env_falsy(monkeypatch):
    for raw in ("0", "false", "no", "off", "FALSE"):
        monkeypatch.setenv("MAHORAGA_ESCALATION_ENABLED", raw)
        assert resolve_enabled() is False, raw


def test_variance_threshold_env(monkeypatch):
    monkeypatch.setenv("MAHORAGA_ESCALATION_VARIANCE_THRESHOLD", "1.5")
    assert resolve_variance_threshold() == 1.5


def test_variance_threshold_env_invalid_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("MAHORAGA_ESCALATION_VARIANCE_THRESHOLD", "not_a_number")
    with caplog.at_level("WARNING"):
        assert resolve_variance_threshold() == DEFAULT_VARIANCE_THRESHOLD


def test_variance_threshold_out_of_range_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("MAHORAGA_ESCALATION_VARIANCE_THRESHOLD", "-5")
    with caplog.at_level("WARNING"):
        assert resolve_variance_threshold() == DEFAULT_VARIANCE_THRESHOLD


def test_gap_threshold_env(monkeypatch):
    monkeypatch.setenv("MAHORAGA_ESCALATION_GAP_THRESHOLD", "0.25")
    assert resolve_gap_threshold() == 0.25


def test_policy_env(monkeypatch):
    for p in (POLICY_NONE, POLICY_CLAUDE, POLICY_DOUBLE_RUN, POLICY_VERIFY):
        monkeypatch.setenv("MAHORAGA_ESCALATION_POLICY", p)
        assert resolve_policy() == p


def test_policy_env_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MAHORAGA_ESCALATION_POLICY", "🦅")
    assert resolve_policy() == DEFAULT_POLICY


# ── compute_hint ──────────────────────────────────────────────────────────────


def _scores(*, ucbs, variances) -> dict[str, dict[str, float]]:
    """Build a scores dict from parallel agent → ucb / variance lists."""
    out: dict[str, dict[str, float]] = {}
    for (agent, ucb), (_, var) in zip(ucbs.items(), variances.items()):
        out[agent] = {"ucb": ucb, "variance": var, "exploit": 0.0, "explore": 0.0}
    return out


def test_disabled_never_escalates_even_on_high_variance():
    scores = _scores(
        ucbs={"a": 1.0, "b": 0.5},
        variances={"a": 5.0, "b": 0.1},
    )
    h = compute_hint(
        "a", scores,
        enabled=False, variance_threshold=0.5, gap_threshold=0.05, policy=POLICY_CLAUDE,
    )
    assert h.should_escalate is False
    assert h.reason == "escalation_disabled"
    assert h.selected_variance == 5.0  # signal still surfaced


def test_policy_none_emits_signal_but_never_escalates():
    scores = _scores(
        ucbs={"a": 1.0, "b": 0.99},
        variances={"a": 10.0, "b": 0.0},
    )
    h = compute_hint(
        "a", scores,
        enabled=True, variance_threshold=0.5, gap_threshold=0.05, policy=POLICY_NONE,
    )
    assert h.should_escalate is False
    assert h.reason == "policy_none"


def test_high_variance_triggers():
    scores = _scores(
        ucbs={"a": 1.0, "b": 0.5},
        variances={"a": 0.7, "b": 0.1},
    )
    h = compute_hint(
        "a", scores,
        enabled=True, variance_threshold=0.5, gap_threshold=0.0, policy=POLICY_CLAUDE,
    )
    assert h.should_escalate is True
    assert "high_variance" in h.reason


def test_close_decision_gap_triggers():
    scores = _scores(
        ucbs={"a": 1.00, "b": 0.99},
        variances={"a": 0.1, "b": 0.1},
    )
    h = compute_hint(
        "a", scores,
        enabled=True, variance_threshold=10.0, gap_threshold=0.05, policy=POLICY_CLAUDE,
    )
    assert h.should_escalate is True
    assert "close_decision_gap" in h.reason


def test_both_triggers_combined_reason():
    scores = _scores(
        ucbs={"a": 1.00, "b": 0.99},
        variances={"a": 0.7, "b": 0.7},
    )
    h = compute_hint(
        "a", scores,
        enabled=True, variance_threshold=0.5, gap_threshold=0.05, policy=POLICY_CLAUDE,
    )
    assert h.should_escalate is True
    assert "high_variance_and_close_gap" in h.reason


def test_confident_when_low_variance_and_wide_gap():
    scores = _scores(
        ucbs={"a": 1.0, "b": 0.1},
        variances={"a": 0.05, "b": 0.05},
    )
    h = compute_hint(
        "a", scores,
        enabled=True, variance_threshold=0.5, gap_threshold=0.05, policy=POLICY_CLAUDE,
    )
    assert h.should_escalate is False
    assert "confident" in h.reason


def test_single_agent_decision_gap_is_infinite():
    scores = _scores(ucbs={"a": 1.0}, variances={"a": 0.05})
    h = compute_hint(
        "a", scores,
        enabled=True, variance_threshold=0.5, gap_threshold=0.05, policy=POLICY_CLAUDE,
    )
    assert h.decision_gap == float("inf")
    assert h.should_escalate is False


def test_to_dict_roundtrips():
    scores = _scores(
        ucbs={"a": 1.0, "b": 0.5},
        variances={"a": 0.05, "b": 0.05},
    )
    h = compute_hint("a", scores, enabled=True)
    d = h.to_dict()
    assert d["selected_agent"] == "a"
    assert d["selected_variance"] == 0.05
    assert d["decision_gap"] == 0.5


def test_resolvers_are_called_when_args_omitted(monkeypatch):
    monkeypatch.setenv("MAHORAGA_ESCALATION_ENABLED", "1")
    monkeypatch.setenv("MAHORAGA_ESCALATION_VARIANCE_THRESHOLD", "0.42")
    monkeypatch.setenv("MAHORAGA_ESCALATION_GAP_THRESHOLD", "0.07")
    monkeypatch.setenv("MAHORAGA_ESCALATION_POLICY", POLICY_VERIFY)
    scores = _scores(ucbs={"a": 1.0, "b": 0.95}, variances={"a": 0.05, "b": 0.05})
    h = compute_hint("a", scores)
    assert h.enabled is True
    assert h.variance_threshold == 0.42
    assert h.gap_threshold == 0.07
    assert h.policy == POLICY_VERIFY
