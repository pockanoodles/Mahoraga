"""
Ablation Runner for Mahoraga Benchmark Harness.

Sweeps hyperparameters and produces comparison tables showing which
settings matter most.  Three ablation axes:

  1. alpha (exploration parameter): controls explore/exploit tradeoff
  2. Context dimension d: Tier-1 (8 features) vs Tier-1+2 (14 features)
  3. Reward weight decomposition: vary success/speed/cost/quality weights

Each ablation runs the full 200-task replay and records:
  - Success rate, mean reward, total regret, growth exponent

Output:
  - ablation_table.md:  markdown table ready for the README
  - ablation_data.json: raw results for further analysis

Usage:
    runner = AblationRunner(oracle, strategies_factory)
    runner.sweep_alpha([0.1, 0.5, 1.0, 2.0, 5.0])
    runner.sweep_context_dim([8, 14])
    runner.sweep_reward_weights([...])
    runner.save_all("benchmark/results/")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class AblationResult:
    """Results from a single ablation run."""

    def __init__(self, label: str, param_name: str, param_value: str,
                 success_rate: float, mean_reward: float, total_regret: float,
                 growth_exponent: float, is_sublinear: bool,
                 mean_latency: float, mean_cost: float):
        self.label = label
        self.param_name = param_name
        self.param_value = param_value
        self.success_rate = success_rate
        self.mean_reward = mean_reward
        self.total_regret = total_regret
        self.growth_exponent = growth_exponent
        self.is_sublinear = is_sublinear
        self.mean_latency = mean_latency
        self.mean_cost = mean_cost

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in [
            "label", "param_name", "param_value", "success_rate",
            "mean_reward", "total_regret", "growth_exponent",
            "is_sublinear", "mean_latency", "mean_cost",
        ]}


class AblationRunner:
    """Runs ablation sweeps and collects results."""

    def __init__(self, results: Optional[list[AblationResult]] = None):
        self.results: list[AblationResult] = results or []

    def add_result(self, result: AblationResult) -> None:
        self.results.append(result)

    def to_markdown(self) -> str:
        """Generate grouped markdown tables from all ablation results."""
        if not self.results:
            return "*No ablation results yet.*\n"

        groups: dict[str, list[AblationResult]] = {}
        for r in self.results:
            groups.setdefault(r.param_name, []).append(r)

        lines: list[str] = []
        for param_name, group in groups.items():
            lines.append(f"\n### Ablation: {param_name}\n")
            lines.append("| Setting | Success Rate | Mean Reward | Total Regret | beta (growth) | Sublinear? | Avg Latency | Avg Cost |")
            lines.append("|---------|-------------|-------------|-------------|--------------|------------|------------|----------|")
            for r in sorted(group, key=lambda x: -x.mean_reward):
                sub = "Yes" if r.is_sublinear else "No"
                lines.append(f"| {r.param_value} | {r.success_rate:.1%} | {r.mean_reward:.4f} | {r.total_regret:.2f} | {r.growth_exponent:.3f} | {sub} | {r.mean_latency:.1f}s | ${r.mean_cost:.4f} |")
            best = max(group, key=lambda x: x.mean_reward)
            lines.append(f"\n**Best: {best.param_value}** (reward={best.mean_reward:.4f}, regret={best.total_regret:.2f})\n")
        return "\n".join(lines)

    def save_markdown(self, output_path: str) -> None:
        """Write grouped ablation tables to a markdown file."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# Ablation Study Results\n\n")
            f.write("Hyperparameter sweeps for Mahoraga's LinUCB bandit router.\n")
            f.write("Each row is a full 200-task replay with different settings.\n")
            f.write(self.to_markdown())
        print(f"  Saved ablation table: {output_path}")

    def save_json(self, output_path: str) -> None:
        """Save ablation results to JSON."""
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([r.to_dict() for r in self.results], f, indent=2)
        print(f"  Saved ablation data:  {output_path}")


# Predefined sweep configs
ALPHA_VALUES = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0]
CONTEXT_DIMS = [8, 14]
REWARD_WEIGHT_PRESETS = {
    "default":       {"success": 0.50, "quality": 0.25, "speed": 0.15, "cost": 0.10},
    "balanced":      {"success": 0.40, "quality": 0.20, "speed": 0.20, "cost": 0.20},
    "quality_first": {"success": 0.30, "quality": 0.40, "speed": 0.15, "cost": 0.15},
    "speed_first":   {"success": 0.25, "quality": 0.15, "speed": 0.45, "cost": 0.15},
    "cost_first":    {"success": 0.25, "quality": 0.15, "speed": 0.15, "cost": 0.45},
    "success_only":  {"success": 1.00, "quality": 0.00, "speed": 0.00, "cost": 0.00},
}


def run_ablation_sweep(oracle, make_strategy, output_dir: str = "benchmark/results") -> AblationRunner:
    """
    Run the full ablation suite.

    make_strategy signature: (alpha, dim, reward_weights) -> Strategy
    Strategy must have .select(task, agents) -> str and .update(task, agent, reward)
    """
    from .oracle import AGENTS
    from .regret import RegretTracker

    runner = AblationRunner()
    tasks = oracle.generate_tasks()

    def _run_one(strategy, label, param_name, param_value):
        tracker = RegretTracker(strategies=["linucb"])
        successes, total_reward, total_latency, total_cost = 0, 0.0, 0.0, 0.0

        for i, task in enumerate(tasks):
            agent = strategy.select(task, AGENTS)
            outcome = oracle.evaluate(task, agent)
            oracle_reward = oracle.optimal_reward(task)
            strategy.update(task, agent, outcome["reward"])
            tracker.record(i, "linucb", outcome["reward"], oracle_reward)
            successes += int(outcome["success"])
            total_reward += outcome["reward"]
            total_latency += outcome["latency_s"]
            total_cost += outcome["cost_usd"]

        n = len(tasks)
        runner.add_result(AblationResult(
            label=label, param_name=param_name, param_value=param_value,
            success_rate=successes / n, mean_reward=total_reward / n,
            total_regret=tracker.total_regret("linucb"),
            growth_exponent=tracker.regret_growth_exponent("linucb"),
            is_sublinear=tracker.is_sublinear("linucb"),
            mean_latency=total_latency / n, mean_cost=total_cost / n,
        ))

    # alpha sweep
    for alpha in ALPHA_VALUES:
        s = make_strategy(alpha=alpha, dim=8, reward_weights=REWARD_WEIGHT_PRESETS["balanced"])
        _run_one(s, f"alpha={alpha}", "alpha (exploration)", f"alpha={alpha}")

    # Context dim sweep
    for dim in CONTEXT_DIMS:
        s = make_strategy(alpha=1.0, dim=dim, reward_weights=REWARD_WEIGHT_PRESETS["balanced"])
        dim_label = f"d={dim} ({'Tier 1' if dim == 8 else 'Tier 1+2'})"
        _run_one(s, dim_label, "Context dimension (d)", dim_label)

    # Reward weight sweep
    for name, weights in REWARD_WEIGHT_PRESETS.items():
        s = make_strategy(alpha=1.0, dim=8, reward_weights=weights)
        w_str = ", ".join(f"{k}={v:.2f}" for k, v in weights.items())
        _run_one(s, name, "Reward weights", f"{name} ({w_str})")

    runner.save_markdown(f"{output_dir}/ablation_table.md")
    runner.save_json(f"{output_dir}/ablation_data.json")
    return runner
