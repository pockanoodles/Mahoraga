"""
Paraphrase-transfer benchmark for A1 (spec §15.6).

Tests the actual A1 hypothesis: semantic episodic retrieval should generalise
from observed prompts to *paraphrases* (same meaning, different surface
keywords) better than keyword retrieval can.

Two phases per condition:
  1. Train phase: replay each `training_prompt` `train_repeats` times, in
     shuffled order. The bandit and memory accumulate state. observe()
     fires → LinUCB updates + memory gets episodes.
  2. Test phase: present each `test_paraphrase` exactly once. route() runs
     but observe() does NOT — no further learning. We measure routing
     accuracy on the held-out paraphrases.

The split is by *prompt*, not by random shuffle: training prompts and test
paraphrases never overlap in surface form. They share *meaning* and
oracle_agent. A semantic retrieval system should infer the correct agent
on test paraphrases by retrieving the trained episodes via embedding
similarity.

Modes compared:
  - off:      no memory bias on read; tests purely the bandit's own
              generalisation via 9-dim handcraft features.
  - keyword:  9-dim handcraft retrieval; should retrieve training prompts
              when keywords overlap with test paraphrases (often weakly).
  - semantic: 384-dim embedding retrieval; should retrieve training
              prompts via meaning, regardless of surface form.

A win for A1 on this benchmark requires: semantic mode achieves higher
test-phase accuracy than keyword and off modes, with a margin that
exceeds 2×SE under N=10 seeds.
"""
from __future__ import annotations

import json
import math
import os
import random
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .oracle import AGENT_COST, AGENT_LATENCY


# ── Pair / prompt loading ─────────────────────────────────────────────────────


@dataclass
class ParaphrasePair:
    pair_id: int
    theme: str
    training_prompt: str
    test_paraphrases: list[str]
    oracle_agent: str
    oracle_reward: float


def load_pairs(path: Path) -> list[ParaphrasePair]:
    data = json.loads(Path(path).read_text())
    return [
        ParaphrasePair(
            pair_id=p["id"],
            theme=p.get("theme", ""),
            training_prompt=p["training_prompt"],
            test_paraphrases=list(p["test_paraphrases"]),
            oracle_agent=p["oracle_agent"],
            oracle_reward=float(p["oracle_reward"]),
        )
        for p in data["pairs"]
    ]


# ── Reward simulation (shared with memory_mode_eval) ─────────────────────────


def _simulate_reward(
    selected: str,
    oracle_agent: str,
    oracle_reward: float,
    rng: random.Random,
    wrong_pick_factor: float = 0.50,
    noise_std: float = 0.04,
) -> float:
    base = oracle_reward if selected == oracle_agent else oracle_reward * wrong_pick_factor
    return max(0.0, min(1.0, base + rng.gauss(0.0, noise_std)))


def _outcome_for(
    pair: ParaphrasePair,
    selected: str,
    rng: random.Random,
):
    from backend.orchestrator.routing.reward import TaskOutcome

    actual_reward = _simulate_reward(
        selected, pair.oracle_agent, pair.oracle_reward, rng,
    )
    lat_mean, lat_std = AGENT_LATENCY.get(selected, (3.0, 1.0))
    latency_s = max(0.5, rng.gauss(lat_mean, lat_std))
    cost_usd = AGENT_COST.get(selected, 0.0)
    return TaskOutcome(
        success=True,
        latency_s=latency_s,
        cost_usd=cost_usd,
        quality_score=actual_reward,
        agent_name=selected,
    ), actual_reward


# ── Test result ──────────────────────────────────────────────────────────────


@dataclass
class TestResult:
    pair_id: int
    paraphrase: str
    oracle_agent: str
    selected_agent: str
    is_correct: bool
    actual_reward: float


@dataclass
class ConditionResult:
    mode: str
    seed: int
    alpha: float
    confidence_weighted: bool
    train_reward: float          # cumulative reward during train phase
    test_reward: float           # cumulative reward during test phase
    test_correct: int            # # test paraphrases routed to oracle agent
    test_total: int              # # test paraphrases evaluated
    test_results: list[TestResult] = field(default_factory=list)

    @property
    def test_accuracy(self) -> float:
        return self.test_correct / self.test_total if self.test_total else 0.0

    @property
    def condition_id(self) -> str:
        suffix = "+conf" if self.confidence_weighted else ""
        return f"{self.mode}@α={self.alpha:.2f}{suffix}"


class _MockRegistry:
    def __init__(self, agents: list[str]) -> None:
        self._agents = agents

    def all(self):
        return [type("A", (), {"name": a})() for a in self._agents]


# ── Single-condition runner ──────────────────────────────────────────────────


def run_condition(
    pairs: list[ParaphrasePair],
    mode: str,
    seed: int,
    state_dir: Path,
    cache_path: Path,
    agents: list[str],
    train_repeats: int = 6,
    alpha: float = 0.20,
    confidence_weighted: bool = False,
) -> ConditionResult:
    """Run one (mode, seed) condition through the full train→test pipeline."""
    os.environ["MAHORAGA_MEMORY_MODE"] = mode
    os.environ["MAHORAGA_MEMORY_ALPHA"] = f"{alpha:.4f}"
    os.environ["MAHORAGA_MEMORY_CONFIDENCE_WEIGHTED"] = (
        "true" if confidence_weighted else "false"
    )
    os.environ.pop("MAHORAGA_MEMORY_ALPHA_PER_BUCKET", None)
    os.environ["MAHORAGA_BANDIT_SEED"] = str(seed)
    os.environ["MAHORAGA_PROMPT_SEED"] = str(seed)

    state_dir.mkdir(parents=True, exist_ok=True)

    from backend.orchestrator.routing import BanditRouter
    from backend.orchestrator.routing.embeddings import EmbeddingService
    from backend.orchestrator.routing.decision_log import DecisionLogger

    logger = DecisionLogger(db_path=state_dir / "decisions.db")
    router = BanditRouter(
        strategy="linucb",
        registry=_MockRegistry(agents),
        logger=logger,
        state_path=state_dir / "bandit_state.json",
    )
    router._embedding_service = EmbeddingService(cache_path=cache_path)
    router._embedding_init_attempted = True

    rng = random.Random(seed)

    # ── Train phase ──────────────────────────────────────────────────────────
    train_order: list[int] = list(range(len(pairs))) * max(1, int(train_repeats))
    rng.shuffle(train_order)

    train_reward = 0.0
    for idx in train_order:
        pair = pairs[idx]
        task = type(
            "T", (),
            {"goal": pair.training_prompt, "id": f"train-{seed}-{idx}"},
        )()
        selected = router.route(task, available_agents=agents)
        outcome, actual = _outcome_for(pair, selected, rng)
        router.observe(task, outcome)
        train_reward += actual

    # ── Test phase ──────────────────────────────────────────────────────────
    # Each pair's paraphrases shown ONCE, in shuffled order.
    test_items: list[tuple[int, str]] = []
    for i, pair in enumerate(pairs):
        for paraphrase in pair.test_paraphrases:
            test_items.append((i, paraphrase))
    rng.shuffle(test_items)

    test_reward = 0.0
    test_correct = 0
    test_results: list[TestResult] = []

    for pair_idx, paraphrase in test_items:
        pair = pairs[pair_idx]
        task = type(
            "T", (),
            {"goal": paraphrase, "id": f"test-{seed}-{pair.pair_id}"},
        )()
        selected = router.route(task, available_agents=agents)

        # Actual reward — what the agent would have scored. Used for the
        # `test_reward` aggregate even though the bandit doesn't observe it.
        actual = _simulate_reward(
            selected, pair.oracle_agent, pair.oracle_reward, rng,
        )
        is_correct = selected == pair.oracle_agent
        if is_correct:
            test_correct += 1
        test_reward += actual

        test_results.append(TestResult(
            pair_id=pair.pair_id,
            paraphrase=paraphrase,
            oracle_agent=pair.oracle_agent,
            selected_agent=selected,
            is_correct=is_correct,
            actual_reward=actual,
        ))
        # CRUCIALLY: do NOT call router.observe() in the test phase. We're
        # measuring transfer; observe() would let the bandit learn from the
        # test, which would contaminate the comparison.

    logger.close()

    return ConditionResult(
        mode=mode,
        seed=seed,
        alpha=alpha,
        confidence_weighted=confidence_weighted,
        train_reward=train_reward,
        test_reward=test_reward,
        test_correct=test_correct,
        test_total=len(test_items),
        test_results=test_results,
    )


# ── Aggregation ──────────────────────────────────────────────────────────────


def _ci95(values: list[float]) -> tuple[float, float]:
    if len(values) < 2:
        return (0.0, 0.0)
    mean = statistics.mean(values)
    std = statistics.stdev(values)
    n = len(values)
    se = std / math.sqrt(n)
    t_table = {
        2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
        7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262, 15: 2.131,
        20: 2.086, 30: 2.042,
    }
    t_crit = t_table.get(n, 1.96)
    margin = t_crit * se
    return (mean - margin, mean + margin)


def aggregate(results: list[ConditionResult]) -> dict[str, Any]:
    by_cond: dict[str, list[ConditionResult]] = {}
    for r in results:
        by_cond.setdefault(r.condition_id, []).append(r)

    summary: dict[str, Any] = {"by_condition": {}}
    for cond, runs in by_cond.items():
        accuracies = [r.test_accuracy for r in runs]
        rewards = [r.test_reward for r in runs]
        summary["by_condition"][cond] = {
            "mode": runs[0].mode,
            "alpha": runs[0].alpha,
            "confidence_weighted": runs[0].confidence_weighted,
            "n_seeds": len(runs),
            "test_accuracy": {
                "mean": statistics.mean(accuracies),
                "std": statistics.stdev(accuracies) if len(accuracies) > 1 else 0.0,
                "ci95": _ci95(accuracies),
                "values": accuracies,
            },
            "test_reward": {
                "mean": statistics.mean(rewards),
                "std": statistics.stdev(rewards) if len(rewards) > 1 else 0.0,
                "ci95": _ci95(rewards),
                "values": rewards,
            },
        }

    # Pairwise vs off-baseline.
    summary["pairwise"] = {}
    off_baseline = next(
        (cond for cond, b in summary["by_condition"].items()
         if b["mode"] == "off"),
        None,
    )
    if off_baseline:
        off_acc = summary["by_condition"][off_baseline]["test_accuracy"]
        off_mean = off_acc["mean"]
        off_std = off_acc["std"]
        for cond, b in summary["by_condition"].items():
            if cond == off_baseline:
                continue
            bm = b["test_accuracy"]["mean"]
            bs = b["test_accuracy"]["std"]
            n = b["n_seeds"]
            pooled_se = math.sqrt(bs**2 / n + off_std**2 / n) if n > 0 else 0.0
            t = (bm - off_mean) / pooled_se if pooled_se > 0 else 0.0
            summary["pairwise"][f"{cond}_vs_{off_baseline}"] = {
                "delta_accuracy": bm - off_mean,
                "approx_t_statistic": t,
                "rough_significant_p05": abs(t) > 2.1,
            }

    return summary


# ── Top-level orchestrator ───────────────────────────────────────────────────


def run_eval(
    pairs: list[ParaphrasePair],
    modes: list[str],
    seeds: list[int],
    result_dir: Path,
    agents: Optional[list[str]] = None,
    train_repeats: int = 6,
    alphas: Optional[list[float]] = None,
    confidence_weighting: Optional[list[bool]] = None,
) -> dict[str, Any]:
    if agents is None:
        agents = ["ollama", "codex-cli", "aider", "gemini-cli", "claude"]
    if alphas is None:
        from backend.orchestrator.routing.episodic_memory import MEMORY_ALPHA
        alphas = [MEMORY_ALPHA]
    if confidence_weighting is None:
        confidence_weighting = [False]

    result_dir = Path(result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    all_results: list[ConditionResult] = []
    raw: list[dict[str, Any]] = []

    for mode in modes:
        mode_alphas = [0.0] if mode == "off" else alphas
        mode_confs = [False] if mode == "off" else confidence_weighting
        for alpha in mode_alphas:
            for cw in mode_confs:
                cond_label = (
                    f"{mode}_a{alpha:.2f}" + ("_cw" if cw else "")
                )
                for seed in seeds:
                    state_dir = (
                        result_dir / "_runs" / f"{cond_label}_seed{seed}"
                    )
                    cache_path = (
                        result_dir / "_runs" / "shared_emb_cache.sqlite"
                    )
                    res = run_condition(
                        pairs=pairs, mode=mode, seed=seed,
                        state_dir=state_dir, cache_path=cache_path,
                        agents=agents, train_repeats=train_repeats,
                        alpha=alpha, confidence_weighted=cw,
                    )
                    all_results.append(res)
                    raw.append({
                        "condition_id": res.condition_id,
                        "mode": res.mode,
                        "alpha": res.alpha,
                        "confidence_weighted": res.confidence_weighted,
                        "seed": res.seed,
                        "train_reward": res.train_reward,
                        "test_reward": res.test_reward,
                        "test_accuracy": res.test_accuracy,
                        "test_results": [
                            {
                                "pair_id": t.pair_id,
                                "paraphrase": t.paraphrase,
                                "oracle_agent": t.oracle_agent,
                                "selected_agent": t.selected_agent,
                                "is_correct": t.is_correct,
                                "actual_reward": t.actual_reward,
                            }
                            for t in res.test_results
                        ],
                    })

    summary = aggregate(all_results)
    summary["elapsed_seconds"] = time.time() - started
    summary["n_pairs"] = len(pairs)
    summary["n_test_paraphrases"] = sum(len(p.test_paraphrases) for p in pairs)
    summary["n_seeds"] = len(seeds)
    summary["train_repeats"] = train_repeats
    summary["modes"] = modes
    summary["alphas"] = alphas
    summary["confidence_weighting"] = confidence_weighting
    summary["agents"] = agents

    (result_dir / "raw_results.json").write_text(json.dumps(raw, indent=2))
    (result_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (result_dir / "summary.md").write_text(_markdown_summary(summary))

    return summary


def _markdown_summary(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Paraphrase-Transfer Benchmark — A1 hypothesis test")
    lines.append("")
    lines.append(
        f"**Pairs**: {summary['n_pairs']} · "
        f"**Test paraphrases**: {summary['n_test_paraphrases']} · "
        f"**Seeds**: {summary['n_seeds']} · "
        f"**Train repeats**: {summary.get('train_repeats', 1)} · "
        f"**Elapsed**: {summary['elapsed_seconds']:.1f}s"
    )
    lines.append("")
    lines.append("## Test-phase routing accuracy (held-out paraphrases)")
    lines.append("")
    lines.append("| Condition | Test accuracy (mean ± std) | 95% CI | Test reward | Train reward |")
    lines.append("|-----------|----------------------------|--------|-------------|--------------|")
    sorted_conds = sorted(
        summary["by_condition"].items(),
        key=lambda kv: -kv[1]["test_accuracy"]["mean"],
    )
    for cond, m in sorted_conds:
        ac = m["test_accuracy"]
        tr = m["test_reward"]
        lines.append(
            f"| `{cond}` | {ac['mean']:.3f} ± {ac['std']:.3f} | "
            f"[{ac['ci95'][0]:.3f}, {ac['ci95'][1]:.3f}] | "
            f"{tr['mean']:.2f} ± {tr['std']:.2f} | "
            f"— |"
        )

    if summary.get("pairwise"):
        lines.append("")
        lines.append("## Δ test accuracy vs off-baseline")
        lines.append("")
        lines.append("| Condition | Δ accuracy | t (approx) | Rough p<0.05? |")
        lines.append("|-----------|-----------:|-----------:|:-------------:|")
        for pair_name, pair in sorted(
            summary["pairwise"].items(),
            key=lambda kv: -kv[1]["delta_accuracy"],
        ):
            sig = "yes" if pair["rough_significant_p05"] else "no"
            cond = pair_name.split("_vs_")[0]
            lines.append(
                f"| `{cond}` | "
                f"{pair['delta_accuracy']:+.3f} | "
                f"{pair['approx_t_statistic']:+.2f} | {sig} |"
            )
    return "\n".join(lines) + "\n"
