"""
reward_fidelity_replay.py — offline validation that the judge-fed correctness
coefficient (`TaskOutcome.correctness`, PR #34) restores the reward gradient
Era 20 found missing.

Era 20's live A/B showed the serving-path success signal saturating at ~1.0
("ran without crashing"), leaving latency as the only gradient: cold-start
LinUCB drifted to the faster arm and never beat round-robin. This replay
re-runs that cold-start protocol with ZERO new inference: the recorded P1
force-explore cross (every arm x every prompt) is re-graded against the
HumanEval+ bank's hidden tests via `verify_replay.run_case`, and a fresh
in-memory LinUCB plays the prompts under each reward variant —

- legacy:  correctness=None (the pre-fix reward, bit-exact by construction)
- oracle:  correctness = 1.0 iff the output truly passed the hidden tests
- judge-*: correctness sampled at the measured judge operating points
           (plain judge recall 0.688 / FPR 0.114, Era 19; code-judge
           recall 0.781 / FPR 0.144, Era 22), seeded RNG

Every variant is scored by the real `RewardCalculator` — the formula is never
re-implemented here. Complements `reweight_replay.py` (alt weights over logged
decisions) and `replay.py` (alt bandit configs over logged episodes); this one
varies the *success-term fidelity* over a recorded cross.

Environment notes (recorded-data caveats, stated up front):
- quality_score is not recorded in the cross files, so quality is held
  constant (QUALITY_CONST) across arms. The point of the experiment is the
  success-term gradient — the term the fix changes — not the quality term.
- cost_usd is 0.0: both arms are free local models, matching the recorded runs.
- latency: granite rows carry elapsed_s; most recovered qwen rows do not (109
  were salvaged from a scratch DB after a mid-run kill — journal 2026-08-03).
  Recorded per-prompt values are used where present (the qwen topup file
  supplies 53 timings) and the arm's mean recorded latency fills the rest,
  preserving the real latency gradient the legacy reward chased.
"""
from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from .context import TaskContext
from .reward import RewardCalculator, TaskOutcome
from .strategies.linucb import LinUCBRouter
from .verify_replay import load_bank, run_case

# Quality is unrecorded in the cross files — held constant across arms so the
# quality term contributes zero gradient (see module docstring).
QUALITY_CONST = 0.6

# ── Pass-criteria thresholds ──────────────────────────────────────────────────
COINFLIP_BAND = 0.15      # |disc_accuracy - 0.5| <= this counts as coin flip
RR_EPS = 0.005            # pass@1 within this of round-robin counts as "<= RR"
STATIC_MARGIN = 0.02      # "approaches best-static": within this or better
TRANSMISSION_FLOOR = 0.5  # judges must retain a majority of oracle's gain


# ── Reward variants ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Variant:
    """One success-term fidelity level fed to the real RewardCalculator."""

    name: str
    kind: str                      # "legacy" | "oracle" | "judge"
    recall: Optional[float] = None  # judge kind only: P(reject | truly failed)
    fpr: Optional[float] = None     # judge kind only: P(reject | truly passed)

    def accept_probability(self, passed: bool) -> float:
        """P(judge accepts) given ground truth — judge kind only."""
        assert self.kind == "judge" and self.recall is not None and self.fpr is not None
        return (1.0 - self.fpr) if passed else (1.0 - self.recall)

    def correctness(self, passed: bool, rng: random.Random) -> Optional[float]:
        """The `TaskOutcome.correctness` this variant feeds for one pull."""
        if self.kind == "legacy":
            return None
        if self.kind == "oracle":
            return 1.0 if passed else 0.0
        return 1.0 if rng.random() < self.accept_probability(passed) else 0.0

    def expected_correctness(self, passed: bool) -> Optional[float]:
        """E[correctness | ground truth] — for deterministic gap/fidelity stats."""
        if self.kind == "legacy":
            return None
        if self.kind == "oracle":
            return 1.0 if passed else 0.0
        return self.accept_probability(passed)


def default_variants(
    plain_recall: float = 0.688, plain_fpr: float = 0.114,
    code_recall: float = 0.781, code_fpr: float = 0.144,
) -> tuple[Variant, ...]:
    """Legacy + oracle + the two measured synthetic-judge operating points."""
    return (
        Variant("legacy", "legacy"),
        Variant("oracle", "oracle"),
        Variant("judge-plain", "judge", recall=plain_recall, fpr=plain_fpr),
        Variant("judge-code", "judge", recall=code_recall, fpr=code_fpr),
    )


# ── Environment: the (prompt x arm) matrix from recorded data ─────────────────


@dataclass
class ArmOutcome:
    """One recorded (prompt, arm) execution, re-graded offline."""

    agent: str
    bucket: str
    gate_success: bool       # the serving-path "ran without crashing" verdict
    passed: bool             # ground truth: extracted code passed hidden tests
    latency_s: float
    latency_recorded: bool   # False = backfilled with the arm's mean
    cost_usd: float = 0.0


@dataclass
class Environment:
    prompts: list[str]                          # bank order, full-matrix only
    arms: list[str]
    outcomes: dict[tuple[str, str], ArmOutcome]
    discriminating: list[str]                   # exactly one arm passes
    n_latency_backfilled: int
    n_dropped_incomplete: int                   # bank prompts missing an arm row

    def pass_rate(self, arm: str) -> float:
        ok = sum(1 for p in self.prompts if self.outcomes[(p, arm)].passed)
        return ok / len(self.prompts) if self.prompts else 0.0


def load_result_rows(paths: Sequence[Path]) -> list[dict]:
    """Bench-results rows keeping the timing/gate columns the reward needs.

    Same joining conventions as `verify_replay.load_results` (prompt_full,
    output_full, actual_agent fallbacks; `#` lines skipped), plus `success`
    (the recorded gate verdict) and `elapsed_s` (None where the file predates
    timing or the row was recovered without it).
    """
    rows: list[dict] = []
    for path in paths:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            prompt = r.get("prompt_full") or r.get("prompt")
            agent = r.get("actual_agent") or r.get("requested_agent") or r.get("agent")
            if not prompt or not agent:
                continue
            elapsed = r.get("elapsed_s")
            rows.append({
                "prompt": prompt,
                "agent": agent,
                "output": r.get("output_full") or "",
                "bucket": r.get("bucket") or "general",
                "success": bool(r.get("success", True)),
                "elapsed_s": float(elapsed) if elapsed is not None else None,
            })
    return rows


def build_environment(bank_path: Path, results_paths: Sequence[Path]) -> Environment:
    """Grade the recorded cross into a per-(prompt, arm) outcome matrix.

    Duplicate (prompt, arm) rows are merged with a coherence rule: a row that
    carries recorded timing wins wholesale (its output and elapsed_s come from
    the same execution), otherwise first-seen wins. Only bank prompts where
    EVERY arm has a row enter the environment — the bandit needs a full matrix.
    """
    bank = load_bank(bank_path)
    rows = load_result_rows(results_paths)

    best: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r["prompt"] not in bank:
            continue
        key = (r["prompt"], r["agent"])
        cur = best.get(key)
        if cur is None or (cur["elapsed_s"] is None and r["elapsed_s"] is not None):
            best[key] = r

    arms = sorted({a for _p, a in best})
    outcomes: dict[tuple[str, str], ArmOutcome] = {}
    for (prompt, agent), r in best.items():
        passed, _err = run_case(r["output"], bank[prompt]["tests"])
        outcomes[(prompt, agent)] = ArmOutcome(
            agent=agent,
            bucket=r["bucket"],
            gate_success=r["success"],
            passed=passed,
            latency_s=r["elapsed_s"] if r["elapsed_s"] is not None else 0.0,
            latency_recorded=r["elapsed_s"] is not None,
        )

    # Backfill missing latency with the arm's mean recorded latency.
    n_backfilled = 0
    for arm in arms:
        recorded = [o.latency_s for o in outcomes.values()
                    if o.agent == arm and o.latency_recorded]
        if not recorded:
            raise ValueError(
                f"No recorded latency for arm {arm!r} in any results file — "
                "cannot backfill without inventing a number. Add a results "
                "file that carries elapsed_s for this arm."
            )
        mean_latency = sum(recorded) / len(recorded)
        for o in outcomes.values():
            if o.agent == arm and not o.latency_recorded:
                o.latency_s = mean_latency
                n_backfilled += 1

    prompts = [p for p in bank if all((p, a) in outcomes for a in arms)]
    discriminating = [
        p for p in prompts
        if sum(1 for a in arms if outcomes[(p, a)].passed) == 1
    ]
    return Environment(
        prompts=prompts,
        arms=arms,
        outcomes=outcomes,
        discriminating=discriminating,
        n_latency_backfilled=n_backfilled,
        n_dropped_incomplete=len(bank) - len(prompts),
    )


# ── Variant scoring (always through the real RewardCalculator) ────────────────


def score_outcome(
    calc: RewardCalculator, outcome: ArmOutcome, correctness: Optional[float]
) -> float:
    """One pull's reward — the real compute(), never a re-implementation."""
    return calc.compute(TaskOutcome(
        success=outcome.gate_success,
        latency_s=outcome.latency_s,
        cost_usd=outcome.cost_usd,
        quality_score=QUALITY_CONST,
        agent_name=outcome.agent,
        bucket=outcome.bucket,
        correctness=correctness,
    ))


def arm_reward_summary(env: Environment, variant: Variant) -> dict:
    """Deterministic per-arm mean reward under E[correctness], plus the gap.

    Judge variants use the analytic expected correctness rather than a sample
    so the gap statistic is stable; the reward is linear in the coefficient,
    so this equals the mean of sampled rewards up to clamping/rounding.
    """
    calc = RewardCalculator()
    means: dict[str, float] = {}
    for arm in env.arms:
        vals = [
            score_outcome(calc, env.outcomes[(p, arm)],
                          variant.expected_correctness(env.outcomes[(p, arm)].passed))
            for p in env.prompts
        ]
        means[arm] = sum(vals) / len(vals) if vals else 0.0
    leader = max(means, key=means.get) if means else ""
    gap = (max(means.values()) - min(means.values())) if len(means) > 1 else 0.0
    return {"mean_reward": {a: round(v, 4) for a, v in means.items()},
            "gap": round(gap, 4), "leader": leader}


def reward_pass_correlation(env: Environment, variant: Variant) -> Optional[float]:
    """Pearson r between per-(prompt, arm) reward and true pass — the fidelity
    headline: does the reward actually measure correctness under this variant?
    """
    calc = RewardCalculator()
    rewards, passes = [], []
    for p in env.prompts:
        for a in env.arms:
            o = env.outcomes[(p, a)]
            rewards.append(score_outcome(calc, o, variant.expected_correctness(o.passed)))
            passes.append(1.0 if o.passed else 0.0)
    if len(rewards) < 3:
        return None
    r = np.asarray(rewards)
    y = np.asarray(passes)
    if r.std() == 0.0 or y.std() == 0.0:
        return None
    return round(float(np.corrcoef(r, y)[0, 1]), 4)


# ── Bandit simulation ─────────────────────────────────────────────────────────


@dataclass
class SimResult:
    variant: str
    n_orderings: int
    pass_at_1: float            # mean over orderings of the policy's true pass@1
    pass_at_1_std: float
    pick_share: dict[str, float]
    disc_accuracy: Optional[float]  # picked the passing arm on discriminating prompts
    n_discriminating: int
    reward_gap: float           # deterministic arm mean-reward gap (expected correctness)
    reward_leader: str
    arm_mean_reward: dict[str, float]
    reward_pass_corr: Optional[float]

    def as_dict(self) -> dict:
        return {
            "variant": self.variant,
            "n_orderings": self.n_orderings,
            "pass_at_1": round(self.pass_at_1, 4),
            "pass_at_1_std": round(self.pass_at_1_std, 4),
            "pick_share": {a: round(v, 4) for a, v in self.pick_share.items()},
            "disc_accuracy": round(self.disc_accuracy, 4) if self.disc_accuracy is not None else None,
            "n_discriminating": self.n_discriminating,
            "reward_gap": self.reward_gap,
            "reward_leader": self.reward_leader,
            "arm_mean_reward": self.arm_mean_reward,
            "reward_pass_corr": self.reward_pass_corr,
        }


def _fresh_bandit(arms: Sequence[str], *, alpha: float, decay: float) -> LinUCBRouter:
    """Cold-start LinUCB with every arm symmetrically initialised at t=0.

    Arms are registered before the first pull via a zero-weight pseudo-obs
    (numerically a no-op) so each takes the t=0 cold-start path. Without
    this, the second arm would hit `_init_agent`'s mid-run average-init +
    compatibility-matrix warm start — which reads live ~/.mahoraga-v2 state
    the replay must not depend on.
    """
    bandit = LinUCBRouter(d=9, alpha=alpha, decay=decay, priors={})
    zero = np.zeros(9)
    for a in arms:
        bandit.inject_pseudo_obs(a, zero, 0.0, lambda_prior=0.0)
    return bandit


def simulate_variant(
    env: Environment,
    variant: Variant,
    *,
    n_orderings: int = 20,
    seed: int = 42,
    alpha: float = 1.0,
    decay: float = 0.98,
) -> SimResult:
    """Cold-start LinUCB over N shuffled orderings, one pull per prompt each.

    Every ordering gets a fresh bandit (Era 20's cold-start protocol) and its
    own seeded RNG stream (shuffle + synthetic-judge sampling), so results are
    deterministic under a fixed seed. Contexts come from the real 9-dim
    `TaskContext` featureizer over the prompt text (queue depth 0 offline).
    """
    contexts = {p: TaskContext.from_task({"goal": p}) for p in env.prompts}
    disc_set = set(env.discriminating)
    calc = RewardCalculator()

    per_ordering_pass: list[float] = []
    picks: Counter[str] = Counter()
    disc_correct = 0
    disc_total = 0

    for k in range(n_orderings):
        rng = random.Random(seed * 1_000_003 + k)
        order = list(env.prompts)
        rng.shuffle(order)
        bandit = _fresh_bandit(env.arms, alpha=alpha, decay=decay)
        hits = 0
        for p in order:
            arm = bandit.select_agent(contexts[p], env.arms)
            outcome = env.outcomes[(p, arm)]
            reward = score_outcome(calc, outcome, variant.correctness(outcome.passed, rng))
            bandit.update(contexts[p], arm, reward)
            hits += int(outcome.passed)
            picks[arm] += 1
            if p in disc_set:
                disc_total += 1
                disc_correct += int(outcome.passed)
        per_ordering_pass.append(hits / len(order) if order else 0.0)

    total_picks = sum(picks.values())
    mean_pass = sum(per_ordering_pass) / len(per_ordering_pass) if per_ordering_pass else 0.0
    var = (
        sum((x - mean_pass) ** 2 for x in per_ordering_pass) / len(per_ordering_pass)
        if per_ordering_pass else 0.0
    )
    gap = arm_reward_summary(env, variant)
    return SimResult(
        variant=variant.name,
        n_orderings=n_orderings,
        pass_at_1=mean_pass,
        pass_at_1_std=var ** 0.5,
        pick_share={a: picks.get(a, 0) / total_picks for a in env.arms} if total_picks else {},
        disc_accuracy=(disc_correct / disc_total) if disc_total else None,
        n_discriminating=len(env.discriminating),
        reward_gap=gap["gap"],
        reward_leader=gap["leader"],
        arm_mean_reward=gap["mean_reward"],
        reward_pass_corr=reward_pass_correlation(env, variant),
    )


# ── Baselines (derived exactly from the matrix, as in Era 20) ─────────────────


def baselines(env: Environment) -> dict:
    """Round-robin / statics / oracle-router pass@1 from the same matrix.

    Round-robin over a shuffled order is, in expectation, the mean of the
    arms' pass rates; statics and the per-prompt oracle are exact.
    """
    static = {a: env.pass_rate(a) for a in env.arms}
    best_arm = max(static, key=static.get) if static else ""
    oracle_router = (
        sum(1 for p in env.prompts if any(env.outcomes[(p, a)].passed for a in env.arms))
        / len(env.prompts) if env.prompts else 0.0
    )
    return {
        "round_robin": round(sum(static.values()) / len(static), 4) if static else 0.0,
        "static": {a: round(v, 4) for a, v in static.items()},
        "best_static_arm": best_arm,
        "best_static": round(static[best_arm], 4) if static else 0.0,
        "oracle_router": round(oracle_router, 4),
    }


# ── Pass criteria ─────────────────────────────────────────────────────────────


def evaluate_criteria(sims: dict[str, SimResult], base: dict) -> list[dict]:
    """PASS/FAIL per replay criterion (N/A where the premise is degenerate).

    1. legacy reproduces Era 20: disc-accuracy ~ coin flip and/or policy
       pass@1 <= round-robin.
    2. oracle beats round-robin AND approaches best-static (within
       STATIC_MARGIN) or better.
    3. each synthetic judge retains >= TRANSMISSION_FLOOR of the oracle's
       pass@1 improvement over legacy (theory: transmission ~ recall - FPR).
    """
    rr = base["round_robin"]
    out: list[dict] = []

    legacy = sims["legacy"]
    coinflip = (
        legacy.disc_accuracy is not None
        and abs(legacy.disc_accuracy - 0.5) <= COINFLIP_BAND
    )
    not_better = legacy.pass_at_1 <= rr + RR_EPS
    out.append({
        "criterion": "legacy reproduces Era 20 (disc-acc ~ coin flip and/or pass@1 <= round-robin)",
        "verdict": "PASS" if (coinflip or not_better) else "FAIL",
        "detail": (
            f"disc-acc={legacy.disc_accuracy:.3f} (coin-flip band 0.5±{COINFLIP_BAND}), "
            f"pass@1={legacy.pass_at_1:.4f} vs RR={rr:.4f}"
        ),
    })

    oracle = sims["oracle"]
    beats_rr = oracle.pass_at_1 > rr
    near_static = oracle.pass_at_1 >= base["best_static"] - STATIC_MARGIN
    out.append({
        "criterion": "oracle beats round-robin and approaches best-static or better",
        "verdict": "PASS" if (beats_rr and near_static) else "FAIL",
        "detail": (
            f"pass@1={oracle.pass_at_1:.4f} vs RR={rr:.4f} and "
            f"best-static={base['best_static']:.4f} ({base['best_static_arm']}, "
            f"margin {STATIC_MARGIN})"
        ),
    })

    oracle_gain = oracle.pass_at_1 - legacy.pass_at_1
    for name, sim in sims.items():
        if name in ("legacy", "oracle"):
            continue
        judge_gain = sim.pass_at_1 - legacy.pass_at_1
        if oracle_gain <= 0:
            verdict, transmission = "N/A", None
            detail = (
                f"oracle gain over legacy is non-positive ({oracle_gain:+.4f}) — "
                f"no improvement to transmit; judge gain {judge_gain:+.4f}"
            )
        else:
            transmission = judge_gain / oracle_gain
            verdict = "PASS" if transmission >= TRANSMISSION_FLOOR else "FAIL"
            detail = (
                f"transmission={transmission:.2f} of oracle gain "
                f"(judge {judge_gain:+.4f} / oracle {oracle_gain:+.4f}, "
                f"floor {TRANSMISSION_FLOOR})"
            )
        out.append({
            "criterion": f"{name} retains a majority of the oracle improvement",
            "verdict": verdict,
            "detail": detail,
        })
    return out


# ── Full replay entrypoint ────────────────────────────────────────────────────


def run_replay(
    bank_path: Path,
    results_paths: Sequence[Path],
    *,
    variants: Optional[Sequence[Variant]] = None,
    n_orderings: int = 20,
    seed: int = 42,
    alpha: float = 1.0,
    decay: float = 0.98,
) -> dict:
    """Build the environment, simulate every variant, evaluate the criteria."""
    env = build_environment(bank_path, results_paths)
    sims = {
        v.name: simulate_variant(
            env, v, n_orderings=n_orderings, seed=seed, alpha=alpha, decay=decay
        )
        for v in (variants or default_variants())
    }
    base = baselines(env)
    return {
        "environment": {
            "n_prompts": len(env.prompts),
            "arms": env.arms,
            "n_discriminating": len(env.discriminating),
            "n_latency_backfilled": env.n_latency_backfilled,
            "n_dropped_incomplete": env.n_dropped_incomplete,
        },
        "baselines": base,
        "variants": {name: s.as_dict() for name, s in sims.items()},
        "criteria": evaluate_criteria(sims, base),
    }
