"""Intervals on resolve rates, and how much more measuring a narrower one costs.

The unit of evidence is the task, not the attempt. Three repeats of one task
are not three independent draws from "tasks like this repo's": the same model
solved and missed the same task on consecutive runs, but its chance on *that*
task is one number. So a rate is the mean over tasks of each task's resolve
fraction, and its interval is a Wilson interval with n = tasks. Repeats make
each task's fraction more exact. They can't make the rate's interval narrower
than the number of tasks allows; only more tasks can. That is the planner's
main message.

Wilson rather than the normal approximation because these rates sit near 0
(granite: 0/18, qwen on ops: 1/9), where the normal interval collapses or
goes negative.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

Z95 = 1.959964


def wilson(k: float, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for k successes in n. `k` may be fractional (a sum
    of per-task resolve fractions)."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class Rate:
    point: float
    lo: float
    hi: float
    tasks: int
    attempts: int

    @property
    def half_width(self) -> float:
        return (self.hi - self.lo) / 2

    def fmt(self) -> str:
        return f"{self.point:.0%} [{self.lo:.0%}–{self.hi:.0%}]"


def task_rate(per_task: dict[str, list[bool]], z: float = Z95) -> Rate:
    """Task-weighted resolve rate and its 95% interval over tasks."""
    fractions = [sum(v) / len(v) for v in per_task.values() if v]
    n = len(fractions)
    k = sum(fractions)
    lo, hi = wilson(k, n, z)
    return Rate(point=k / n if n else 0.0, lo=lo, hi=hi, tasks=n,
                attempts=sum(len(v) for v in per_task.values()))


def tasks_needed(p: float, half_width: float, z: float = Z95, cap: int = 10_000) -> int:
    """Fewest tasks whose Wilson interval at rate `p` is no wider than ±half_width."""
    if not 0 < half_width < 0.5:
        raise ValueError("half_width must be in (0, 0.5)")
    for n in range(1, cap + 1):
        lo, hi = wilson(p * n, n, z)
        if (hi - lo) / 2 <= half_width:
            return n
    return cap


@dataclass(frozen=True)
class Plan:
    target_half_width: float
    tasks_needed: int
    tasks_have: int
    attempts_needed: int  # new attempts across all cells, one repeat each
    nights: int

    def fmt(self) -> str:
        if self.tasks_have >= self.tasks_needed:
            return (f"±{self.target_half_width:.0%} is within reach of the {self.tasks_have} "
                    f"tasks you have; repeats now only firm up per-task stability.")
        more = self.tasks_needed - self.tasks_have
        return (f"±{self.target_half_width:.0%} needs ~{self.tasks_needed} tasks; you have "
                f"{self.tasks_have}. Mine ~{more} more (another repo or a deeper history), "
                f"then ~{self.attempts_needed} attempts ≈ {self.nights} night(s). "
                f"More repeats of the same tasks will not get there.")


def plan(rate: Rate, half_width: float, minutes_per_attempt: float, cells: int,
         night_hours: float = 8.0) -> Plan:
    """What it takes to narrow `rate` to ±half_width.

    `cells` = arms × conditions that every new task is attempted under. The
    planning rate is the measured point, pulled to 0.5 when nothing is known
    yet (the widest case). Nights assume back-to-back attempts; the guard's
    pauses only lengthen it.
    """
    p = rate.point if rate.tasks else 0.5
    need = tasks_needed(p, half_width)
    new_attempts = max(0, need - rate.tasks) * cells
    per_night = max(1, int(night_hours * 60 // max(minutes_per_attempt, 0.1)))
    return Plan(half_width, need, rate.tasks, new_attempts,
                math.ceil(new_attempts / per_night) if new_attempts else 0)
