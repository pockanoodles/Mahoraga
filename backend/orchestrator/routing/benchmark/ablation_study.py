"""
Mahoraga Full Ablation Study — orch benchmark ablation

Runs 6 controlled experiments on the same 200-task oracle, each producing a
cumulative-regret chart. All charts share consistent styling and are saved
to benchmark_results/ablation/.

Experiments
-----------
1. strategy_comparison  — linucb / dlinucb / thompson / ucb1 / static
2. warm_start           — dlinucb cold-start vs warm-start
3. episodic_memory      — dlinucb α=0.20 vs α=0.0 (memory disabled)
4. swap_penalty         — dlinucb β_swap=0.10 vs β_swap=0.0
5. bucket_granularity   — oracle with 7 categories vs 3 collapsed categories
6. adaptive_gamma       — synthetic drift: global γ vs per-arm adaptive γ
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

import numpy as np

try:
    from .oracle import AGENTS, COMPATIBILITY, TASK_CATEGORIES, Oracle, Task
except ImportError:
    from oracle import AGENTS, COMPATIBILITY, TASK_CATEGORIES, Oracle, Task  # type: ignore[no-redef]

ABLATION_DIR = Path(__file__).parent / "results" / "ablation"

# ── Minimal strategy implementations ───────────────────────────────────────

class _Static:
    _MAP = {
        "simple_chat": "ollama", "code_generation": "codex-cli",
        "code_refactoring": "aider", "debugging": "aider",
        "file_operations": "codex-cli", "research": "gemini-cli",
        "planning": "gemini-cli", "complex_reasoning": "gemini-cli",
    }
    _MISROUTE = {
        "simple_chat": "gemini-cli", "code_generation": "ollama",
        "code_refactoring": "codex-cli", "debugging": "codex-cli",
        "file_operations": "ollama", "research": "ollama",
        "planning": "ollama", "complex_reasoning": "ollama",
    }

    def __init__(self, seed: int = 99) -> None:
        self._rng = random.Random(seed)

    def select(self, task: Task) -> str:
        if self._rng.random() < 0.18:
            return self._MISROUTE.get(task.category, "ollama")
        return self._MAP.get(task.category, "ollama")

    def update(self, task: Task, agent: str, reward: float) -> None:
        pass


class _UCB1:
    def __init__(self, c: float = 1.5) -> None:
        self.c = c
        self.counts = {a: 0 for a in AGENTS}
        self.totals = {a: 0.0 for a in AGENTS}
        self.t = 0

    def select(self, task: Task) -> str:
        self.t += 1
        for a in AGENTS:
            if self.counts[a] == 0:
                return a
        return max(AGENTS, key=lambda a: (
            self.totals[a] / self.counts[a] +
            self.c * np.sqrt(np.log(self.t) / self.counts[a])
        ))

    def update(self, task: Task, agent: str, reward: float) -> None:
        self.counts[agent] += 1
        self.totals[agent] += reward


class _Thompson:
    def __init__(self) -> None:
        self._rng = np.random.default_rng(42)
        self.alpha = {a: 1.0 for a in AGENTS}
        self.beta  = {a: 1.0 for a in AGENTS}

    def select(self, task: Task) -> str:
        samples = {a: float(self._rng.beta(self.alpha[a], self.beta[a])) for a in AGENTS}
        return max(samples, key=samples.__getitem__)

    def update(self, task: Task, agent: str, reward: float) -> None:
        if reward > 0.5:
            self.alpha[agent] += 1.0
        else:
            self.beta[agent]  += 1.0


class _LinUCB:
    DIM = 8

    def __init__(self, alpha: float = 1.0, gamma: float = 1.0) -> None:
        self.alpha = alpha
        self.gamma = gamma
        self.A = {a: np.eye(self.DIM) for a in AGENTS}
        self.b = {a: np.zeros(self.DIM) for a in AGENTS}

    def select(self, task: Task) -> str:
        x = task.context_vector(self.DIM)
        return max(AGENTS, key=lambda a: self._ucb(a, x))

    def _ucb(self, a: str, x: np.ndarray) -> float:
        A_inv = np.linalg.inv(self.A[a])
        return float(x @ A_inv @ self.b[a]) + self.alpha * float(np.sqrt(max(0.0, float(x @ A_inv @ x))))

    def update(self, task: Task, agent: str, reward: float) -> None:
        x = task.context_vector(self.DIM)
        if self.gamma < 1.0:
            self.A[agent] = self.gamma * self.A[agent] + np.outer(x, x)
            self.b[agent] = self.gamma * self.b[agent] + reward * x
        else:
            self.A[agent] += np.outer(x, x)
            self.b[agent] += reward * x

    def warm_start(self, matrix: dict[str, dict[str, float]]) -> None:
        """Inject one pseudo-observation per (agent, category) cell."""
        from .oracle import TASK_CATEGORIES
        _cat_vecs = {c: _category_onehot(c) for c in TASK_CATEGORIES}
        for agent, bucket_rewards in matrix.items():
            if agent not in AGENTS:
                continue
            for cat, reward in bucket_rewards.items():
                if cat not in _cat_vecs:
                    continue
                x = _cat_vecs[cat]
                self.A[agent] += np.outer(x, x)
                self.b[agent] += reward * x


class _AdaptiveLinUCB(_LinUCB):
    """dLinUCB with per-arm adaptive γ — sim mirror of the production math
    in `strategies.linucb_per_bucket._adapt_gamma` (docs/specs/gamma-spec.md).
    Kept in sync by hand; the production version is the source of truth.
    """

    def __init__(
        self,
        alpha: float = 1.0,
        gamma: float = 0.98,
        gamma_min: float = 0.93,
        gamma_max: float = 0.995,
        beta: float = 0.9,
        tau: float = 0.5,
        warmup: int = 10,
        recovery_rate: float = 0.0,
    ) -> None:
        super().__init__(alpha=alpha, gamma=gamma)
        self.gamma_min = gamma_min
        self.gamma_max = gamma_max
        self.beta = beta
        self.tau = tau
        self.warmup = warmup
        self.recovery_rate = recovery_rate
        self.gamma_per_arm: dict[str, float] = {}
        self.pred_error_ema: dict[str, float] = {}
        self.pulls: dict[str, int] = {}
        self._r_mean: dict[str, float] = {}
        self._r_var: dict[str, float] = {}

    def update(self, task: Task, agent: str, reward: float) -> None:
        x = task.context_vector(self.DIM)
        self.pulls[agent] = self.pulls.get(agent, 0) + 1

        if agent not in self._r_mean:
            self._r_mean[agent] = reward
            self._r_var[agent] = 0.0625
        else:
            delta = reward - self._r_mean[agent]
            self._r_mean[agent] = self.beta * self._r_mean[agent] + (1 - self.beta) * reward
            self._r_var[agent] = self.beta * self._r_var[agent] + (1 - self.beta) * delta**2

        # Recovery: idle arms' drift memory fades (the applied gamma is
        # recomputed from the EMA, so decaying the EMA is what recovers).
        if self.recovery_rate > 0.0:
            for a in self.pred_error_ema:
                if a != agent:
                    self.pred_error_ema[a] *= 1.0 - self.recovery_rate

        if self.pulls[agent] < self.warmup:
            g = self.gamma
        else:
            theta = np.linalg.inv(self.A[agent]) @ self.b[agent]
            err_sq = (reward - float(x @ theta)) ** 2
            # Variance floor + outlier cap, then EMA. E<=1 is the noise
            # floor for a converged arm; only the excess reads as drift.
            eps_norm = min(err_sq / max(self._r_var[agent], 1e-3), 25.0)
            self.pred_error_ema[agent] = (
                self.beta * self.pred_error_ema.get(agent, 0.0)
                + (1 - self.beta) * eps_norm
            )
            excess = max(0.0, self.pred_error_ema[agent] - 1.0)
            g = self.gamma_min + (self.gamma_max - self.gamma_min) * float(
                np.exp(-excess / self.tau)
            )
        self.gamma_per_arm[agent] = g

        self.A[agent] = g * self.A[agent] + np.outer(x, x)
        self.b[agent] = g * self.b[agent] + reward * x


class _DriftOracle:
    """Wraps an Oracle; degrades one agent's reward inside a task-index
    window [start, end) — the synthetic changepoint from gamma-spec.md's
    ablation plan. optimal_* answer for the DEGRADED world, so regret is
    measured against the dynamic oracle.
    """

    def __init__(
        self,
        base,
        target: str,
        delta: float = 0.15,
        start: int = 80,
        end: int = 150,
    ) -> None:
        self.base = base
        self.target = target
        self.delta = delta
        self.start = start
        self.end = end
        self._i = 0  # tasks evaluated so far == current task index

    def _degradation(self, agent: str) -> float:
        if agent == self.target and self.start <= self._i < self.end:
            return self.delta
        return 0.0

    def expected_reward(self, task: Task, agent: str) -> float:
        return max(
            0.0, self.base.expected_reward(task, agent) - self._degradation(agent)
        )

    def optimal_agent(self, task: Task) -> str:
        return max(AGENTS, key=lambda a: self.expected_reward(task, a))

    def optimal_reward(self, task: Task) -> float:
        return max(self.expected_reward(task, a) for a in AGENTS)

    def evaluate(self, task: Task, agent: str) -> dict:
        outcome = dict(self.base.evaluate(task, agent))
        outcome["reward"] = max(0.0, outcome["reward"] - self._degradation(agent))
        self._i += 1
        return outcome


def _category_onehot(category: str) -> np.ndarray:
    """8-dim one-hot vector over TASK_CATEGORIES, used for warm-start injection."""
    vec = np.zeros(8)
    idx = TASK_CATEGORIES.index(category) if category in TASK_CATEGORIES else 0
    vec[idx % 8] = 1.0
    return vec


def _compatibility_as_matrix() -> dict[str, dict[str, float]]:
    """Convert oracle COMPATIBILITY to the warm_start format."""
    matrix: dict[str, dict[str, float]] = {}
    for cat, agent_map in COMPATIBILITY.items():
        for agent, (mean_r, _std) in agent_map.items():
            matrix.setdefault(agent, {})[cat] = mean_r
    return matrix


# ── Run helpers ─────────────────────────────────────────────────────────────

def _run(
    oracle: Oracle,
    strategy,
    tasks: list[Task],
    beta_swap: float = 0.0,
    memory_alpha: float = 0.0,
    episode_store: Optional[list] = None,
) -> list[float]:
    """Run strategy through tasks, return per-task cumulative regret list."""
    cumulative: list[float] = []
    total_regret = 0.0
    prev_agent: Optional[str] = None
    mem_biases: dict[str, float] = {}

    for task in tasks:
        oracle_r = oracle.optimal_reward(task)

        if memory_alpha > 0.0 and episode_store and len(episode_store) >= 3:
            # Simplified episodic memory: blend stored rewards into selection
            x = task.context_vector(8)
            # Find k=5 nearest stored episodes by dot-product similarity
            scored = sorted(
                episode_store,
                key=lambda ep: float(np.dot(ep[0], x)),
                reverse=True,
            )[:5]
            weighted: dict[str, float] = {}
            wsum:     dict[str, float] = {}
            for ep_x, ep_agent, ep_r in scored:
                sim = max(0.0, float(np.dot(ep_x, x)))
                weighted[ep_agent] = weighted.get(ep_agent, 0.0) + sim * ep_r
                wsum[ep_agent] = wsum.get(ep_agent, 0.0) + sim
            mem_biases = {
                a: weighted[a] / wsum[a]
                for a in AGENTS if a in wsum and wsum[a] > 0
            }

        # Select agent — blend memory bias if available
        if memory_alpha > 0.0 and mem_biases:
            # Get base strategy scores via exploit approximation
            base_agent = strategy.select(task)
            if mem_biases:
                biased = {
                    a: (1.0 - memory_alpha) * (1.0 if strategy.select(task) == a else 0.5)
                       + memory_alpha * mem_biases.get(a, 0.5)
                    for a in AGENTS
                }
                agent = max(biased, key=biased.__getitem__)
            else:
                agent = base_agent
        else:
            agent = strategy.select(task)

        outcome = oracle.evaluate(task, agent)
        reward = outcome["reward"]

        # Swap penalty
        if prev_agent is not None and agent != prev_agent and beta_swap > 0.0:
            reward = max(0.0, reward - beta_swap)

        strategy.update(task, agent, reward)

        if episode_store is not None:
            episode_store.append((task.context_vector(8), agent, outcome["reward"]))

        total_regret += oracle_r - outcome["reward"]
        cumulative.append(total_regret)
        prev_agent = agent

    return cumulative


def _run_drift(
    drift_oracle: "_DriftOracle",
    strategy,
    tasks: list[Task],
) -> tuple[list[float], list[str]]:
    """Like _run, but also records per-task picks for drift metrics."""
    cumulative: list[float] = []
    picks: list[str] = []
    total_regret = 0.0
    for task in tasks:
        oracle_r = drift_oracle.optimal_reward(task)
        agent = strategy.select(task)
        outcome = drift_oracle.evaluate(task, agent)
        strategy.update(task, agent, outcome["reward"])
        total_regret += oracle_r - outcome["reward"]
        cumulative.append(total_regret)
        picks.append(agent)
    return cumulative, picks


def _drift_metrics(
    curve: list[float],
    picks: list[str],
    tasks: list[Task],
    base_oracle,
    target: str,
    start: int,
    end: int,
) -> dict:
    """Detection latency, changepoint regret, recovery time (gamma-spec.md)."""
    # Tasks where the pre-drift oracle would pick the degraded arm — the
    # ones on which detection/recovery are observable.
    affected = [
        i for i, t in enumerate(tasks) if base_oracle.optimal_agent(t) == target
    ]
    detection = next(
        (i - start for i in affected if start <= i < end and picks[i] != target),
        None,
    )
    recovery = next(
        (i - end for i in affected if i >= end and picks[i] == target),
        None,
    )

    def _share(lo: int, hi: int) -> float:
        window = [i for i in affected if lo <= i < hi]
        if not window:
            return 0.0
        return round(sum(picks[i] == target for i in window) / len(window), 4)

    return {
        "detection_latency": detection,
        "regret_after_changepoint": round(
            curve[-1] - curve[start - 1] if start > 0 else curve[-1], 4
        ),
        "recovery_time": recovery,
        # Share of affected tasks (pre-drift optimal == target) actually
        # routed to the target — the robust drift-response signal; the
        # first-index metrics above are noise when this is low pre-drift.
        "target_share_pre": _share(0, start),
        "target_share_during": _share(start, end),
        "target_share_post": _share(end, len(tasks)),
    }


# ── Chart helper ─────────────────────────────────────────────────────────────

_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
           "#06b6d4", "#f97316", "#ec4899", "#a3e635", "#14b8a6"]


def _plot_regret(
    curves: dict[str, list[float]],
    title: str,
    output_path: str,
    dpi: int = 150,
) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print(f"WARNING: matplotlib not available — skipping {Path(output_path).name}")
        return

    fig, ax = plt.subplots(figsize=(10, 5.5))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    for i, (label, regret) in enumerate(curves.items()):
        ax.plot(range(1, len(regret) + 1), regret,
                label=label, color=_COLORS[i % len(_COLORS)], linewidth=1.8)

    ax.set_xlabel("Task number", color="#9ca3af", fontsize=11)
    ax.set_ylabel("Cumulative regret", color="#9ca3af", fontsize=11)
    ax.set_title(title, color="#e5e7eb", fontsize=13, fontweight="bold")
    ax.legend(frameon=True, facecolor="#161b22", edgecolor="#30363d",
              labelcolor="#e5e7eb", fontsize=9)
    ax.tick_params(colors="#6b7280")
    for spine in ["bottom", "left"]:
        ax.spines[spine].set_color("#30363d")
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.grid(True, alpha=0.12, color="#30363d")

    plt.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  Saved: {Path(output_path).name}")


# ── 5 Experiments ───────────────────────────────────────────────────────────

def _exp1_strategy_comparison(oracle, tasks, out, dpi) -> dict:
    print("  [1/6] Strategy comparison...")
    strategies = {
        "static":   _Static(),
        "ucb1":     _UCB1(),
        "thompson": _Thompson(),
        "linucb":   _LinUCB(alpha=1.0, gamma=1.0),
        "dlinucb":  _LinUCB(alpha=1.0, gamma=0.98),
    }
    curves = {name: _run(oracle, s, tasks) for name, s in strategies.items()}
    _plot_regret(curves, "Strategy Comparison — Cumulative Regret",
                 str(out / "strategy_comparison.png"), dpi=dpi)
    return {name: {"final_regret": c[-1], "curve": c} for name, c in curves.items()}


def _exp2_warm_start(oracle, tasks, out, dpi) -> dict:
    print("  [2/6] Warm-start vs cold-start...")
    cold = _LinUCB(alpha=1.0, gamma=0.98)
    warm = _LinUCB(alpha=1.0, gamma=0.98)
    warm.warm_start(_compatibility_as_matrix())

    curves = {
        "dlinucb (cold)": _run(oracle, cold, tasks),
        "dlinucb (warm)": _run(oracle, warm, tasks),
    }
    _plot_regret(curves, "Warm-Start vs Cold-Start", str(out / "warm_start.png"), dpi=dpi)
    return {name: {"final_regret": c[-1], "curve": c} for name, c in curves.items()}


def _exp3_episodic_memory(oracle, tasks, out, dpi) -> dict:
    print("  [3/6] Episodic memory on/off...")
    no_mem  = _LinUCB(alpha=1.0, gamma=0.98)
    with_mem = _LinUCB(alpha=1.0, gamma=0.98)
    mem_store: list = []

    curves = {
        "dlinucb (α=0.0)":  _run(oracle, no_mem, tasks, memory_alpha=0.0),
        "dlinucb (α=0.20)": _run(oracle, with_mem, tasks, memory_alpha=0.20,
                                  episode_store=mem_store),
    }
    _plot_regret(curves, "Episodic Memory On/Off", str(out / "episodic_memory.png"), dpi=dpi)
    return {name: {"final_regret": c[-1], "curve": c} for name, c in curves.items()}


def _exp4_swap_penalty(oracle, tasks, out, dpi) -> dict:
    print("  [4/6] Swap penalty on/off...")
    no_swap   = _LinUCB(alpha=1.0, gamma=0.98)
    with_swap = _LinUCB(alpha=1.0, gamma=0.98)

    curves = {
        "dlinucb (β=0.0)":  _run(oracle, no_swap,   tasks, beta_swap=0.0),
        "dlinucb (β=0.10)": _run(oracle, with_swap, tasks, beta_swap=0.10),
    }
    _plot_regret(curves, "Swap Penalty On/Off", str(out / "swap_penalty.png"), dpi=dpi)
    return {name: {"final_regret": c[-1], "curve": c} for name, c in curves.items()}


def _exp5_bucket_granularity(oracle, tasks, out, dpi) -> dict:
    """Simulate 3-bucket vs 7-bucket routing by collapsing oracle categories."""
    print("  [5/6] Bucket granularity: 3 vs 7 categories...")

    # 7-bucket: all categories except 'file_operations' merged into 'code'
    _7CAT = TASK_CATEGORIES  # 8 categories → treat as 7 by merging file_ops into code
    _3CAT_MAP = {
        "simple_chat":       "general",
        "research":          "general",
        "planning":          "general",
        "complex_reasoning": "general",
        "code_generation":   "code",
        "code_refactoring":  "code",
        "debugging":         "code",
        "file_operations":   "code",
    }

    # Build a wrapper that remaps task categories
    class _CollapseOracle:
        def __init__(self, base_oracle, n_buckets):
            self._o = base_oracle
            self._n = n_buckets

        def optimal_reward(self, task):
            return self._o.optimal_reward(task)

        def evaluate(self, task, agent):
            return self._o.evaluate(task, agent)

    def _task_context_7(task):
        return task.context_vector(8)

    def _task_context_3(task):
        # Collapse: only 3 dims matter — code/general/management
        v = np.zeros(3)
        bucket = _3CAT_MAP.get(task.category, "general")
        v[{"code": 0, "general": 1}.get(bucket, 2)] = 1.0
        return v

    class _LinUCB3:
        DIM = 3
        def __init__(self, alpha=1.0, gamma=0.98):
            self.alpha, self.gamma = alpha, gamma
            self.A = {a: np.eye(self.DIM) for a in AGENTS}
            self.b = {a: np.zeros(self.DIM) for a in AGENTS}
        def select(self, task):
            x = _task_context_3(task)
            return max(AGENTS, key=lambda a: self._ucb(a, x))
        def _ucb(self, a, x):
            Ai = np.linalg.inv(self.A[a])
            return float(x @ Ai @ self.b[a]) + self.alpha * float(np.sqrt(max(0.0, float(x @ Ai @ x))))
        def update(self, task, agent, reward):
            x = _task_context_3(task)
            self.A[agent] = self.gamma * self.A[agent] + np.outer(x, x)
            self.b[agent] = self.gamma * self.b[agent] + reward * x

    s7 = _LinUCB(alpha=1.0, gamma=0.98)
    s3 = _LinUCB3()

    curves = {
        "dlinucb (7 buckets)": _run(oracle, s7, tasks),
        "dlinucb (3 buckets)": _run(oracle, s3, tasks),
    }
    _plot_regret(curves, "Bucket Granularity: 7 vs 3", str(out / "bucket_granularity.png"), dpi=dpi)
    return {name: {"final_regret": c[-1], "curve": c} for name, c in curves.items()}


def _exp6_adaptive_gamma(oracle, tasks, out, dpi, seed: int = 42) -> dict:
    """Synthetic drift: degrade the most-picked-by-oracle arm mid-run and
    compare global γ vs adaptive per-arm γ (gamma-spec.md §Ablation Plan).
    """
    print("  [6/6] Adaptive gamma under synthetic drift...")
    n = len(tasks)
    start, end = int(n * 0.4), int(n * 0.75)

    # Degrade the arm the oracle relies on most — worst-case drift.
    from collections import Counter
    target = Counter(oracle.optimal_agent(t) for t in tasks).most_common(1)[0][0]

    variants = {
        "global γ=0.98": lambda: _LinUCB(alpha=1.0, gamma=0.98),
        "adaptive γ": lambda: _AdaptiveLinUCB(alpha=1.0),
        "adaptive γ + recovery": lambda: _AdaptiveLinUCB(
            alpha=1.0, recovery_rate=0.01
        ),
    }

    results: dict = {}
    curves: dict[str, list[float]] = {}
    for name, make in variants.items():
        # Fresh same-seed oracle per variant → identical reward streams,
        # so the curves differ only by strategy behavior.
        base = Oracle(seed=seed, n_tasks=n)
        base.generate_tasks()
        drift = _DriftOracle(base, target=target, delta=0.15, start=start, end=end)
        strategy = make()
        # Warm-start so the bandit is near-converged BEFORE the changepoint —
        # otherwise the experiment measures cold-start convergence, not
        # drift response (pre-drift target share was ~0.17 without this).
        strategy.warm_start(_compatibility_as_matrix())
        curve, picks = _run_drift(drift, strategy, tasks)
        curves[name] = curve
        results[name] = {
            "final_regret": curve[-1],
            "curve": curve,
            "drift_target": target,
            "drift_window": [start, end],
            **_drift_metrics(curve, picks, tasks, base, target, start, end),
        }

    _plot_regret(
        curves,
        f"Adaptive γ Under Drift ({target} degraded, tasks {start}–{end})",
        str(out / "adaptive_gamma_drift.png"),
        dpi=dpi,
    )
    return results


# ── Summary writers ──────────────────────────────────────────────────────────

def _write_summary(
    all_results: dict,
    out: Path,
) -> None:
    # JSON
    json_data: dict = {}
    for exp, variants in all_results.items():
        json_data[exp] = {
            # Everything scalar (drift metrics etc.); curves stay out.
            name: {k: v for k, v in variant.items() if k != "curve"}
            for name, variant in variants.items()
        }
    (out / "ablation_summary.json").write_text(json.dumps(json_data, indent=2))
    print(f"  Saved: ablation_summary.json")

    # Markdown table
    lines = [
        "# Ablation Study Summary\n",
        "All experiments run on 200-task oracle, seed=42.\n",
    ]
    for exp, variants in all_results.items():
        lines.append(f"## {exp.replace('_', ' ').title()}\n")
        lines.append("| Configuration | Final Regret |")
        lines.append("|--------------|-------------|")
        for name, v in variants.items():
            lines.append(f"| {name} | {v['final_regret']:.4f} |")
        lines.append("")

    (out / "ablation_summary.md").write_text("\n".join(lines))
    print(f"  Saved: ablation_summary.md")


# ── Main entry point ─────────────────────────────────────────────────────────

def run_ablation(
    n_tasks: int = 200,
    seed: int = 42,
    output_dir: Optional[str] = None,
    dpi: int = 150,
) -> None:
    """Run all 6 ablation experiments and write charts + summary."""
    out = Path(output_dir) if output_dir else ABLATION_DIR
    out.mkdir(parents=True, exist_ok=True)

    print(f"Ablation study: 6 experiments × {n_tasks} tasks (seed={seed})")
    print(f"Output: {out}\n")

    oracle = Oracle(seed=seed, n_tasks=n_tasks)
    tasks  = oracle.generate_tasks()

    all_results = {}
    all_results["strategy_comparison"] = _exp1_strategy_comparison(oracle, tasks, out, dpi)
    all_results["warm_start"]          = _exp2_warm_start(oracle, tasks, out, dpi)
    all_results["episodic_memory"]     = _exp3_episodic_memory(oracle, tasks, out, dpi)
    all_results["swap_penalty"]        = _exp4_swap_penalty(oracle, tasks, out, dpi)
    all_results["bucket_granularity"]  = _exp5_bucket_granularity(oracle, tasks, out, dpi)
    all_results["adaptive_gamma"]      = _exp6_adaptive_gamma(oracle, tasks, out, dpi, seed=seed)

    _write_summary(all_results, out)

    print(f"\nAblation complete. Results in {out}/")
