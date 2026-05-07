"""
Off-policy correction for composer overrides.

Spec: docs/v2-remaining-work.md §A1.

When the cross-axis composer overrides the bandit's pick (e.g. flips
codex-cli → ollama because A3 strongly disagrees), the bandit's update
otherwise treats it as if it had freely chosen the override agent.
That distorts learning: the override agent's A/b matrices accumulate
credit for decisions the bandit didn't make, while the bandit's true
preference signal gets diluted.

The fix is a textbook importance-weighted off-policy correction:

    w = P_bandit(final_agent) / P_composer(final_agent)

`P_composer(final_agent) = 1.0` because the composer's policy is
deterministic on its chosen agent, so the ratio simplifies to
`P_bandit(final_agent)`. When the composer didn't override (final_agent
== bandit_pick), we treat w = 1.0 so behaviour is identical to the
no-composer case.

`P_bandit(a)` is derived from softmax over the bandit's UCB scores:

    P_bandit(a) = exp(UCB_a / τ) / Σ_i exp(UCB_i / τ)

τ (temperature) auto-scales as the standard deviation of the UCB scores
across available agents. When scores are tightly clustered (bandit is
uncertain), τ is small, probabilities are near-uniform, and override
weights are moderate. When one agent dominates (bandit is confident),
τ is large, probabilities are peaked, and the override weight is small —
the bandit barely learns from a decision it strongly disagreed with,
which is correct.

A small floor (`WEIGHT_FLOOR`) prevents pathological zero updates when
the bandit had near-zero probability on the override agent. Without it,
a long string of A3-driven overrides could leave the override agent's
matrices completely unlearned.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

# Floor on the importance weight: w < this gets clamped up so we still
# learn something from a strongly-disagreed-with override. Empirically
# small enough that the bandit's preference still dominates.
WEIGHT_FLOOR = 1e-3

# Floor on the auto-scaled temperature. Without this floor, tight UCB
# clusters (e.g. (0.51, 0.49)) would compute τ ≈ 0.01, making the
# softmax extremely peaked — exactly the OPPOSITE of the spec's intent
# ("tight scores → near-uniform probs"). The floor is calibrated to
# Mahoraga's typical UCB range (roughly [0, 3] with α=1.0): at τ=0.05,
# a 0.02 score gap produces ~60/40 probabilities (close-call), while
# a 10× spread saturates near 0/100/0/... (clear dominance). Tunable.
TEMPERATURE_FLOOR = 0.05


def auto_temperature(ucb_values: Iterable[float], floor: float = TEMPERATURE_FLOOR) -> float:
    """Standard deviation of UCB scores, with a calibration floor.

    Returns std(ucb_values) clamped at `floor`. The floor is the load-
    bearing piece — pure std-based τ would inflate the logit ratio for
    tightly-clustered scores, producing peaked probabilities for cases
    where the bandit was nearly indifferent. The floor flattens those.
    """
    vals = list(ucb_values)
    if len(vals) < 2:
        return max(floor, 1.0)
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    sd = math.sqrt(var)
    return max(sd, floor)


def bandit_probs_from_scores(
    scores: dict[str, dict],
    *,
    temperature: Optional[float] = None,
) -> dict[str, float]:
    """Softmax over UCB scores → P_bandit(a) for each available agent.

    `scores` is the per-agent dict produced by RoutingStrategy.compute_scores
    (each entry has 'ucb' / 'exploit' / 'explore' / 'variance'). Returns
    a probability dict that sums to 1.0 over the input keys. Returns {}
    on empty input.
    """
    if not scores:
        return {}
    ucb_vals = [float(s.get("ucb", s.get("exploit", 0.0))) for s in scores.values()]
    if temperature is None:
        temperature = auto_temperature(ucb_vals)
    # Numerically stable softmax.
    max_logit = max(ucb_vals)
    exps = [math.exp(min((v - max_logit) / temperature, 30.0)) for v in ucb_vals]
    z = sum(exps)
    if z <= 0:
        # Degenerate; fall back to uniform.
        n = len(scores)
        return {a: 1.0 / n for a in scores}
    return {a: exps[i] / z for i, a in enumerate(scores)}


def importance_weight(
    *,
    bandit_pick: str,
    final_agent: str,
    scores: dict[str, dict],
    floor: float = WEIGHT_FLOOR,
) -> float:
    """Compute the off-policy weight for the bandit update.

    Returns 1.0 in the standard case (no override). Returns
    P_bandit(final_agent), clamped to ≥ floor, when the composer
    overrode the bandit. Returns 1.0 if scores are empty (graceful
    degradation when a strategy doesn't expose scores).
    """
    if final_agent == bandit_pick:
        return 1.0
    if not scores:
        return 1.0
    probs = bandit_probs_from_scores(scores)
    p = probs.get(final_agent)
    if p is None:
        # final_agent isn't even in the scored set — extreme override
        # (e.g. composer reached for a fallback). Use the floor.
        return floor
    return max(p, floor)
