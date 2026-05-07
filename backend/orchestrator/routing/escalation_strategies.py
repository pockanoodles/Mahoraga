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

import os
from enum import Enum
from typing import Optional

from ..config import MahoragaConfig


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
