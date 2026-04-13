"""
Composite reward function for bandit routing.
Reward = success × (w_quality × quality + w_speed × speed_score + w_cost × cost_score)
"""

from dataclasses import dataclass


@dataclass
class TaskOutcome:
    success: bool
    latency_s: float
    cost_usd: float
    quality_score: float
    agent_name: str
    error_message: str = ""


class RewardCalculator:
    MAX_LATENCY: float = 60.0
    MAX_COST: float = 0.10

    def __init__(self, w_quality=0.4, w_speed=0.3, w_cost=0.3):
        total = w_quality + w_speed + w_cost
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Weights must sum to 1.0, got {total}")
        self.w_quality = w_quality
        self.w_speed = w_speed
        self.w_cost = w_cost

    def compute(self, outcome: TaskOutcome) -> float:
        if not outcome.success:
            return 0.0

        speed_score = 1.0 - min(outcome.latency_s / self.MAX_LATENCY, 1.0)
        cost_score = 1.0 - min(outcome.cost_usd / self.MAX_COST, 1.0)

        reward = (
            self.w_quality * outcome.quality_score
            + self.w_speed * speed_score
            + self.w_cost * cost_score
        )
        return round(max(0.0, min(1.0, reward)), 4)
