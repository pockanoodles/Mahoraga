"""
A2 — Confidence-aware escalation (uncertainty signal).

Spec: docs/semantic-routing.md §15 — "What A1 Unlocks: A2".

LinUCB's posterior variance for arm a at context x is

    variance(a, x) = x' A_a^{-1} x

(the squared exploration term, before α scaling). It quantifies model
uncertainty about that arm's expected reward at this context. Two
related signals matter for escalation:

  - posterior_variance(selected) — how unsure are we about the chosen
    agent?
  - decision_gap = ucb(top1) - ucb(top2) — how confident are we that
    top1 actually beats top2?

This module turns those signals into an `UncertaintyHint` that the
caller (BanditRouter, gateway, dashboards) can inspect or act on. The
*action* (claude escalation, double-run, aggressive verification) is
the caller's responsibility — keeping this module a pure read-only
signal lets us empirically calibrate thresholds before committing to
mechanism. Note: distinct from `routing/escalation.py`, which handles
worker-failure retries; this is *confidence-driven* escalation, fired
before a task is even attempted.

Env knobs:
  MAHORAGA_ESCALATION_ENABLED            (bool, default off)
  MAHORAGA_ESCALATION_VARIANCE_THRESHOLD (float, default 0.5)
  MAHORAGA_ESCALATION_GAP_THRESHOLD      (float, default 0.05)
  MAHORAGA_ESCALATION_POLICY             ({none,claude,double_run,verify}, default claude)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from typing import Optional

from ..config import MahoragaConfig

_log = logging.getLogger(__name__)

POLICY_NONE = "none"
POLICY_CLAUDE = "claude"
POLICY_DOUBLE_RUN = "double_run"
POLICY_VERIFY = "verify"
_VALID_POLICIES = {POLICY_NONE, POLICY_CLAUDE, POLICY_DOUBLE_RUN, POLICY_VERIFY}

DEFAULT_VARIANCE_THRESHOLD = 0.5
DEFAULT_GAP_THRESHOLD = 0.05
DEFAULT_POLICY = POLICY_CLAUDE


def _read_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _read_float_env(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        v = float(raw)
    except ValueError:
        _log.warning("%s=%r is not a number; using default %.4f", name, raw, default)
        return default
    if not (lo <= v <= hi):
        _log.warning(
            "%s=%.4f out of [%.4f, %.4f]; using default %.4f",
            name, v, lo, hi, default,
        )
        return default
    return v


def _read_cfg(key: str) -> Optional[object]:
    try:
        return MahoragaConfig().get(key)
    except (KeyError, FileNotFoundError):
        return None


def resolve_enabled() -> bool:
    raw = os.environ.get("MAHORAGA_ESCALATION_ENABLED")
    if raw is not None and raw.strip():
        return _read_bool_env("MAHORAGA_ESCALATION_ENABLED")
    cfg = _read_cfg("escalation_enabled")
    if isinstance(cfg, bool):
        return cfg
    return False


def resolve_variance_threshold() -> float:
    raw = os.environ.get("MAHORAGA_ESCALATION_VARIANCE_THRESHOLD")
    if raw is not None and raw.strip():
        return _read_float_env(
            "MAHORAGA_ESCALATION_VARIANCE_THRESHOLD",
            DEFAULT_VARIANCE_THRESHOLD,
            lo=0.0, hi=100.0,
        )
    cfg = _read_cfg("escalation_variance_threshold")
    if isinstance(cfg, (int, float)) and 0.0 <= float(cfg) <= 100.0:
        return float(cfg)
    return DEFAULT_VARIANCE_THRESHOLD


def resolve_gap_threshold() -> float:
    raw = os.environ.get("MAHORAGA_ESCALATION_GAP_THRESHOLD")
    if raw is not None and raw.strip():
        return _read_float_env(
            "MAHORAGA_ESCALATION_GAP_THRESHOLD",
            DEFAULT_GAP_THRESHOLD,
            lo=0.0, hi=10.0,
        )
    cfg = _read_cfg("escalation_gap_threshold")
    if isinstance(cfg, (int, float)) and 0.0 <= float(cfg) <= 10.0:
        return float(cfg)
    return DEFAULT_GAP_THRESHOLD


def resolve_policy() -> str:
    raw = os.environ.get("MAHORAGA_ESCALATION_POLICY", "").strip().lower()
    if raw in _VALID_POLICIES:
        return raw
    if raw:
        _log.warning(
            "MAHORAGA_ESCALATION_POLICY=%r is invalid; using default %s",
            raw, DEFAULT_POLICY,
        )
    cfg = _read_cfg("escalation_policy")
    if isinstance(cfg, str) and cfg in _VALID_POLICIES:
        return cfg
    return DEFAULT_POLICY


@dataclass
class UncertaintyHint:
    """Read-only signal describing whether to escalate this routing decision."""
    should_escalate: bool
    policy: str
    selected_agent: str
    selected_variance: float
    decision_gap: float
    variance_threshold: float
    gap_threshold: float
    reason: str
    enabled: bool

    def to_dict(self) -> dict:
        return asdict(self)


def compute_hint(
    selected_agent: str,
    scores: dict[str, dict[str, float]],
    *,
    enabled: Optional[bool] = None,
    variance_threshold: Optional[float] = None,
    gap_threshold: Optional[float] = None,
    policy: Optional[str] = None,
) -> UncertaintyHint:
    """Build an UncertaintyHint from the bandit's per-agent scores.

    Caller passes the actually-chosen agent (which may differ from the
    bandit's argmax under memory blending). Escalation triggers when:
      a) posterior variance of `selected_agent` exceeds threshold, OR
      b) the decision gap (top1 ucb − top2 ucb) is below `gap_threshold`,
         indicating two near-tied candidates.

    With `policy == "none"` (or escalation disabled) we still emit the
    signal but never set should_escalate=True — useful for telemetry-only
    deployments while we calibrate thresholds.
    """
    enabled = resolve_enabled() if enabled is None else enabled
    variance_threshold = (
        resolve_variance_threshold() if variance_threshold is None else variance_threshold
    )
    gap_threshold = (
        resolve_gap_threshold() if gap_threshold is None else gap_threshold
    )
    policy = resolve_policy() if policy is None else policy

    selected_score = scores.get(selected_agent) or {}
    selected_variance = float(selected_score.get("variance", 0.0))

    ucbs = sorted(
        (float(s.get("ucb", 0.0)) for s in scores.values()),
        reverse=True,
    )
    decision_gap = (ucbs[0] - ucbs[1]) if len(ucbs) >= 2 else float("inf")

    triggered_by_variance = selected_variance > variance_threshold
    triggered_by_gap = (
        decision_gap < gap_threshold and len(ucbs) >= 2
    )

    if not enabled or policy == POLICY_NONE:
        should_escalate = False
        reason = "escalation_disabled" if not enabled else "policy_none"
    elif triggered_by_variance and triggered_by_gap:
        should_escalate = True
        reason = (
            f"high_variance_and_close_gap "
            f"(var={selected_variance:.4f}>{variance_threshold:.4f}, "
            f"gap={decision_gap:.4f}<{gap_threshold:.4f})"
        )
    elif triggered_by_variance:
        should_escalate = True
        reason = (
            f"high_variance "
            f"(var={selected_variance:.4f}>{variance_threshold:.4f})"
        )
    elif triggered_by_gap:
        should_escalate = True
        reason = (
            f"close_decision_gap "
            f"(gap={decision_gap:.4f}<{gap_threshold:.4f})"
        )
    else:
        should_escalate = False
        reason = (
            f"confident "
            f"(var={selected_variance:.4f}, gap={decision_gap:.4f})"
        )

    return UncertaintyHint(
        should_escalate=should_escalate,
        policy=policy,
        selected_agent=selected_agent,
        selected_variance=selected_variance,
        decision_gap=decision_gap,
        variance_threshold=variance_threshold,
        gap_threshold=gap_threshold,
        reason=reason,
        enabled=enabled,
    )
