"""
A2 — Escalation strategies.

Spec: docs/v2-remaining-work.md §A2.

When `should_escalate=True` is set on a ComposedDecision, the gateway
needs to know HOW to escalate. Three strategies, selected by the
composer based on uncertainty level + budget state:

  CLAUDE: route the task to the Claude API adapter (paid, highest
          quality ceiling). Only fires when ANTHROPIC_API_KEY is set
          AND budget permits.

  DOUBLE_RUN: execute the bandit's pick AND the composer's preferred
              alternative; pick the higher-quality output via the
              4-layer scorer. 2× latency, $0 if both agents are free.
              Both outcomes log as episodes — the bandit learns from
              both decisions in one shot.

  AGGRESSIVE_VERIFY: execute the bandit's pick but with a stricter
                    quality threshold; on fail, retry with the
                    composer's preferred alternative. Cheapest of
                    the three.

Selection logic (per spec §A2):

    if anthropic_key_available and budget_permits:
        CLAUDE
    elif final_pick != bandit_pick:
        # composer already has a preferred alternative
        DOUBLE_RUN
    else:
        AGGRESSIVE_VERIFY

This module exposes a pure `select_strategy(...)` function. The actual
gateway hook (which adapter swap to perform, how to parallel-execute,
etc.) lives in `service/app.py` and consumes the strategy enum.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Iterable, Optional

from ..config import MahoragaConfig

_log = logging.getLogger(__name__)


class EscalationStrategy(str, Enum):
    """How to escalate when should_escalate=True. Inherits str so the
    enum value serialises cleanly into the decisions DB column."""
    NONE = "none"
    CLAUDE = "claude_escalation"
    DOUBLE_RUN = "double_run"
    AGGRESSIVE_VERIFY = "aggressive_verify"


def _read_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def resolve_allow_paid_escalation() -> bool:
    """When False, escalation never routes to Claude — only double_run
    or aggressive_verify. Default off so a fresh install never spends
    money without explicit opt-in."""
    raw = os.environ.get("MAHORAGA_ALLOW_PAID_ESCALATION")
    if raw is not None and raw.strip():
        return _read_bool_env("MAHORAGA_ALLOW_PAID_ESCALATION")
    try:
        cfg = MahoragaConfig().get("allow_paid_escalation")
    except (KeyError, FileNotFoundError):
        cfg = None
    if isinstance(cfg, bool):
        return cfg
    return False


def has_anthropic_key() -> bool:
    """Cheap key-presence check. Does not validate the key with Anthropic."""
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def select_strategy(
    *,
    should_escalate: bool,
    bandit_pick: str,
    final_pick: str,
    anthropic_key_available: Optional[bool] = None,
    budget_permits: Optional[bool] = None,
) -> EscalationStrategy:
    """Pure selection function — see module docstring for rules.

    All inputs are plain values so this is trivially testable. Defaults
    resolve from env when args are None: anthropic_key_available falls
    back to ANTHROPIC_API_KEY presence, budget_permits to the
    MAHORAGA_ALLOW_PAID_ESCALATION env (default False).
    """
    if not should_escalate:
        return EscalationStrategy.NONE

    if anthropic_key_available is None:
        anthropic_key_available = has_anthropic_key()
    if budget_permits is None:
        budget_permits = resolve_allow_paid_escalation()

    if anthropic_key_available and budget_permits:
        return EscalationStrategy.CLAUDE
    if final_pick != bandit_pick:
        return EscalationStrategy.DOUBLE_RUN
    return EscalationStrategy.AGGRESSIVE_VERIFY


CLAUDE_ADAPTER_NAME = "claude"


@dataclass
class EscalationAction:
    """The result of applying an EscalationStrategy at the gateway.

    `final_agent` is what the executor should actually run. May differ
    from the input `selected_agent` when the strategy swapped it (the
    only mutator today is CLAUDE_ESCALATION → "claude").

    `flags` carries metadata the verifier / executor consume:
      - "strict_verify": True for AGGRESSIVE_VERIFY → bumps the quality
        pass threshold from default to 0.70 (per spec §A2).
      - "double_run_alt": when DOUBLE_RUN fires, the alternative agent
        the gateway WOULD execute alongside `final_agent` once parallel
        execution lands. For now it's logged to telemetry only.

    `reason` is a one-line human-readable explanation suitable for
    log lines and dashboards.
    """
    final_agent: str
    strategy: str
    flags: dict
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _available_names(adapter_registry) -> set[str]:
    """Best-effort introspection of the adapter registry to avoid swapping
    to an agent that isn't actually loaded. Tolerates None / minimal
    registry shapes used by tests."""
    if adapter_registry is None:
        return set()
    if hasattr(adapter_registry, "all"):
        try:
            return {a.name for a in adapter_registry.all()}
        except Exception:  # noqa: BLE001
            return set()
    if isinstance(adapter_registry, Iterable):
        return {str(x) for x in adapter_registry}
    return set()


def apply_strategy(
    *,
    strategy: str,
    selected_agent: str,
    bandit_pick: Optional[str],
    would_be_agent: Optional[str],
    adapter_registry=None,
) -> EscalationAction:
    """Pure dispatcher consumed by service/app.py after route().

    NONE → passthrough (no log, no flag).
    CLAUDE → swap to "claude" if registered; else fall through to
             DOUBLE_RUN (or AGGRESSIVE_VERIFY if no alternative).
    DOUBLE_RUN → keep selected_agent, record `double_run_alt` flag for
                 telemetry. Actual parallel execution is a future
                 hook — the flag tells observability what would have
                 run alongside.
    AGGRESSIVE_VERIFY → keep selected_agent, set `strict_verify` flag
                       so the verifier applies the 0.70 threshold.
    """
    s = (strategy or EscalationStrategy.NONE.value).strip()

    if s == EscalationStrategy.NONE.value or s == "":
        return EscalationAction(
            final_agent=selected_agent,
            strategy=EscalationStrategy.NONE.value,
            flags={},
            reason="no_escalation",
        )

    if s == EscalationStrategy.CLAUDE.value:
        names = _available_names(adapter_registry)
        if CLAUDE_ADAPTER_NAME in names:
            return EscalationAction(
                final_agent=CLAUDE_ADAPTER_NAME,
                strategy=EscalationStrategy.CLAUDE.value,
                flags={"swapped_from": selected_agent},
                reason=f"claude_escalation: {selected_agent} → {CLAUDE_ADAPTER_NAME}",
            )
        # Claude not registered — fall through. Pick the next-best
        # strategy: double_run if we have a composer alternative, else
        # aggressive_verify. This keeps the gateway from breaking when
        # ANTHROPIC_API_KEY is set in env but the adapter wasn't loaded.
        _log.info(
            "escalation: claude requested but adapter not registered; "
            "falling through to %s",
            "double_run" if (
                would_be_agent and would_be_agent != selected_agent
            ) else "aggressive_verify",
        )
        if would_be_agent and would_be_agent != selected_agent:
            s = EscalationStrategy.DOUBLE_RUN.value
        else:
            s = EscalationStrategy.AGGRESSIVE_VERIFY.value

    if s == EscalationStrategy.DOUBLE_RUN.value:
        alt = would_be_agent if (
            would_be_agent and would_be_agent != selected_agent
        ) else None
        flags = {"double_run_alt": alt} if alt else {}
        # Without an alternative, double_run is degenerate — degrade.
        if alt is None:
            return EscalationAction(
                final_agent=selected_agent,
                strategy=EscalationStrategy.AGGRESSIVE_VERIFY.value,
                flags={"strict_verify": True},
                reason="double_run requested but no alternative; "
                       "fell through to aggressive_verify",
            )
        return EscalationAction(
            final_agent=selected_agent,
            strategy=EscalationStrategy.DOUBLE_RUN.value,
            flags=flags,
            reason=f"double_run telemetry: would also run {alt}",
        )

    # AGGRESSIVE_VERIFY (the default fallback)
    return EscalationAction(
        final_agent=selected_agent,
        strategy=EscalationStrategy.AGGRESSIVE_VERIFY.value,
        flags={"strict_verify": True},
        reason="aggressive_verify: strict quality threshold",
    )


# Quality threshold the verifier uses when strict_verify is set.
# Spec §A2: "stricter threshold (e.g., quality ≥ 0.70)".
STRICT_VERIFY_QUALITY_THRESHOLD = 0.70
