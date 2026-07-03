"""
Pareto Knee-Point Hyperparameter Calibration for Mahoraga's LinUCB router.

Sweeps (alpha, gamma, beta_swap) over a 4×5×5 grid (100 configs), runs each
through a 200-task simulated benchmark, and identifies the Pareto knee-point
that simultaneously minimises cumulative regret and swap-cost waste.

Reference: ParetoBandit (March 2026) — multi-objective bandit calibration.

Outputs (benchmark_results/):
    pareto_sweep.json   — all 100 run results
    pareto_front.png    — scatter plot with Pareto front + knee highlighted

The winning config is written to ~/.mahoraga-v2/tuned_hyperparams.json so
BanditRouter can load it on next startup.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from typing import Optional

import numpy as np

try:
    from .oracle import AGENTS, Oracle, Task
except ImportError:
    from oracle import AGENTS, Oracle, Task  # type: ignore[no-redef]

RESULTS_DIR = Path(__file__).parent / "results"
TUNED_HYPERPARAMS_PATH = Path.home() / ".mahoraga-v2" / "tuned_hyperparams.json"

# ── Sweep grid ─────────────────────────────────────────────────────────────
ALPHA_GRID: list[float]    = [0.5, 1.0, 1.5, 2.0]
GAMMA_GRID: list[float]    = [0.95, 0.97, 0.98, 0.99, 1.0]
BETA_SWAP_GRID: list[float] = [0.0, 0.05, 0.10, 0.15, 0.20]


# ── Result dataclass ────────────────────────────────────────────────────────

@dataclass
class SweepResult:
    alpha: float
    gamma: float
    beta_swap: float
    cumulative_regret: float
    swap_cost_waste: float
    convergence_speed: int   # number of tasks until exploration rate < 15 %


# ── Discounted LinUCB (for the sweep only — no production dependencies) ────

class _dLinUCB:
    """Minimal dLinUCB implementation for the parameter sweep.

    Uses the same oracle context vectors as harness.py (dim=8) so results
    are directly comparable to the strategy comparison chart.
    """

    DIM = 8

    def __init__(self, alpha: float, gamma: float) -> None:
        self.alpha = alpha
        self.gamma = gamma
        self.A: dict[str, np.ndarray] = {a: np.eye(self.DIM) for a in AGENTS}
        self.b: dict[str, np.ndarray] = {a: np.zeros(self.DIM) for a in AGENTS}
        self.t = 0
        self._explore_count = 0

    def select(self, task: Task) -> tuple[str, bool]:
        """Return (agent, is_exploration).

        Exploration = the UCB-argmax differs from the exploit-argmax.
        """
        x = task.context_vector(self.DIM)
        self.t += 1

        ucbs: dict[str, float] = {}
        exploits: dict[str, float] = {}
        for a in AGENTS:
            A_inv = np.linalg.inv(self.A[a])
            theta = A_inv @ self.b[a]
            exploit = float(x @ theta)
            explore = self.alpha * float(np.sqrt(max(0.0, float(x @ A_inv @ x))))
            ucbs[a] = exploit + explore
            exploits[a] = exploit

        best = max(AGENTS, key=lambda a: ucbs[a])
        exploit_best = max(AGENTS, key=lambda a: exploits[a])
        is_exploration = best != exploit_best
        if is_exploration:
            self._explore_count += 1
        return best, is_exploration

    def update(self, task: Task, agent: str, reward: float) -> None:
        x = task.context_vector(self.DIM)
        if self.gamma < 1.0:
            self.A[agent] = self.gamma * self.A[agent] + np.outer(x, x)
            self.b[agent] = self.gamma * self.b[agent] + reward * x
        else:
            self.A[agent] += np.outer(x, x)
            self.b[agent] += reward * x

    def exploration_rate(self) -> float:
        return self._explore_count / self.t if self.t > 0 else 1.0


# ── Single config runner ────────────────────────────────────────────────────

def run_single_config(
    alpha: float,
    gamma: float,
    beta_swap: float,
    n_tasks: int = 200,
    seed: int = 42,
) -> SweepResult:
    """Run one (alpha, gamma, beta_swap) configuration through n_tasks tasks."""
    oracle = Oracle(seed=seed, n_tasks=n_tasks)
    tasks = oracle.generate_tasks()
    strategy = _dLinUCB(alpha=alpha, gamma=gamma)

    cumulative_regret = 0.0
    swap_cost_waste = 0.0
    convergence_speed = n_tasks   # default: never converged within budget
    prev_agent: Optional[str] = None
    converged = False

    # Rolling 20-task window for exploration-rate convergence check
    explore_window: list[int] = []

    for i, task in enumerate(tasks):
        oracle_r = oracle.optimal_reward(task)
        agent, is_exploration = strategy.select(task)
        outcome = oracle.evaluate(task, agent)

        # Measure regret against oracle — before swap penalty
        cumulative_regret += oracle_r - outcome["reward"]

        # Swap penalty: agent changed from previous task
        if prev_agent is not None and agent != prev_agent:
            swap_cost_waste += beta_swap

        # Update bandit with swap-penalised reward
        penalised_reward = max(0.0, outcome["reward"] - (beta_swap if prev_agent and agent != prev_agent else 0.0))
        strategy.update(task, agent, penalised_reward)

        prev_agent = agent

        # Convergence tracking
        explore_window.append(1 if is_exploration else 0)
        if len(explore_window) > 20:
            explore_window.pop(0)
        if not converged and len(explore_window) == 20:
            if sum(explore_window) / 20 < 0.15:
                convergence_speed = i + 1
                converged = True

    return SweepResult(
        alpha=alpha,
        gamma=gamma,
        beta_swap=beta_swap,
        cumulative_regret=round(cumulative_regret, 4),
        swap_cost_waste=round(swap_cost_waste, 4),
        convergence_speed=convergence_speed,
    )


# ── Pareto front + knee point ───────────────────────────────────────────────

def find_pareto_front(results: list[SweepResult]) -> list[SweepResult]:
    """Return configs not dominated on (cumulative_regret, swap_cost_waste)."""
    pareto: list[SweepResult] = []
    for r in results:
        dominated = any(
            o.cumulative_regret <= r.cumulative_regret
            and o.swap_cost_waste <= r.swap_cost_waste
            and (o.cumulative_regret < r.cumulative_regret or o.swap_cost_waste < r.swap_cost_waste)
            for o in results
        )
        if not dominated:
            pareto.append(r)
    return pareto


def find_knee_point(pareto: list[SweepResult]) -> SweepResult:
    """Pick the config closest to the normalised origin — the knee of the Pareto curve."""
    if len(pareto) == 1:
        return pareto[0]

    regrets = [r.cumulative_regret for r in pareto]
    swaps   = [r.swap_cost_waste   for r in pareto]

    r_min, r_max = min(regrets), max(regrets)
    s_min, s_max = min(swaps),   max(swaps)
    r_range = r_max - r_min or 1.0
    s_range = s_max - s_min or 1.0

    return min(
        pareto,
        key=lambda r: ((r.cumulative_regret - r_min) / r_range) ** 2
                    + ((r.swap_cost_waste   - s_min) / s_range) ** 2,
    )


# ── Main sweep entry point ──────────────────────────────────────────────────

def run_pareto_sweep(
    n_tasks: int = 200,
    seed: int = 42,
    output_dir: Optional[str] = None,
    dpi: int = 150,
) -> SweepResult:
    """Run the full 100-config Pareto sweep and return the knee-point config."""
    out = Path(output_dir) if output_dir else RESULTS_DIR
    out.mkdir(parents=True, exist_ok=True)

    configs = list(product(ALPHA_GRID, GAMMA_GRID, BETA_SWAP_GRID))
    total = len(configs)
    print(f"Pareto sweep: {total} configs × {n_tasks} tasks = {total * n_tasks:,} simulated tasks")

    all_results: list[SweepResult] = []
    for i, (alpha, gamma, beta_swap) in enumerate(configs):
        result = run_single_config(alpha, gamma, beta_swap, n_tasks=n_tasks, seed=seed)
        all_results.append(result)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{total} configs done...")

    # Save raw results
    sweep_path = out / "pareto_sweep.json"
    sweep_path.write_text(json.dumps([asdict(r) for r in all_results], indent=2))
    print(f"\nSaved: {sweep_path}")

    # Compute Pareto front + knee
    pareto = find_pareto_front(all_results)
    knee   = find_knee_point(pareto)

    # Plot
    _plot_pareto(all_results, pareto, knee, str(out / "pareto_front.png"), dpi=dpi)

    # Persist winning config
    TUNED_HYPERPARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tuned = {"alpha": knee.alpha, "gamma": knee.gamma, "beta_swap": knee.beta_swap}
    TUNED_HYPERPARAMS_PATH.write_text(json.dumps(tuned, indent=2))

    print(f"\n{'=' * 52}")
    print(f"  Pareto front: {len(pareto)} non-dominated configs")
    print(f"  Knee-point (best regret/swap-waste balance):")
    print(f"    alpha={knee.alpha}  gamma={knee.gamma}  beta_swap={knee.beta_swap}")
    print(f"    regret={knee.cumulative_regret:.4f}  swap_waste={knee.swap_cost_waste:.4f}"
          f"  convergence={knee.convergence_speed} tasks")
    print(f"  Written to: {TUNED_HYPERPARAMS_PATH}")
    print(f"{'=' * 52}")

    return knee


# ── Plot ────────────────────────────────────────────────────────────────────

def _plot_pareto(
    all_results: list[SweepResult],
    pareto: list[SweepResult],
    knee: SweepResult,
    output_path: str,
    dpi: int = 150,
) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not available — skipping pareto_front.png")
        return

    fig, ax = plt.subplots(figsize=(9, 6))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    # All configs
    ax.scatter(
        [r.swap_cost_waste for r in all_results],
        [r.cumulative_regret for r in all_results],
        c="#4b5563", alpha=0.35, s=18, label="All configs", zorder=1,
    )

    # Pareto front
    pareto_sorted = sorted(pareto, key=lambda r: r.swap_cost_waste)
    ax.scatter(
        [r.swap_cost_waste for r in pareto_sorted],
        [r.cumulative_regret for r in pareto_sorted],
        c="#3b82f6", s=50, label="Pareto front", zorder=2,
    )
    ax.plot(
        [r.swap_cost_waste for r in pareto_sorted],
        [r.cumulative_regret for r in pareto_sorted],
        c="#3b82f6", linewidth=1.2, alpha=0.6, zorder=2,
    )

    # Knee point
    ax.scatter(
        [knee.swap_cost_waste], [knee.cumulative_regret],
        c="#ef4444", s=220, marker="*",
        label=f"Knee  α={knee.alpha} γ={knee.gamma} β={knee.beta_swap}",
        zorder=3,
    )

    ax.set_xlabel("Swap-Cost Waste (total)", color="#9ca3af", fontsize=11)
    ax.set_ylabel("Cumulative Regret", color="#9ca3af", fontsize=11)
    ax.set_title("Pareto Front: Regret vs Swap-Cost Waste", color="#e5e7eb",
                 fontsize=13, fontweight="bold")
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
    print(f"Saved: {output_path}")
