"""
Benchmark harness for comparing routing strategies.

This harness runs SIMULATED execution — no real agents are called.
Instead, it uses a simulated oracle that produces outcomes based on
ground-truth task-type compatibility per agent.

Ground truth compatibility matrix (how good each agent is at each task type):
    aider:      code_generation=0.9, code_editing=0.9, debugging=0.8, simple_qa=0.4, research=0.3, terminal_operations=0.5
    ollama:     code_generation=0.5, code_editing=0.5, debugging=0.5, simple_qa=0.9, research=0.7, terminal_operations=0.6
    claude:     code_generation=0.8, code_editing=0.8, debugging=0.9, simple_qa=0.9, research=0.95, terminal_operations=0.7
    codex:      code_generation=0.85, code_editing=0.7, debugging=0.7, simple_qa=0.5, research=0.4, terminal_operations=0.6
    goose:      code_generation=0.3, code_editing=0.3, debugging=0.4, simple_qa=0.7, research=0.8, terminal_operations=0.9
    gemini-cli: code_generation=0.6, code_editing=0.6, debugging=0.6, simple_qa=0.85, research=0.85, terminal_operations=0.5

For each (agent, task_type) pair, the oracle returns:
    success = random() < compatibility_score
    quality_score = compatibility_score * random() * 0.3 + compatibility_score * 0.7  (noisy quality)
    latency_s = random() * 5 + (1 - compatibility_score) * 10  (faster for good matches)
    cost_usd = {"aider": 0.0, "ollama": 0.0, "claude": 0.002, "codex": 0.001, "goose": 0.0, "gemini-cli": 0.0001}

Usage:
    cd /Users/kaitosoeno/Projects/Mahoraga/.worktrees/feat-bandit-router
    python -m backend.orchestrator.routing.benchmark.harness
"""
from __future__ import annotations
import json
import random
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from backend.orchestrator.routing import BanditRouter, TaskOutcome

AGENTS = ["aider", "ollama", "claude", "codex", "goose", "gemini-cli"]

COMPATIBILITY = {
    "aider":      {"code_generation": 0.9, "code_editing": 0.9, "debugging": 0.8, "simple_qa": 0.4, "research": 0.3,  "terminal_operations": 0.5},
    "ollama":     {"code_generation": 0.5, "code_editing": 0.5, "debugging": 0.5, "simple_qa": 0.9, "research": 0.7,  "terminal_operations": 0.6},
    "claude":     {"code_generation": 0.8, "code_editing": 0.8, "debugging": 0.9, "simple_qa": 0.9, "research": 0.95, "terminal_operations": 0.7},
    "codex":      {"code_generation": 0.85,"code_editing": 0.7, "debugging": 0.7, "simple_qa": 0.5, "research": 0.4,  "terminal_operations": 0.6},
    "goose":      {"code_generation": 0.3, "code_editing": 0.3, "debugging": 0.4, "simple_qa": 0.7, "research": 0.8,  "terminal_operations": 0.9},
    "gemini-cli": {"code_generation": 0.6, "code_editing": 0.6, "debugging": 0.6, "simple_qa": 0.85,"research": 0.85, "terminal_operations": 0.5},
}

COSTS = {"aider": 0.0, "ollama": 0.0, "claude": 0.002, "codex": 0.001, "goose": 0.0, "gemini-cli": 0.0001}

STRATEGIES = ["static", "ucb1", "thompson", "linucb"]

TASKS_PATH = Path(__file__).parent / "tasks.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"


# ── Simple mock registry ───────────────────────────────────────────────────────

class _MockAgent:
    def __init__(self, name: str):
        self.name = name

class _MockRegistry:
    def all(self):
        return [_MockAgent(a) for a in AGENTS]


# ── Oracle ────────────────────────────────────────────────────────────────────

def simulate_outcome(agent: str, task_type: str, rng: random.Random) -> dict:
    compat = COMPATIBILITY[agent].get(task_type, 0.5)
    success = rng.random() < compat
    quality = compat * rng.random() * 0.3 + compat * 0.7 if success else 0.0
    latency = rng.random() * 5 + (1 - compat) * 10
    cost = COSTS.get(agent, 0.0)
    return {"success": success, "quality_score": quality, "latency_s": latency, "cost_usd": cost}


# ── Benchmark harness ─────────────────────────────────────────────────────────

class BenchmarkHarness:
    def __init__(self):
        self.tasks = self._load_tasks()
        self.registry = _MockRegistry()
        self.rng = random.Random(42)

    def _load_tasks(self) -> list[dict]:
        if not TASKS_PATH.exists():
            raise FileNotFoundError(
                f"tasks.jsonl not found at {TASKS_PATH}\n"
                "Run: python -m backend.orchestrator.routing.benchmark.generate_tasks"
            )
        tasks = []
        with TASKS_PATH.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    tasks.append(json.loads(line))
        return tasks

    def run_strategy(self, strategy_name: str) -> dict:
        """Run all tasks through a given strategy. Returns per-task records."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            state_path = Path(tf.name)

        try:
            router = BanditRouter(
                strategy=strategy_name,
                registry=self.registry,
                state_path=state_path,
            )

            records = []
            cumulative_reward = 0.0

            for t in self.tasks:
                task_type = t["type"]

                # Route
                selected_agent = router.route(t)

                # Simulate outcome with fixed RNG
                sim = simulate_outcome(selected_agent, task_type, self.rng)

                outcome = TaskOutcome(
                    success=sim["success"],
                    latency_s=sim["latency_s"],
                    cost_usd=sim["cost_usd"],
                    quality_score=sim["quality_score"],
                    agent_name=selected_agent,
                )

                # Observe
                router.observe(t, outcome)

                reward = router.reward_calc.compute(outcome)
                cumulative_reward += reward

                records.append({
                    "task_id": t["id"],
                    "task_type": task_type,
                    "agent": selected_agent,
                    "success": sim["success"],
                    "reward": reward,
                    "latency_s": sim["latency_s"],
                    "cost_usd": sim["cost_usd"],
                    "cumulative_reward": cumulative_reward,
                })

        finally:
            if state_path.exists():
                state_path.unlink()

        return {"strategy": strategy_name, "records": records}

    def compute_summary(self, run_result: dict) -> dict:
        records = run_result["records"]
        n = len(records)
        if n == 0:
            return {}

        successes = sum(1 for r in records if r["success"])
        total_cost = sum(r["cost_usd"] for r in records)
        total_reward = sum(r["reward"] for r in records)
        total_latency = sum(r["latency_s"] for r in records)

        # Per-type success rate
        by_type: dict[str, list] = defaultdict(list)
        for r in records:
            by_type[r["task_type"]].append(r["success"])
        success_by_type = {k: sum(v) / len(v) for k, v in by_type.items()}

        # Agent distribution
        agent_counts: dict[str, int] = defaultdict(int)
        for r in records:
            agent_counts[r["agent"]] += 1
        agent_dist = {a: agent_counts.get(a, 0) / n for a in AGENTS}

        return {
            "strategy": run_result["strategy"],
            "n": n,
            "success_rate": successes / n,
            "avg_latency": total_latency / n,
            "avg_cost": total_cost / n,
            "total_cost": total_cost,
            "avg_reward": total_reward / n,
            "cumulative_reward": total_reward,
            "success_by_type": success_by_type,
            "agent_dist": agent_dist,
            "records": records,
        }

    def run_all(self) -> list[dict]:
        results = []
        for strategy in STRATEGIES:
            # Reset RNG to same seed for each strategy so oracle draws are identical
            self.rng = random.Random(42)
            print(f"  Running strategy: {strategy} ...", flush=True)
            run = self.run_strategy(strategy)
            summary = self.compute_summary(run)
            results.append(summary)
        return results

    # ── Chart generation ───────────────────────────────────────────────────────

    def _generate_charts(self, results: list[dict]) -> None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            print("WARNING: matplotlib not installed — skipping chart generation")
            return

        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        colors = ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2"]

        # 1. Cumulative reward curve
        fig, ax = plt.subplots(figsize=(10, 5))
        for res, color in zip(results, colors):
            cumulative = [r["cumulative_reward"] for r in res["records"]]
            ax.plot(range(1, len(cumulative) + 1), cumulative, label=res["strategy"], color=color, linewidth=1.5)
        ax.set_xlabel("Task #")
        ax.set_ylabel("Cumulative Reward")
        ax.set_title("Cumulative Reward per Strategy")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(RESULTS_DIR / "reward_curve.png", dpi=120)
        plt.close(fig)

        # 2. Success rate by task type — grouped bar chart
        task_types = sorted({k for res in results for k in res["success_by_type"]})
        x = range(len(task_types))
        width = 0.2
        fig, ax = plt.subplots(figsize=(12, 5))
        for i, (res, color) in enumerate(zip(results, colors)):
            rates = [res["success_by_type"].get(tt, 0.0) for tt in task_types]
            offsets = [xi + (i - 1.5) * width for xi in x]
            ax.bar(offsets, rates, width=width, label=res["strategy"], color=color, alpha=0.85)
        ax.set_xticks(list(x))
        ax.set_xticklabels(task_types, rotation=20, ha="right")
        ax.set_ylabel("Success Rate")
        ax.set_title("Success Rate by Task Type")
        ax.legend()
        ax.set_ylim(0, 1.05)
        ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(RESULTS_DIR / "success_by_type.png", dpi=120)
        plt.close(fig)

        # 3. Agent distribution — stacked bar per strategy
        fig, ax = plt.subplots(figsize=(10, 5))
        strategy_names = [res["strategy"] for res in results]
        agent_colors = ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948"]
        bottoms = [0.0] * len(results)
        for agent, color in zip(AGENTS, agent_colors):
            fractions = [res["agent_dist"].get(agent, 0.0) for res in results]
            ax.bar(strategy_names, fractions, bottom=bottoms, label=agent, color=color, alpha=0.85)
            bottoms = [b + f for b, f in zip(bottoms, fractions)]
        ax.set_ylabel("Fraction of Tasks")
        ax.set_title("Agent Selection Distribution per Strategy")
        ax.legend(loc="upper right", bbox_to_anchor=(1.15, 1))
        ax.set_ylim(0, 1.05)
        ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout()
        fig.savefig(RESULTS_DIR / "agent_dist.png", dpi=120)
        plt.close(fig)

        print(f"Charts saved to {RESULTS_DIR}/")

    # ── Reporting ──────────────────────────────────────────────────────────────

    def _write_summary_json(self, results: list[dict]) -> None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        # Strip records list (too large for summary JSON)
        clean = []
        for res in results:
            r = {k: v for k, v in res.items() if k != "records"}
            clean.append(r)
        with (RESULTS_DIR / "summary.json").open("w") as f:
            json.dump(clean, f, indent=2)

    def _find_best(self, results: list[dict]) -> dict:
        """Return strategy name that wins each metric."""
        best: dict[str, str] = {}
        metrics = {
            "success_rate": max,
            "avg_latency": min,
            "avg_cost": min,
            "total_cost": min,
            "avg_reward": max,
        }
        for metric, fn in metrics.items():
            best_val = fn(res[metric] for res in results)
            for res in results:
                if res[metric] == best_val:
                    best[metric] = res["strategy"]
                    break
        return best

    def _write_comparison_md(self, results: list[dict]) -> None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        best = self._find_best(results)

        def fmt(strategy: str, metric: str, value: str) -> str:
            if best.get(metric) == strategy:
                return f"**{value}**"
            return value

        header = "| Strategy | Success Rate | Avg Latency | Avg Cost/Task | Total Cost | Avg Reward |"
        sep    = "|----------|-------------|-------------|---------------|------------|------------|"
        rows = [header, sep]

        for res in results:
            s = res["strategy"]
            label = s if s != "static" else "static (baseline)"
            sr  = fmt(s, "success_rate", f"{res['success_rate']*100:.1f}%")
            lat = fmt(s, "avg_latency",  f"{res['avg_latency']:.1f}s")
            ac  = fmt(s, "avg_cost",     f"${res['avg_cost']:.4f}")
            tc  = fmt(s, "total_cost",   f"${res['total_cost']:.4f}")
            ar  = fmt(s, "avg_reward",   f"{res['avg_reward']:.3f}")
            rows.append(f"| {label} | {sr} | {lat} | {ac} | {tc} | {ar} |")

        md = "\n".join(rows) + "\n"
        with (RESULTS_DIR / "comparison.md").open("w") as f:
            f.write("# Bandit Router Benchmark Results\n\n")
            f.write(md)

    def _print_summary_table(self, results: list[dict]) -> None:
        best = self._find_best(results)
        print()
        print("=" * 80)
        print("BANDIT ROUTER BENCHMARK RESULTS")
        print("=" * 80)
        print(f"{'Strategy':<20} {'Success%':>10} {'AvgLatency':>12} {'AvgCost':>12} {'TotalCost':>12} {'AvgReward':>12}")
        print("-" * 80)
        for res in results:
            s = res["strategy"]
            marker = " *" if best.get("avg_reward") == s else "  "
            print(
                f"{s:<20}"
                f"{res['success_rate']*100:>9.1f}%"
                f"{res['avg_latency']:>11.2f}s"
                f"  ${res['avg_cost']:>9.5f}"
                f"  ${res['total_cost']:>8.4f}"
                f"  {res['avg_reward']:>10.4f}"
                f"{marker}"
            )
        print("-" * 80)
        print(f"  * = best avg_reward (winner)")
        print()

    def report(self, results: list[dict]) -> None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        self._write_summary_json(results)
        self._write_comparison_md(results)
        self._generate_charts(results)
        self._print_summary_table(results)
        print(f"Results written to {RESULTS_DIR}/")


def main() -> None:
    harness = BenchmarkHarness()
    n_tasks = len(harness.tasks)
    print(f"Loaded {n_tasks} tasks")
    print(f"Running {len(STRATEGIES)} strategies ...\n")
    results = harness.run_all()
    harness.report(results)


if __name__ == "__main__":
    main()
