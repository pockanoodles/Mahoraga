"""
Composite reward function for bandit routing.

Reward = success × (w_success·1 + w_quality·quality + w_speed·φ_speed + w_cost·φ_cost)
       - swap_penalty

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

if TYPE_CHECKING:
    from .reward_learner import RewardWeightLearner


# ── Per-bucket reward weights (success, quality, speed, cost) ─────────────────
# Clamp each weight ≥ 0.05 to prevent collapse.
# These are informed priors — learnable via OLS regression after 100 tasks/bucket.
BUCKET_WEIGHTS: dict[str, tuple[float, float, float, float]] = {
    "code":     (0.60, 0.20, 0.15, 0.05),
    "research": (0.35, 0.45, 0.10, 0.10),
    "plan":     (0.40, 0.40, 0.10, 0.10),
    "security": (0.55, 0.35, 0.05, 0.05),
    "test":     (0.60, 0.25, 0.10, 0.05),
    "review":   (0.35, 0.50, 0.10, 0.05),
    "refactor": (0.45, 0.35, 0.15, 0.05),
    "debug":    (0.55, 0.25, 0.15, 0.05),
    "general":  (0.45, 0.25, 0.20, 0.10),
}

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


class RewardCalculator:
    """Computes a composite scalar reward in [0, 1] from a TaskOutcome.

    Optionally accepts a RewardWeightLearner so that per-bucket weights improve
    over time as task observations accumulate (OLS fit after 100 samples/bucket).
    """

    def __init__(self, learner: RewardWeightLearner | None = None) -> None:
        self._learner = learner

    def compute(self, outcome: TaskOutcome) -> float:
        """Return reward in [0.0, 1.0].  Returns 0.0 on failure."""
        if not outcome.success:
            return 0.0

        if self._learner is not None:
            w_s, w_q, w_sp, w_c = self._learner.get_weights(outcome.bucket)
        else:
            w_s, w_q, w_sp, w_c = BUCKET_WEIGHTS.get(outcome.bucket, BUCKET_WEIGHTS["general"])

        phi_speed = math.exp(-_SPEED_LAMBDA * outcome.latency_s / _SPEED_T_REF)
        phi_cost  = 1.0 - math.tanh(outcome.cost_usd / _COST_REF)

        raw = w_s * 1.0 + w_q * outcome.quality_score + w_sp * phi_speed + w_c * phi_cost

        # Swap penalty: if spawn_time_ms is substantial, agent was cold-loaded.
        # Normalise against the speed reference (5 s = 5000 ms).
        if outcome.spawn_time_ms > 500:   # only penalise non-trivial spawns
            swap_penalty = _BETA_SWAP * min(outcome.spawn_time_ms / (_SPEED_T_REF * 1000), 1.0)
            raw -= swap_penalty

        return round(max(0.0, min(1.0, raw)), 4)

    def attach_learner(self, learner: RewardWeightLearner) -> None:
        """Attach or replace the weight learner at runtime."""
        self._learner = learner
