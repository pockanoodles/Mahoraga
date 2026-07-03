"""
Cross-axis decision composer for A2/A3/A4 signals.

Each of the post-A1 axes emits a read-only signal:
  - A2 (`uncertainty.UncertaintyHint`): bandit posterior variance + decision gap.
  - A3 (`quality_predictor.QualityModel`): P(success | context, agent).
  - A4 (`brain_retrieval.BrainHit` list): top-k similar project-context entries.

In isolation each gives a partial view. Combined, they answer:
  "Should the bandit's argmax be trusted, escalated, or overridden?"

The composer is intentionally a pure function: signals in, a `ComposedDecision`
out. No I/O, no global state. It is NOT yet wired to alter the bandit pick
in production — the goal is to make calibration and unit-testing trivial
before any signal flips a switch.

Calibration philosophy
----------------------
A signal can either *suppress* or *trigger* escalation, but never silently
override an agent pick. The agent change requires the bandit pick AND A3
to agree there's a clearly better candidate (margin > config threshold).
This keeps the decision auditable: every adjustment shows up in
`ComposedDecision.adjustments` with a named cause.

Default rules (config knobs in parens):
  - A2 says escalate AND brain top-1 sim > brain_suppress_sim
        → suppress: "a4_suppress" (we have established context).
  - A2 says escalate AND A3 P(success | bandit_pick) > a3_suppress_p
        → suppress: "a3_suppress" (model is confident).
  - A3 says some other agent has p_alt > p_pick + a3_override_margin AND
    the alt agent is in `available`
        → override pick: "a3_override".
  - Otherwise pass-through: bandit pick + uncertainty.should_escalate.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass, asdict, field
from typing import Optional

from .uncertainty import UncertaintyHint
from .brain_retrieval import BrainHit
from .escalation_strategies import EscalationStrategy, select_strategy

_log = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────


@dataclass
class ComposerConfig:
    """Tunable thresholds. Resolved from env when not supplied explicitly."""
    a3_suppress_p: float = 0.75            # picked agent needs ≥ this P(success) to suppress escalation
    a3_override_margin: float = 0.20       # alt agent needs ≥ pick_p + this to override
    brain_suppress_sim: float = 0.55       # top-1 brain similarity ≥ this suppresses escalation
    enabled: bool = False                  # master gate; default off

    @classmethod
    def from_env(cls) -> "ComposerConfig":
        def _f(name: str, default: float) -> float:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                _log.warning("%s=%r not a float; using %.2f", name, raw, default)
                return default

        def _b(name: str, default: bool) -> bool:
            raw = os.environ.get(name, "").strip().lower()
            if raw in ("1", "true", "yes", "on"):
                return True
            if raw in ("0", "false", "no", "off"):
                return False
            return default

        return cls(
            a3_suppress_p=_f("MAHORAGA_COMPOSER_A3_SUPPRESS_P", cls.a3_suppress_p),
            a3_override_margin=_f("MAHORAGA_COMPOSER_A3_OVERRIDE_MARGIN", cls.a3_override_margin),
            brain_suppress_sim=_f("MAHORAGA_COMPOSER_BRAIN_SUPPRESS_SIM", cls.brain_suppress_sim),
            enabled=_b("MAHORAGA_COMPOSER_ENABLED", cls.enabled),
        )


# ── Output ────────────────────────────────────────────────────────────────────


@dataclass
class ComposedDecision:
    """Final cross-axis routing decision.

    `agent` may differ from the input `bandit_pick` only when A3 has a
    high-confidence alternative AND the composer is enabled. `escalate`
    starts as `uncertainty.should_escalate` and is then suppressed by
    A3/A4 evidence under the rules above.

    Shadow telemetry: `would_be_agent` and `would_be_escalate` are
    *always* computed (even when `enabled=False`), so we can log what
    the composer WOULD have decided if it had been enabled. After ~200
    shadow episodes you can compute counterfactual cumulative reward
    and compare against the bandit's actual cumulative reward — that's
    the calibration signal that tells you whether to flip enabled=True.

    `contributors` lists the signal IDs that materially affected the
    output (e.g. ["a2_high_variance", "a4_suppress"]). `adjustments`
    is a list of named transformations applied; each has a `kind` and
    a small explanation string.
    """
    agent: str
    escalate: bool
    contributors: list[str] = field(default_factory=list)
    adjustments: list[dict] = field(default_factory=list)
    bandit_pick: Optional[str] = None
    a3_pick: Optional[str] = None
    a3_picked_p: Optional[float] = None
    brain_top_sim: Optional[float] = None
    enabled: bool = False
    # Shadow-mode fields: composer's hypothetical decision if enabled.
    would_be_agent: Optional[str] = None
    would_be_escalate: Optional[bool] = None
    # A2: which escalation strategy the gateway should run when
    # `escalate=True`. NONE if no escalation. Selected from the
    # bandit_pick / would_be_agent pair + budget/key state.
    escalation_strategy: str = EscalationStrategy.NONE.value

    def to_dict(self) -> dict:
        return asdict(self)


# ── Composition ───────────────────────────────────────────────────────────────


def compose_decision(
    *,
    bandit_pick: str,
    available: list[str],
    uncertainty: Optional[UncertaintyHint] = None,
    a3_predictions: Optional[dict[str, float]] = None,
    brain_hits: Optional[list[BrainHit]] = None,
    config: Optional[ComposerConfig] = None,
) -> ComposedDecision:
    """Combine A2/A3/A4 signals into a single routing decision.

    Inputs:
      bandit_pick      — agent the bandit (with memory blending) chose.
      available        — currently routable agents.
      uncertainty      — A2 hint; pass-through if None.
      a3_predictions   — {agent: P(success)} from A3; ignored if None or empty.
      brain_hits       — top-k A4 BrainHits; ignored if None or empty.
      config           — thresholds; defaults to env-resolved.

    The composer is `enabled=False` by default, in which case it returns
    a pass-through ComposedDecision (agent = bandit_pick, escalate =
    uncertainty.should_escalate, no adjustments). This lets it run as
    a shadow signal in production without altering live behaviour.
    """
    cfg = config or ComposerConfig.from_env()
    contributors: list[str] = []
    adjustments: list[dict] = []

    base_escalate = bool(uncertainty and uncertainty.should_escalate)
    if uncertainty and uncertainty.should_escalate:
        contributors.append(f"a2_{uncertainty.reason.split()[0]}")

    a3_picked_p: Optional[float] = None
    a3_pick: Optional[str] = None
    a3_pick_p: Optional[float] = None
    if a3_predictions:
        a3_picked_p = a3_predictions.get(bandit_pick)
        # Best alternative restricted to currently-available agents.
        ranked = sorted(
            ((a, p) for a, p in a3_predictions.items() if a in available),
            key=lambda kv: kv[1], reverse=True,
        )
        if ranked:
            a3_pick, a3_pick_p = ranked[0]
            if a3_pick != bandit_pick:
                contributors.append("a3_disagrees_with_bandit")

    brain_top_sim: Optional[float] = None
    if brain_hits:
        brain_top_sim = max((h.similarity for h in brain_hits), default=None)
        if brain_top_sim is not None and brain_top_sim >= cfg.brain_suppress_sim:
            contributors.append("a4_strong_match")

    # Compute the rules' output unconditionally — that gives us the
    # "what the composer would do" decision used for shadow telemetry.
    would_be_agent = bandit_pick
    would_be_escalate = base_escalate

    # Rule 1: A4 strong match suppresses escalation.
    if (
        would_be_escalate
        and brain_top_sim is not None
        and brain_top_sim >= cfg.brain_suppress_sim
    ):
        would_be_escalate = False
        adjustments.append({
            "kind": "a4_suppress",
            "reason": f"brain_top_sim={brain_top_sim:.3f}≥{cfg.brain_suppress_sim:.3f}",
        })

    # Rule 2: A3 high P(success) for picked agent suppresses escalation.
    if (
        would_be_escalate
        and a3_picked_p is not None
        and a3_picked_p >= cfg.a3_suppress_p
    ):
        would_be_escalate = False
        adjustments.append({
            "kind": "a3_suppress",
            "reason": f"p(picked)={a3_picked_p:.3f}≥{cfg.a3_suppress_p:.3f}",
        })

    # Rule 3: A3 has a clear winner with margin → override pick.
    if (
        a3_pick is not None
        and a3_pick != bandit_pick
        and a3_pick_p is not None
        and a3_picked_p is not None
        and (a3_pick_p - a3_picked_p) >= cfg.a3_override_margin
    ):
        adjustments.append({
            "kind": "a3_override",
            "reason": (
                f"p({a3_pick})={a3_pick_p:.3f} > "
                f"p({bandit_pick})={a3_picked_p:.3f} "
                f"+ {cfg.a3_override_margin:.2f}"
            ),
        })
        would_be_agent = a3_pick

    # Apply only when enabled. In shadow mode we report the bandit pick
    # as the live decision but still surface the would_be_* fields so
    # offline analysis can compare counterfactual reward.
    final_agent = would_be_agent if cfg.enabled else bandit_pick
    final_escalate = would_be_escalate if cfg.enabled else base_escalate

    # A2: pick the escalation mechanism for this decision. The gateway
    # consumes this in service/app.py when escalate=True. Computed on
    # would_be_agent (not final_agent) so the strategy reflects the
    # composer's hypothesis, even in shadow mode.
    strategy = select_strategy(
        should_escalate=bool(would_be_escalate),
        bandit_pick=bandit_pick,
        final_pick=would_be_agent,
    )

    return ComposedDecision(
        agent=final_agent,
        escalate=final_escalate,
        contributors=contributors,
        adjustments=adjustments if cfg.enabled else [],
        bandit_pick=bandit_pick,
        a3_pick=a3_pick,
        a3_picked_p=a3_picked_p,
        brain_top_sim=brain_top_sim,
        enabled=cfg.enabled,
        would_be_agent=would_be_agent,
        would_be_escalate=would_be_escalate,
        escalation_strategy=strategy.value,
    )
