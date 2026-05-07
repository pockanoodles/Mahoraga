"""Tests for A2 — escalation strategy selection (routing/escalation_strategies.py).

Spec: docs/v2-remaining-work.md §A2.
"""
from __future__ import annotations

import pytest

from backend.orchestrator.routing.escalation_strategies import (
    EscalationStrategy,
    has_anthropic_key,
    resolve_allow_paid_escalation,
    select_strategy,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("MAHORAGA_ALLOW_PAID_ESCALATION", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_no_escalation_returns_none():
    s = select_strategy(
        should_escalate=False, bandit_pick="ollama", final_pick="ollama",
        anthropic_key_available=True, budget_permits=True,
    )
    assert s == EscalationStrategy.NONE


def test_claude_when_key_and_budget():
    s = select_strategy(
        should_escalate=True, bandit_pick="ollama", final_pick="ollama",
        anthropic_key_available=True, budget_permits=True,
    )
    assert s == EscalationStrategy.CLAUDE


def test_double_run_when_picks_differ_and_no_paid():
    """Composer override + no Claude budget → double-run (cheapest way
    to honour both candidates)."""
    s = select_strategy(
        should_escalate=True, bandit_pick="ollama", final_pick="aider",
        anthropic_key_available=True, budget_permits=False,
    )
    assert s == EscalationStrategy.DOUBLE_RUN


def test_aggressive_verify_when_picks_match_and_no_paid():
    """No alternative + no budget → cheapest path: rerun with stricter
    quality bar."""
    s = select_strategy(
        should_escalate=True, bandit_pick="ollama", final_pick="ollama",
        anthropic_key_available=False, budget_permits=False,
    )
    assert s == EscalationStrategy.AGGRESSIVE_VERIFY


def test_no_key_falls_through_to_double_run():
    """Even with budget enabled, no key means we can't escalate to Claude."""
    s = select_strategy(
        should_escalate=True, bandit_pick="ollama", final_pick="aider",
        anthropic_key_available=False, budget_permits=True,
    )
    assert s == EscalationStrategy.DOUBLE_RUN


def test_resolves_from_env_when_args_omitted(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("MAHORAGA_ALLOW_PAID_ESCALATION", "1")
    s = select_strategy(
        should_escalate=True, bandit_pick="ollama", final_pick="ollama",
    )
    assert s == EscalationStrategy.CLAUDE


def test_default_disallows_paid_escalation():
    """Default off so a fresh install never accidentally spends."""
    assert resolve_allow_paid_escalation() is False


def test_has_anthropic_key_detects_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert has_anthropic_key() is True


def test_has_anthropic_key_strips_whitespace(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert has_anthropic_key() is False


def test_strategy_values_serialise_as_strings():
    """We persist the strategy in the decisions DB as TEXT — values
    must be plain strings (StrEnum-like behaviour)."""
    assert EscalationStrategy.NONE.value == "none"
    assert EscalationStrategy.CLAUDE.value == "claude_escalation"
    assert EscalationStrategy.DOUBLE_RUN.value == "double_run"
    assert EscalationStrategy.AGGRESSIVE_VERIFY.value == "aggressive_verify"


# ── ComposedDecision integration ──────────────────────────────────────────────


def test_composer_emits_strategy_field():
    """Round-trip through compose_decision: strategy must land on the
    output, even in shadow mode."""
    from backend.orchestrator.routing.brain_retrieval import BrainEntry, BrainHit
    from backend.orchestrator.routing.composer import (
        ComposerConfig, compose_decision,
    )
    from backend.orchestrator.routing.uncertainty import (
        UncertaintyHint, POLICY_CLAUDE,
    )
    hint = UncertaintyHint(
        should_escalate=True, policy=POLICY_CLAUDE, selected_agent="ollama",
        selected_variance=1.0, decision_gap=0.5, variance_threshold=0.5,
        gap_threshold=0.05, reason="high_variance", enabled=True,
    )
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama", "aider"],
        uncertainty=hint,
        a3_predictions={"ollama": 0.30, "aider": 0.85},
        brain_hits=None,
        config=ComposerConfig(enabled=True, a3_override_margin=0.20),
    )
    # A3 override fires (p_alt=0.85 vs p_pick=0.30), so picks differ.
    # No ANTHROPIC_API_KEY in test env → falls to DOUBLE_RUN.
    assert out.escalation_strategy in (
        EscalationStrategy.DOUBLE_RUN.value,
        EscalationStrategy.CLAUDE.value,
        EscalationStrategy.AGGRESSIVE_VERIFY.value,
    )


def test_composer_escalation_strategy_none_when_no_escalation():
    from backend.orchestrator.routing.composer import (
        ComposerConfig, compose_decision,
    )
    out = compose_decision(
        bandit_pick="ollama",
        available=["ollama"],
        uncertainty=None,
        config=ComposerConfig(enabled=True),
    )
    assert out.escalation_strategy == EscalationStrategy.NONE.value
