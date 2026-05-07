"""Tests for A2 — escalation strategy selection (routing/escalation_strategies.py).

Spec: docs/v2-remaining-work.md §A2.
"""
from __future__ import annotations

import pytest

from backend.orchestrator.routing.escalation_strategies import (
    CLAUDE_ADAPTER_NAME,
    STRICT_VERIFY_QUALITY_THRESHOLD,
    EscalationAction,
    EscalationStrategy,
    apply_strategy,
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


# ── apply_strategy (gateway dispatcher) ───────────────────────────────────────


class _FakeRegistry:
    """Minimal stand-in for AdapterRegistry.all() — yields adapters with .name."""

    def __init__(self, names):
        class _Adapter:
            def __init__(self, n):
                self.name = n
        self._adapters = [_Adapter(n) for n in names]

    def all(self):
        return list(self._adapters)


def test_apply_none_passthrough():
    action = apply_strategy(
        strategy=EscalationStrategy.NONE.value,
        selected_agent="ollama",
        bandit_pick="ollama",
        would_be_agent="ollama",
        adapter_registry=_FakeRegistry(["ollama", "claude"]),
    )
    assert action.final_agent == "ollama"
    assert action.flags == {}
    assert action.strategy == EscalationStrategy.NONE.value


def test_apply_claude_swaps_when_registered():
    action = apply_strategy(
        strategy=EscalationStrategy.CLAUDE.value,
        selected_agent="ollama",
        bandit_pick="ollama",
        would_be_agent="ollama",
        adapter_registry=_FakeRegistry(["ollama", "claude"]),
    )
    assert action.final_agent == CLAUDE_ADAPTER_NAME
    assert action.flags == {"swapped_from": "ollama"}
    assert action.strategy == EscalationStrategy.CLAUDE.value


def test_apply_claude_falls_through_when_unregistered_with_alt():
    """Claude requested but not registered + alternative exists → DOUBLE_RUN."""
    action = apply_strategy(
        strategy=EscalationStrategy.CLAUDE.value,
        selected_agent="ollama",
        bandit_pick="ollama",
        would_be_agent="aider",
        adapter_registry=_FakeRegistry(["ollama", "aider"]),  # no claude
    )
    assert action.strategy == EscalationStrategy.DOUBLE_RUN.value
    assert action.flags.get("double_run_alt") == "aider"
    assert action.final_agent == "ollama"


def test_apply_claude_falls_through_to_verify_when_no_alt():
    """Claude unavailable AND no composer alternative → AGGRESSIVE_VERIFY."""
    action = apply_strategy(
        strategy=EscalationStrategy.CLAUDE.value,
        selected_agent="ollama",
        bandit_pick="ollama",
        would_be_agent="ollama",
        adapter_registry=_FakeRegistry(["ollama"]),
    )
    assert action.strategy == EscalationStrategy.AGGRESSIVE_VERIFY.value
    assert action.flags.get("strict_verify") is True


def test_apply_double_run_records_alt():
    action = apply_strategy(
        strategy=EscalationStrategy.DOUBLE_RUN.value,
        selected_agent="ollama",
        bandit_pick="ollama",
        would_be_agent="aider",
        adapter_registry=_FakeRegistry(["ollama", "aider"]),
    )
    assert action.final_agent == "ollama"
    assert action.flags["double_run_alt"] == "aider"


def test_apply_double_run_no_alt_degrades_to_verify():
    """If would_be_agent == selected_agent there's nothing to double-run."""
    action = apply_strategy(
        strategy=EscalationStrategy.DOUBLE_RUN.value,
        selected_agent="ollama",
        bandit_pick="ollama",
        would_be_agent="ollama",
        adapter_registry=_FakeRegistry(["ollama"]),
    )
    assert action.strategy == EscalationStrategy.AGGRESSIVE_VERIFY.value
    assert action.flags.get("strict_verify") is True


def test_apply_aggressive_verify_sets_flag():
    action = apply_strategy(
        strategy=EscalationStrategy.AGGRESSIVE_VERIFY.value,
        selected_agent="ollama",
        bandit_pick="ollama",
        would_be_agent="ollama",
        adapter_registry=_FakeRegistry(["ollama"]),
    )
    assert action.flags["strict_verify"] is True
    assert action.final_agent == "ollama"


def test_apply_handles_none_registry():
    """Defensive: no registry passed → claude can't be swapped, fall through."""
    action = apply_strategy(
        strategy=EscalationStrategy.CLAUDE.value,
        selected_agent="ollama",
        bandit_pick="ollama",
        would_be_agent="aider",
        adapter_registry=None,
    )
    # No registry → claude unavailable → degrades to double_run.
    assert action.strategy == EscalationStrategy.DOUBLE_RUN.value


def test_apply_handles_unknown_strategy_string():
    """Unknown strategy → defaults to AGGRESSIVE_VERIFY (safer than no-op)."""
    action = apply_strategy(
        strategy="some_future_strategy",
        selected_agent="ollama",
        bandit_pick="ollama",
        would_be_agent="ollama",
        adapter_registry=_FakeRegistry(["ollama"]),
    )
    assert action.strategy == EscalationStrategy.AGGRESSIVE_VERIFY.value


def test_apply_action_to_dict_serialises():
    action = apply_strategy(
        strategy=EscalationStrategy.AGGRESSIVE_VERIFY.value,
        selected_agent="ollama",
        bandit_pick="ollama",
        would_be_agent="ollama",
    )
    d = action.to_dict()
    assert d["final_agent"] == "ollama"
    assert d["strategy"] == EscalationStrategy.AGGRESSIVE_VERIFY.value
    assert isinstance(d["flags"], dict)


def test_strict_verify_threshold_is_70_percent():
    """Spec §A2: aggressive_verify uses quality ≥ 0.70."""
    assert STRICT_VERIFY_QUALITY_THRESHOLD == 0.70


# ── service/app.py _gateway_escalation integration ───────────────────────────


def test_gateway_escalation_passthrough_when_composer_none(monkeypatch, tmp_path):
    """No composer ran → final agent unchanged, no flags."""
    from backend.orchestrator.routing.bandit_router import BanditRouter
    from backend.orchestrator.routing.decision_log import DecisionLogger
    from backend.orchestrator.service.app import _gateway_escalation

    r = BanditRouter(
        strategy="linucb_per_bucket",
        registry=None,
        logger=DecisionLogger(db_path=tmp_path / "d.db"),
        state_path=tmp_path / "state.json",
    )
    # No route() called → _last_composed is None.
    final, flags = _gateway_escalation(r, "ollama", _FakeRegistry(["ollama"]))
    assert final == "ollama"
    assert flags == {}


def test_gateway_escalation_swaps_to_claude_when_strategy_set(monkeypatch, tmp_path):
    """Force CLAUDE strategy on a real ComposedDecision → swap fires."""
    from backend.orchestrator.routing.bandit_router import BanditRouter
    from backend.orchestrator.routing.composer import ComposedDecision
    from backend.orchestrator.routing.decision_log import DecisionLogger
    from backend.orchestrator.service.app import _gateway_escalation

    r = BanditRouter(
        strategy="linucb_per_bucket",
        registry=None,
        logger=DecisionLogger(db_path=tmp_path / "d.db"),
        state_path=tmp_path / "state.json",
    )
    # Inject a ComposedDecision as if route() had finished.
    r._last_composed = ComposedDecision(
        agent="ollama",
        escalate=True,
        bandit_pick="ollama",
        would_be_agent="ollama",
        enabled=False,
        escalation_strategy=EscalationStrategy.CLAUDE.value,
    )
    final, flags = _gateway_escalation(
        r, "ollama", _FakeRegistry(["ollama", "claude"]),
    )
    assert final == "claude"
    assert flags["swapped_from"] == "ollama"


def test_gateway_escalation_aggressive_verify_flag(tmp_path):
    """AGGRESSIVE_VERIFY → strict_verify flag returned to executor."""
    from backend.orchestrator.routing.bandit_router import BanditRouter
    from backend.orchestrator.routing.composer import ComposedDecision
    from backend.orchestrator.routing.decision_log import DecisionLogger
    from backend.orchestrator.service.app import _gateway_escalation

    r = BanditRouter(
        strategy="linucb_per_bucket",
        registry=None,
        logger=DecisionLogger(db_path=tmp_path / "d.db"),
        state_path=tmp_path / "state.json",
    )
    r._last_composed = ComposedDecision(
        agent="ollama",
        escalate=True,
        bandit_pick="ollama",
        would_be_agent="ollama",
        enabled=False,
        escalation_strategy=EscalationStrategy.AGGRESSIVE_VERIFY.value,
    )
    final, flags = _gateway_escalation(r, "ollama", _FakeRegistry(["ollama"]))
    assert final == "ollama"
    assert flags["strict_verify"] is True


def test_gateway_escalation_double_run_records_alt(tmp_path):
    from backend.orchestrator.routing.bandit_router import BanditRouter
    from backend.orchestrator.routing.composer import ComposedDecision
    from backend.orchestrator.routing.decision_log import DecisionLogger
    from backend.orchestrator.service.app import _gateway_escalation

    r = BanditRouter(
        strategy="linucb_per_bucket",
        registry=None,
        logger=DecisionLogger(db_path=tmp_path / "d.db"),
        state_path=tmp_path / "state.json",
    )
    r._last_composed = ComposedDecision(
        agent="ollama",
        escalate=True,
        bandit_pick="ollama",
        would_be_agent="aider",
        enabled=False,
        escalation_strategy=EscalationStrategy.DOUBLE_RUN.value,
    )
    final, flags = _gateway_escalation(
        r, "ollama", _FakeRegistry(["ollama", "aider"]),
    )
    assert final == "ollama"
    assert flags.get("double_run_alt") == "aider"
