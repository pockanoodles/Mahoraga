"""
Phase-4 evaluation: semantic vs keyword vs off memory modes.

Replays a prompt set through a fresh BanditRouter under each memory mode
and aggregates cumulative reward, regret-vs-oracle, and per-bucket quality
across N seeds. The benchmark uses the FULL production code path —
BanditRouter, EpisodicMemory (with the dim=384 semantic tower), the
embedding service, and the LinUCB strategy — so results reflect what
real deployments will actually see.

Two prompt sets are supported:

  - "synthetic": the existing 28-prompt synthetic task pool from the
    `simulate` command. Buckets are well-separated; expected to show a
    small to moderate semantic vs keyword gap.

  - "adversarial": the 30-prompt set in benchmarks/adversarial_prompts.json.
    Six clusters of 5 prompts each share surface keyword features but
    diverge semantically. The 9-dim handcraft vector cannot discriminate
    *within* a cluster — semantic memory must.

Reward simulation: each prompt has an oracle agent + oracle reward. When
the bandit picks the oracle agent, it gets `oracle_reward + small noise`;
otherwise it gets a fraction of the oracle reward (penalty for wrong-fit).
The `(seed)` parameter controls both the prompt order shuffle and the
noise — runs are deterministic given the seed tuple.

Locked design decisions exercised:
  #7  N=10 seeds with mean ± std + 95% CI (t-distribution).
  #8  MAHORAGA_MEMORY_MODE flag toggled per condition.

Output layout:

  {result_dir}/
    raw_results.json       per-(mode, seed, task) full trace
    summary.json           aggregated stats per mode
    summary.md             human-readable comparison
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


# ── Prompt loading ────────────────────────────────────────────────────────────


@dataclass
class EvalPrompt:
    prompt: str
    bucket: str
    oracle_agent: str
    oracle_reward: float
    cluster_id: Optional[int] = None  # populated for adversarial set


def load_adversarial(path: Path) -> list[EvalPrompt]:
    """Load the adversarial prompt set from JSON."""
    data = json.loads(path.read_text())
    out: list[EvalPrompt] = []
    for cluster in data["clusters"]:
        for entry in cluster["prompts"]:
            out.append(EvalPrompt(
                prompt=entry["prompt"],
                bucket=entry["bucket"],
                oracle_agent=entry["oracle_agent"],
                oracle_reward=float(entry["oracle_reward"]),
                cluster_id=cluster["id"],
            ))
    return out


def load_synthetic() -> list[EvalPrompt]:
    """Load the existing 28-prompt synthetic pool (the standard benchmark)."""
    from backend.orchestrator.cli.commands.benchmark import _SYNTHETIC_TASKS
    out: list[EvalPrompt] = []
    for goal, bucket, oracle_agent, _latency, oracle_qual in _SYNTHETIC_TASKS:
        out.append(EvalPrompt(
            prompt=goal,
            bucket=bucket,
            oracle_agent=oracle_agent,
            oracle_reward=float(oracle_qual),
        ))
    return out


# ── Reward simulation ────────────────────────────────────────────────────────


def simulate_outcome_reward(
    selected: str,
    oracle_agent: str,
    oracle_reward: float,
    rng: random.Random,
    wrong_pick_factor: float = 0.50,
    noise_std: float = 0.04,
) -> float:
    """Return the reward the agent actually achieves on this prompt.

    - Right agent: oracle_reward + Gaussian noise.
    - Wrong agent: oracle_reward * wrong_pick_factor + Gaussian noise.

    The noise prevents the bandit from identifying the oracle perfectly
    on a single observation.
    """
    if selected == oracle_agent:
        base = oracle_reward
    else:
        base = oracle_reward * wrong_pick_factor
    noise = rng.gauss(0.0, noise_std)
    return max(0.0, min(1.0, base + noise))


# ── Single-condition runner ──────────────────────────────────────────────────


@dataclass
class TaskResult:
    task_index: int
    prompt: str
    bucket: str
    cluster_id: Optional[int]
    oracle_agent: str
    selected_agent: str
    is_correct: bool
    oracle_reward: float
    actual_reward: float
    cumulative_reward: float
    cumulative_regret: float


@dataclass
class ConditionResult:
    mode: str
    seed: int
    alpha: float
    confidence_weighted: bool
    cumulative_reward: float
    cumulative_regret: float
    correct_picks: int
    total_picks: int
    per_bucket: dict[str, dict[str, float]]
    tasks: list[TaskResult] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct_picks / self.total_picks if self.total_picks else 0.0

    @property
    def condition_id(self) -> str:
        """Stable identifier combining mode + α + confidence-weighting flag."""
        suffix = "+conf" if self.confidence_weighted else ""
        return f"{self.mode}@α={self.alpha:.2f}{suffix}"


class _MockRegistry:
    """Provides a fixed agent roster to BanditRouter."""

    def __init__(self, agents: list[str]) -> None:
        self._agents = agents

    def all(self):
        return [type("A", (), {"name": a})() for a in self._agents]


def _outcome_for(
    prompt: EvalPrompt,
    selected: str,
    rng: random.Random,
) -> Any:
    """Build a TaskOutcome for the bandit to learn from. Imports live so
    the function remains usable without circular dependencies at module load."""
    from backend.orchestrator.routing.reward import TaskOutcome
    actual_reward = simulate_outcome_reward(
        selected, prompt.oracle_agent, prompt.oracle_reward, rng,
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
        bucket=prompt.bucket,
    ), actual_reward


def run_condition(
    prompts: list[EvalPrompt],
    mode: str,
    seed: int,
    state_dir: Path,
    cache_path: Path,
    agents: list[str],
    repeats: int = 5,
    alpha: float = 0.20,
    confidence_weighted: bool = False,
) -> ConditionResult:
    """Run one (mode, seed) condition. Returns aggregated results.

    Each prompt is replayed `repeats` times (in shuffled order) so episodic
    memory has enough exposure to build a useful retrieval signal. With
    repeats=1 the memory paths are effectively cold the entire run — the
    benchmark cannot distinguish modes (semantic falls through to keyword
    on every empty retrieval).

    α and confidence-weighted are routing-time hyperparameters: they
    control how aggressively the memory bias is blended into the LinUCB
    exploit score. See bandit_router._resolve_memory_alpha and
    _resolve_confidence_weighting.
    """
    # Set env first — BanditRouter resolves mode at every call.
    os.environ["MAHORAGA_MEMORY_MODE"] = mode
    os.environ["MAHORAGA_MEMORY_ALPHA"] = f"{alpha:.4f}"
    os.environ["MAHORAGA_MEMORY_CONFIDENCE_WEIGHTED"] = (
        "true" if confidence_weighted else "false"
    )
    os.environ["MAHORAGA_BANDIT_SEED"] = str(seed)
    os.environ["MAHORAGA_PROMPT_SEED"] = str(seed)

    state_dir.mkdir(parents=True, exist_ok=True)

    # Defer imports — BanditRouter reads env vars at construction.
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
    # Force cache path so concurrent eval runs don't fight over the default.
    router._embedding_service = EmbeddingService(cache_path=cache_path)
    router._embedding_init_attempted = True

    rng = random.Random(seed)

    # Prompt order: each prompt repeated `repeats` times, full sequence shuffled.
    order = list(range(len(prompts))) * max(1, int(repeats))
    rng.shuffle(order)

    cum_reward = 0.0
    cum_regret = 0.0
    correct = 0
    per_bucket: dict[str, list[float]] = {}
    per_bucket_correct: dict[str, int] = {}
    per_bucket_total: dict[str, int] = {}

    task_results: list[TaskResult] = []

    for step_i, idx in enumerate(order):
        p = prompts[idx]
        # Construct minimal task object compatible with TaskContext.from_task
        task = type("T", (), {"goal": p.prompt, "id": f"t{seed}-{step_i}"})()

        selected = router.route(task, available_agents=agents)
        outcome, actual_reward = _outcome_for(p, selected, rng)
        router.observe(task, outcome)

        cum_reward += actual_reward
        cum_regret += max(0.0, p.oracle_reward - actual_reward)
        if selected == p.oracle_agent:
            correct += 1
        per_bucket.setdefault(p.bucket, []).append(actual_reward)
        per_bucket_correct[p.bucket] = per_bucket_correct.get(p.bucket, 0) + (
            1 if selected == p.oracle_agent else 0
        )
        per_bucket_total[p.bucket] = per_bucket_total.get(p.bucket, 0) + 1

        task_results.append(TaskResult(
            task_index=step_i,
            prompt=p.prompt,
            bucket=p.bucket,
            cluster_id=p.cluster_id,
            oracle_agent=p.oracle_agent,
            selected_agent=selected,
            is_correct=(selected == p.oracle_agent),
            oracle_reward=p.oracle_reward,
            actual_reward=actual_reward,
            cumulative_reward=cum_reward,
            cumulative_regret=cum_regret,
        ))

    per_bucket_summary = {}
    for bucket, rewards in per_bucket.items():
        n_correct = per_bucket_correct.get(bucket, 0)
        n_total = per_bucket_total.get(bucket, 0)
        per_bucket_summary[bucket] = {
            "mean_reward": statistics.mean(rewards),
            "n_total": n_total,
            "n_correct": n_correct,
            "accuracy": n_correct / n_total if n_total else 0.0,
        }

    logger.close()

    return ConditionResult(
        mode=mode,
        seed=seed,
        alpha=alpha,
        confidence_weighted=confidence_weighted,
        cumulative_reward=cum_reward,
        cumulative_regret=cum_regret,
        correct_picks=correct,
        total_picks=len(order),
        per_bucket=per_bucket_summary,
        tasks=task_results,
    )


# ── Aggregation ──────────────────────────────────────────────────────────────


def _ci95(values: list[float]) -> tuple[float, float]:
    """95% confidence interval using t-distribution (small N)."""
    if len(values) < 2:
        return (0.0, 0.0)
    mean = statistics.mean(values)
    std = statistics.stdev(values)
    n = len(values)
    se = std / math.sqrt(n)
    # t-critical for 95% CI, df = n-1 (rough lookup; for N=10 → ~2.262)
    t_crit_table = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
                    7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262, 15: 2.131,
                    20: 2.086, 30: 2.042, 50: 2.009}
    t_crit = t_crit_table.get(n, 1.96)  # large-N fallback
    margin = t_crit * se
    return (mean - margin, mean + margin)


def aggregate(results: list[ConditionResult]) -> dict[str, Any]:
    """Aggregate per-condition statistics across seeds.

    Conditions are identified by `condition_id = mode@α=X.XX[+conf]`.
    The "by_mode" key in the output is kept for backward compatibility:
    it groups by condition_id (not just `mode`), so callers that iterate
    `summary["by_mode"]` see one entry per (mode, α, conf) tuple.
    """
    by_cond: dict[str, list[ConditionResult]] = {}
    for r in results:
        by_cond.setdefault(r.condition_id, []).append(r)

    summary: dict[str, Any] = {"by_mode": {}, "by_condition": {}}
    for cond, runs in by_cond.items():
        rewards = [r.cumulative_reward for r in runs]
        regrets = [r.cumulative_regret for r in runs]
        accuracies = [r.accuracy for r in runs]

        bucket_accuracies: dict[str, list[float]] = {}
        bucket_rewards: dict[str, list[float]] = {}
        for run in runs:
            for bucket, stats in run.per_bucket.items():
                bucket_accuracies.setdefault(bucket, []).append(stats["accuracy"])
                bucket_rewards.setdefault(bucket, []).append(stats["mean_reward"])

        bucket_summary = {}
        for bucket in sorted(bucket_accuracies):
            bucket_summary[bucket] = {
                "mean_accuracy": statistics.mean(bucket_accuracies[bucket]),
                "mean_reward": statistics.mean(bucket_rewards[bucket]),
                "n_seeds": len(bucket_accuracies[bucket]),
            }

        block = {
            "n_seeds": len(runs),
            "mode": runs[0].mode,
            "alpha": runs[0].alpha,
            "confidence_weighted": runs[0].confidence_weighted,
            "cumulative_reward": {
                "mean": statistics.mean(rewards),
                "std": statistics.stdev(rewards) if len(rewards) > 1 else 0.0,
                "ci95": _ci95(rewards),
                "values": rewards,
            },
            "cumulative_regret": {
                "mean": statistics.mean(regrets),
                "std": statistics.stdev(regrets) if len(regrets) > 1 else 0.0,
                "ci95": _ci95(regrets),
                "values": regrets,
            },
            "accuracy": {
                "mean": statistics.mean(accuracies),
                "std": statistics.stdev(accuracies) if len(accuracies) > 1 else 0.0,
                "ci95": _ci95(accuracies),
                "values": accuracies,
            },
            "per_bucket": bucket_summary,
        }
        summary["by_condition"][cond] = block
        # Back-compat: legacy callers/tests still iterate `by_mode`.
        summary["by_mode"][cond] = block

    # Pairwise reward deltas vs the off-mode baseline (if present).
    summary["pairwise"] = {}
    off_baseline = None
    for cond, block in summary["by_condition"].items():
        if block["mode"] == "off":
            off_baseline = (cond, block)
            break
    if off_baseline:
        off_cond, off_block = off_baseline
        off_mean = off_block["cumulative_reward"]["mean"]
        off_std = off_block["cumulative_reward"]["std"]
        for cond, block in summary["by_condition"].items():
            if cond == off_cond:
                continue
            ma = block["cumulative_reward"]["mean"]
            sda = block["cumulative_reward"]["std"]
            n = block["n_seeds"]
            pooled_se = math.sqrt(sda**2 / n + off_std**2 / n) if n > 0 else 0.0
            t_stat = (ma - off_mean) / pooled_se if pooled_se > 0 else 0.0
            summary["pairwise"][f"{cond}_vs_{off_cond}"] = {
                "delta_mean_reward": ma - off_mean,
                "approx_t_statistic": t_stat,
                "rough_significant_p05": abs(t_stat) > 2.1,
            }

    return summary


# ── Top-level orchestrator ───────────────────────────────────────────────────


def run_eval(
    prompts: list[EvalPrompt],
    modes: list[str],
    seeds: list[int],
    result_dir: Path,
    agents: Optional[list[str]] = None,
    repeats: int = 5,
    alphas: Optional[list[float]] = None,
    confidence_weighting: Optional[list[bool]] = None,
) -> dict[str, Any]:
    """Run the full grid of (mode × α × conf-weight × seed) conditions and
    write artifacts.

    The default α list is [MEMORY_ALPHA] (the production default 0.20). To
    sweep α, pass a list like [0.0, 0.05, 0.10, 0.20, 0.30]. The off-mode
    condition is run only once (memory disabled, α has no effect) regardless
    of how many α values are passed.
    """
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
    raw_traces: list[dict[str, Any]] = []

    for mode in modes:
        # For mode=off, α and confidence-weighting have no effect — skip
        # the redundant grid points and run it once at the canonical α=0.0.
        mode_alphas = [0.0] if mode == "off" else alphas
        mode_confs = [False] if mode == "off" else confidence_weighting

        for alpha in mode_alphas:
            for cw in mode_confs:
                cond_label = (
                    f"{mode}_a{alpha:.2f}" + ("_cw" if cw else "")
                )
                for seed in seeds:
                    run_state_dir = (
                        result_dir / "_runs" / f"{cond_label}_seed{seed}"
                    )
                    cache_path = (
                        result_dir / "_runs" / "shared_emb_cache.sqlite"
                    )
                    res = run_condition(
                        prompts=prompts, mode=mode, seed=seed,
                        state_dir=run_state_dir, cache_path=cache_path,
                        agents=agents, repeats=repeats,
                        alpha=alpha, confidence_weighted=cw,
                    )
                    all_results.append(res)
                    raw_traces.append({
                        "condition_id": res.condition_id,
                        "mode": res.mode,
                        "alpha": res.alpha,
                        "confidence_weighted": res.confidence_weighted,
                        "seed": res.seed,
                        "cumulative_reward": res.cumulative_reward,
                        "cumulative_regret": res.cumulative_regret,
                        "accuracy": res.accuracy,
                        "tasks": [
                            {
                                "task_index": t.task_index,
                                "prompt": t.prompt,
                                "bucket": t.bucket,
                                "cluster_id": t.cluster_id,
                                "oracle_agent": t.oracle_agent,
                                "selected_agent": t.selected_agent,
                                "is_correct": t.is_correct,
                                "actual_reward": t.actual_reward,
                                "cumulative_reward": t.cumulative_reward,
                                "cumulative_regret": t.cumulative_regret,
                            }
                            for t in res.tasks
                        ],
                    })

    summary = aggregate(all_results)
    summary["elapsed_seconds"] = time.time() - started
    summary["n_prompts"] = len(prompts)
    summary["n_seeds"] = len(seeds)
    summary["repeats"] = repeats
    summary["modes"] = modes
    summary["alphas"] = alphas
    summary["confidence_weighting"] = confidence_weighting
    summary["agents"] = agents

    (result_dir / "raw_results.json").write_text(
        json.dumps(raw_traces, indent=2)
    )
    (result_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (result_dir / "summary.md").write_text(_markdown_summary(summary))

    return summary


def _markdown_summary(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Memory-Mode Evaluation Summary")
    lines.append("")
    alpha_str = ", ".join(f"{a:.2f}" for a in summary.get("alphas", [0.20]))
    cw_str = (
        "on+off" if any(summary.get("confidence_weighting", [False]))
        and not all(summary.get("confidence_weighting", [False]))
        else ("on" if any(summary.get("confidence_weighting", [False])) else "off")
    )
    lines.append(
        f"**Prompts**: {summary['n_prompts']} × {summary.get('repeats', 1)} repeats · "
        f"**Seeds**: {summary['n_seeds']} · "
        f"**Modes**: {', '.join(summary['modes'])} · "
        f"**α**: {alpha_str} · "
        f"**Conf-weighted**: {cw_str} · "
        f"**Elapsed**: {summary['elapsed_seconds']:.1f}s"
    )
    lines.append("")
    lines.append("## Headline metrics (sorted by mean reward)")
    lines.append("")
    lines.append("| Condition | Mode | α | Conf | Cum reward (mean ± std) | 95% CI | Accuracy | Regret |")
    lines.append("|-----------|------|---|------|-------------------------|--------|----------|--------|")
    sorted_conds = sorted(
        summary["by_condition"].items(),
        key=lambda kv: -kv[1]["cumulative_reward"]["mean"],
    )
    for cond, m in sorted_conds:
        cr = m["cumulative_reward"]
        ac = m["accuracy"]
        rg = m["cumulative_regret"]
        cw = "yes" if m["confidence_weighted"] else "no"
        lines.append(
            f"| `{cond}` | {m['mode']} | {m['alpha']:.2f} | {cw} | "
            f"{cr['mean']:.2f} ± {cr['std']:.2f} | "
            f"[{cr['ci95'][0]:.2f}, {cr['ci95'][1]:.2f}] | "
            f"{ac['mean']:.2%} | "
            f"{rg['mean']:.2f} |"
        )

    if summary["pairwise"]:
        lines.append("")
        lines.append("## Deltas vs off-mode baseline")
        lines.append("")
        lines.append("| Condition | Δ mean reward | t (approx) | Rough p<0.05? |")
        lines.append("|-----------|---------------|------------|---------------|")
        sorted_pairs = sorted(
            summary["pairwise"].items(),
            key=lambda kv: -kv[1]["delta_mean_reward"],
        )
        for pair_name, pair in sorted_pairs:
            sig = "yes" if pair["rough_significant_p05"] else "no"
            lines.append(
                f"| `{pair_name.split('_vs_')[0]}` | "
                f"{pair['delta_mean_reward']:+.3f} | "
                f"{pair['approx_t_statistic']:+.2f} | {sig} |"
            )

    lines.append("")
    lines.append("## Per-bucket accuracy (mean across seeds)")
    lines.append("")
    buckets = sorted({
        b for m in summary["by_condition"].values() for b in m["per_bucket"]
    })
    header = "| Bucket | " + " | ".join(
        f"`{c}`" for c in summary["by_condition"].keys()
    ) + " |"
    sep = "|" + "---|" * (1 + len(summary["by_condition"]))
    lines.append(header)
    lines.append(sep)
    for bucket in buckets:
        row = [bucket]
        for cond in summary["by_condition"]:
            stats = summary["by_condition"][cond]["per_bucket"].get(bucket)
            if stats is None:
                row.append("—")
            else:
                row.append(f"{stats['mean_accuracy']:.2f}")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"
