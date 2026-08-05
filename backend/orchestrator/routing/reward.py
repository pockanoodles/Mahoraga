"""
Composite reward function for bandit routing.

Reward = success × (w_success·c + w_quality·quality + w_speed·φ_speed + w_cost·φ_cost)
       - swap_penalty

c = judge correctness coefficient in [0, 1]; 1.0 when no judge ran (correctness=None),
    so the reward is unchanged wherever the judge is off — see findings.md Era 20:
    the constant success term saturated at ~1.0 and left latency as the only gradient.

φ_speed = exp(-λ · latency_s / T_REF_S)   exponential decay against a fixed reference time
φ_cost  = 1 - tanh(cost_usd / COST_REF)   soft budget penalty

Per-bucket weights tune what matters most for each task type.
Swap penalty discounts the reward when the agent changed from the previous selection,
making the bandit aware of Ollama model-swap overhead.

If a RewardWeightLearner is attached, learned per-bucket weights override the static priors
once MIN_SAMPLES (100) observations have accumulated for that bucket.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .vocab import BUCKETS

if TYPE_CHECKING:
    from .budget_pacer import BudgetPacer
    from .reward_learner import RewardWeightLearner


# ── Per-bucket reward weights (success, quality, speed, cost) ─────────────────
# Clamp each weight ≥ 0.05 to prevent collapse.
# These are informed priors — learnable via OLS regression after 100 tasks/bucket.
BUCKET_WEIGHTS: dict[str, tuple[float, float, float, float]] = {
    "code":     (0.60, 0.20, 0.15, 0.05),
    "debug":    (0.55, 0.25, 0.15, 0.05),
    "plan":     (0.40, 0.40, 0.10, 0.10),
    "research": (0.35, 0.45, 0.10, 0.10),
    "review":   (0.35, 0.50, 0.10, 0.05),
    "refactor": (0.45, 0.35, 0.15, 0.05),
    "security": (0.55, 0.35, 0.05, 0.05),
    "test":     (0.60, 0.25, 0.10, 0.05),
    "general":  (0.45, 0.25, 0.20, 0.10),
}
assert set(BUCKET_WEIGHTS.keys()) == set(BUCKETS), (
    f"BUCKET_WEIGHTS keys out of sync with vocab.BUCKETS. "
    f"Missing: {set(BUCKETS) - set(BUCKET_WEIGHTS.keys())}. "
    f"Extra: {set(BUCKET_WEIGHTS.keys()) - set(BUCKETS)}."
)

# Speed reference: a 5 s response is "normal" for a local model.
# exp(-1 * 5/5) ≈ 0.37 — moderate penalty at reference time.
# exp(-1 * 1/5) ≈ 0.82 — good score for a fast 1 s response.
_SPEED_LAMBDA: float = 1.0
_SPEED_T_REF:  float = 5.0   # seconds

# Cost reference: soft-penalise above this threshold (tanh plateau).
_COST_REF: float = 0.05   # USD

# Swap penalty coefficient: subtract this fraction of normalised spawn time from reward.
_BETA_SWAP: float = 0.10


@dataclass
class TaskOutcome:
    """Outcome of a single task execution, used to compute bandit reward."""

    success: bool
    latency_s: float
    cost_usd: float
    quality_score: float
    agent_name: str
    error_message: str = ""
    bucket: str = "general"
    spawn_time_ms: float = 0.0   # agent_spawn_time_ms; used for swap penalty
    quality_components: dict[str, float | None] | None = None
    correctness: float | None = None   # reward-judge verdict; None = judge didn't run
    judge_cost: float = 0.0            # the judge call's own cost (telemetry only)
    judge_detail: str = ""             # judge provenance, e.g. a code-judge override reason


class RewardCalculator:
    """Computes a composite scalar reward in [0, 1] from a TaskOutcome.

    Optionally accepts a RewardWeightLearner so that per-bucket weights improve
    over time as task observations accumulate (OLS fit after 100 samples/bucket).
    """

    def __init__(
        self,
        learner: RewardWeightLearner | None = None,
        pacer: "BudgetPacer | None" = None,
    ) -> None:
        self._learner = learner
        self._pacer = pacer

    def compute(self, outcome: TaskOutcome) -> float:
        """Return reward in [0.0, 1.0].  Returns 0.0 on failure.

        F1 budget pacer: when attached, λ inflates the cost weight via
        `pacer.cost_weight_adjustment`. Effect: as rolling-average cost
        approaches the ceiling, the cost penalty grows, pushing the
        bandit toward cheaper agents without ever fully blocking
        quality optimisation.
        """
        if not outcome.success:
            return 0.0

        if self._learner is not None:
            w_s, w_q, w_sp, w_c = self._learner.get_weights(outcome.bucket)
        else:
            w_s, w_q, w_sp, w_c = BUCKET_WEIGHTS.get(outcome.bucket, BUCKET_WEIGHTS["general"])

        if self._pacer is not None:
            w_c = w_c + self._pacer.cost_weight_adjustment

        phi_speed = math.exp(-_SPEED_LAMBDA * outcome.latency_s / _SPEED_T_REF)
        phi_cost  = 1.0 - math.tanh(outcome.cost_usd / _COST_REF)

        # Judge correctness scales the success term (Era 20): None ≡ 1.0 keeps
        # the legacy reward exact wherever the judge doesn't run. success=False
        # already returned 0.0 above — a judge True never resurrects a crash.
        c = 1.0 if outcome.correctness is None else max(0.0, min(1.0, outcome.correctness))
        raw = w_s * c + w_q * outcome.quality_score + w_sp * phi_speed + w_c * phi_cost

        # Swap penalty: if spawn_time_ms is substantial, agent was cold-loaded.
        # Normalise against the speed reference (5 s = 5000 ms).
        if outcome.spawn_time_ms > 500:   # only penalise non-trivial spawns
            swap_penalty = _BETA_SWAP * min(outcome.spawn_time_ms / (_SPEED_T_REF * 1000), 1.0)
            raw -= swap_penalty

        return round(max(0.0, min(1.0, raw)), 4)

    def attach_learner(self, learner: RewardWeightLearner) -> None:
        """Attach or replace the weight learner at runtime."""
        self._learner = learner

    def attach_pacer(self, pacer: "BudgetPacer") -> None:
        """Attach or replace the budget pacer at runtime."""
        self._pacer = pacer
