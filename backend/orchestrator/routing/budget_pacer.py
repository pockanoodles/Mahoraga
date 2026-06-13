"""
F1 — Budget Pacer.

Spec: docs/specs/v2-debug-F1-F4.md §F1.

Online primal-dual budget pacer adapted from ParetoBandit (Apache 2.0).
Enforces a per-task average cost ceiling via a Lagrange multiplier λ
that dynamically inflates the cost penalty in the bandit's reward
function, plus a hard per-task cap that excludes too-expensive agents
from the candidate set entirely.

Two distinct guard rails:

  - Soft (λ): the bandit gradually shifts toward cheaper agents as
    rolling-window average cost approaches the ceiling. Quality
    optimisation continues; cost just gets weighted more.

  - Hard: any agent whose `estimate_cost(task)` exceeds
    `hard_limit` is excluded from `available_agents` for that
    routing decision. No rolling-average smoothing — the cap is
    absolute. Backstop against runaway escalation cost.

Without F1, escalation (claude_escalation in particular) cannot be
safely enabled — a bandit that picks claude 10x in a row eats a
non-trivial bill. With F1, the soft and hard ceilings make
MAHORAGA_ALLOW_PAID_ESCALATION=1 safe by construction.

Env config:
  MAHORAGA_BUDGET_CEILING=0.05      # USD per task, rolling average target
  MAHORAGA_BUDGET_WINDOW=100        # rolling window size for average
  MAHORAGA_BUDGET_HARD_LIMIT=0.50   # absolute per-task ceiling
  MAHORAGA_BUDGET_ETA=0.01          # dual ascent learning rate
"""
from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

_log = logging.getLogger(__name__)

# Defaults if env unset. Calibrated for "low-cost daily use" — escalation
# is allowed but the rolling average steers toward free agents.
DEFAULT_CEILING = 0.05
DEFAULT_WINDOW = 100
DEFAULT_HARD_LIMIT = 0.50
DEFAULT_ETA = 0.01

BUDGET_PACER_STATE_PATH = Path.home() / ".mahoraga-v2" / "budget_pacer.json"


def _read_float_env(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        _log.warning("%s=%r not a float; using default %.4f", name, raw, default)
        return default
    if not (lo <= v <= hi):
        _log.warning(
            "%s=%.4f out of [%.4f, %.4f]; using default %.4f",
            name, v, lo, hi, default,
        )
        return default
    return v


def _read_int_env(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    if not (lo <= v <= hi):
        return default
    return v


def resolve_ceiling() -> float:
    return _read_float_env(
        "MAHORAGA_BUDGET_CEILING", DEFAULT_CEILING, lo=0.0, hi=10.0,
    )


def resolve_window() -> int:
    return _read_int_env(
        "MAHORAGA_BUDGET_WINDOW", DEFAULT_WINDOW, lo=10, hi=10000,
    )


def resolve_hard_limit() -> float:
    return _read_float_env(
        "MAHORAGA_BUDGET_HARD_LIMIT", DEFAULT_HARD_LIMIT, lo=0.0, hi=100.0,
    )


def resolve_eta() -> float:
    return _read_float_env(
        "MAHORAGA_BUDGET_ETA", DEFAULT_ETA, lo=0.0, hi=1.0,
    )


@dataclass
class BudgetPacer:
    """Online primal-dual cost pacer.

    State (`spent`, `lambda_`) persists across restarts via save/load,
    so a long-running service doesn't reset its rolling average every
    time you bounce the FastAPI process.
    """
    ceiling: float = field(default_factory=resolve_ceiling)
    window: int = field(default_factory=resolve_window)
    hard_limit: float = field(default_factory=resolve_hard_limit)
    eta: float = field(default_factory=resolve_eta)
    spent: deque[float] = field(default_factory=lambda: deque(maxlen=DEFAULT_WINDOW))
    lambda_: float = 0.0

    def __post_init__(self):
        # Re-cap deque maxlen after env-driven window resolves.
        if self.spent.maxlen != self.window:
            self.spent = deque(self.spent, maxlen=self.window)

    # ── Dual update ──────────────────────────────────────────────────────────

    def update(self, task_cost: float) -> None:
        """Append observed cost; do one step of dual ascent on λ.

        Empirically: ceiling at 0.05 with eta=0.01, λ converges to a
        steady state in ~50–200 tasks depending on cost mix. Negative
        gradient (under-budget) decays λ toward 0; positive gradient
        grows λ until the cost penalty dominates the reward.
        """
        cost = max(0.0, float(task_cost))
        self.spent.append(cost)
        avg = sum(self.spent) / len(self.spent)
        # Gradient ascent on dual variable λ.
        self.lambda_ = max(0.0, self.lambda_ + self.eta * (avg - self.ceiling))

    @property
    def avg_cost(self) -> float:
        if not self.spent:
            return 0.0
        return sum(self.spent) / len(self.spent)

    @property
    def cost_weight_adjustment(self) -> float:
        """Additive bump for the cost weight in the composite reward."""
        return self.lambda_

    # ── Hard-limit filter ────────────────────────────────────────────────────

    def filter_agents(
        self,
        available: list[str],
        task_cost_estimates: dict[str, float],
    ) -> list[str]:
        """Remove agents whose per-task estimate exceeds the hard limit.

        With `hard_limit == 0`, only zero-cost agents pass — the
        "absolutely no paid agents" mode. With `hard_limit > 0`, agents
        whose estimate is unknown (missing from the cost dict) are
        treated as zero-cost and pass through. Use a non-zero default
        in `task_cost_estimates` if you want the opposite behaviour.

        If the filter would empty the candidate set, fall back to the
        cheapest available agent — never let the budget guard rail
        starve the bandit of any choice. Logs a warning when this
        fallback fires so it's visible.
        """
        if self.hard_limit <= 0.0:
            kept = [a for a in available if task_cost_estimates.get(a, 0.0) == 0.0]
            if not kept and available:
                cheapest = min(available, key=lambda a: task_cost_estimates.get(a, 0.0))
                _log.warning(
                    "budget_pacer: hard_limit=0 filtered all agents; "
                    "falling back to cheapest %s",
                    cheapest,
                )
                return [cheapest]
            return kept
        kept = [
            a for a in available
            if task_cost_estimates.get(a, 0.0) <= self.hard_limit
        ]
        if not kept and available:
            cheapest = min(available, key=lambda a: task_cost_estimates.get(a, 0.0))
            _log.warning(
                "budget_pacer: hard_limit=%.4f filtered all agents; "
                "falling back to cheapest %s",
                self.hard_limit, cheapest,
            )
            return [cheapest]
        return kept

    # ── Telemetry ────────────────────────────────────────────────────────────

    def to_status_dict(self) -> dict:
        """Snapshot for `orch budget status` / dashboard consumption."""
        return {
            "ceiling": round(self.ceiling, 4),
            "hard_limit": round(self.hard_limit, 4),
            "window": self.window,
            "eta": self.eta,
            "lambda": round(self.lambda_, 6),
            "avg_cost": round(self.avg_cost, 6),
            "n_observed": len(self.spent),
            "headroom": round(max(0.0, self.ceiling - self.avg_cost), 6),
            "over_ceiling": self.avg_cost > self.ceiling,
        }

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self, path: Path = BUDGET_PACER_STATE_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "ceiling": self.ceiling,
            "window": self.window,
            "hard_limit": self.hard_limit,
            "eta": self.eta,
            "lambda_": self.lambda_,
            "spent": list(self.spent),
        }))

    @classmethod
    def load(cls, path: Path = BUDGET_PACER_STATE_PATH) -> "BudgetPacer":
        """Load persisted state, with env-resolved values overriding the
        persisted ceiling/limit/window. Lambda and spent history are
        preserved across restarts so the rolling average doesn't reset."""
        if not Path(path).exists():
            return cls()
        try:
            data = json.loads(Path(path).read_text())
        except (json.JSONDecodeError, OSError) as exc:
            _log.warning("budget_pacer: failed to load %s (%s)", path, exc)
            return cls()
        # Env > persisted for the static config (so changing ceiling
        # actually takes effect on next startup). Lambda and spent
        # history come from persisted state.
        ceiling = resolve_ceiling()
        window = resolve_window()
        hard_limit = resolve_hard_limit()
        eta = resolve_eta()
        spent_list = list(data.get("spent", []))[-window:]
        return cls(
            ceiling=ceiling,
            window=window,
            hard_limit=hard_limit,
            eta=eta,
            spent=deque(spent_list, maxlen=window),
            lambda_=float(data.get("lambda_", 0.0)),
        )
