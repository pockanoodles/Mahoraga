"""
Regret Curve Tracker for Mahoraga Benchmark Harness.

Tracks per-step regret: the difference between the oracle-optimal reward
and the reward of the agent actually selected.  Cumulative regret should
be sublinear over time — that proves the bandit is learning.

Generates:
  - regret_curve.png:   cumulative regret for all 4 strategies
  - regret_summary.json: final regret, regret growth rate, convergence flag

Usage:
    tracker = RegretTracker(strategies=["static", "ucb1", "thompson", "linucb"])
    tracker.record(step=0, strategy="linucb", actual_reward=0.72, oracle_reward=0.88)
    ...
    tracker.plot("benchmark/results/regret_curve.png")
    tracker.summary()
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np


class RegretTracker:
    """
    Accumulates per-step regret for multiple routing strategies.

    regret_t = oracle_reward_t - actual_reward_t

    A good bandit should show:
      - Cumulative regret growing sublinearly (flattening over time)
      - Per-step regret decreasing toward 0 as the bandit learns
      - Lower total regret than non-contextual baselines
    """

    def __init__(self, strategies: list[str]):
        self.strategies = strategies
        self._regret: dict[str, list[float]] = {s: [] for s in strategies}
        self._actual: dict[str, list[float]] = {s: [] for s in strategies}
        self._oracle: dict[str, list[float]] = {s: [] for s in strategies}

    def record(self, step: int, strategy: str, actual_reward: float, oracle_reward: float) -> None:
        """Record one routing decision's regret."""
        regret = max(0.0, oracle_reward - actual_reward)
        self._regret[strategy].append(regret)
        self._actual[strategy].append(actual_reward)
        self._oracle[strategy].append(oracle_reward)

    def cumulative_regret(self, strategy: str) -> np.ndarray:
        return np.cumsum(self._regret[strategy])

    def total_regret(self, strategy: str) -> float:
        return float(sum(self._regret[strategy]))

    def mean_per_step_regret(self, strategy: str, window: int = 20) -> np.ndarray:
        """Rolling average per-step regret."""
        arr = np.array(self._regret[strategy])
        if len(arr) < window:
            return arr
        kernel = np.ones(window) / window
        return np.convolve(arr, kernel, mode="valid")

    def is_sublinear(self, strategy: str, threshold: float = 0.8) -> bool:
        """Test if cumulative regret growth is sublinear (beta < threshold)."""
        cum = self.cumulative_regret(strategy)
        if len(cum) < 20 or cum[-1] <= 0:
            return False
        start = max(1, len(cum) // 10)
        t = np.arange(start, len(cum)) + 1
        log_t = np.log(t)
        log_r = np.log(np.maximum(cum[start:], 1e-10))
        if np.std(log_t) < 1e-10:
            return False
        beta = float(np.polyfit(log_t, log_r, 1)[0])
        return beta < threshold

    def regret_growth_exponent(self, strategy: str) -> float:
        """Estimate beta where cumulative_regret ~ t^beta."""
        cum = self.cumulative_regret(strategy)
        if len(cum) < 20 or cum[-1] <= 0:
            return float("nan")
        start = max(1, len(cum) // 10)
        t = np.arange(start, len(cum)) + 1
        log_t = np.log(t)
        log_r = np.log(np.maximum(cum[start:], 1e-10))
        if np.std(log_t) < 1e-10:
            return float("nan")
        beta = float(np.polyfit(log_t, log_r, 1)[0])
        return beta

    def summary(self) -> dict:
        """Generate summary statistics for all strategies."""
        result = {}
        for s in self.strategies:
            n = len(self._regret[s])
            if n == 0:
                continue
            result[s] = {
                "n_steps": n,
                "total_regret": round(self.total_regret(s), 4),
                "mean_regret_per_step": round(float(np.mean(self._regret[s])), 4),
                "mean_actual_reward": round(float(np.mean(self._actual[s])), 4),
                "mean_oracle_reward": round(float(np.mean(self._oracle[s])), 4),
                "regret_growth_exponent": round(self.regret_growth_exponent(s), 3),
                "is_sublinear": self.is_sublinear(s),
                "mean_regret_first_20pct": round(float(np.mean(self._regret[s][:max(1, n // 5)])), 4),
                "mean_regret_last_20pct": round(float(np.mean(self._regret[s][-(max(1, n // 5)):])), 4),
            }
        return result

    def plot(self, output_path: str, title: str = "Cumulative Regret") -> None:
        """Generate dual-panel regret chart (cumulative + learning curve)."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))
        fig.patch.set_facecolor("#0d1117")

        colors = {
            "static": "#6b7280", "ucb1": "#f59e0b",
            "thompson": "#8b5cf6", "linucb": "#10b981",
        }
        labels = {
            "static": "Static (baseline)", "ucb1": "UCB1",
            "thompson": "Thompson Sampling", "linucb": "LinUCB (ours)",
        }

        # Left panel: cumulative regret
        ax1.set_facecolor("#0d1117")
        for s in self.strategies:
            cum = self.cumulative_regret(s)
            if len(cum) == 0:
                continue
            lw = 2.5 if s == "linucb" else 1.5
            a = 1.0 if s == "linucb" else 0.7
            ax1.plot(range(len(cum)), cum, color=colors.get(s, "#fff"),
                     label=labels.get(s, s), linewidth=lw, alpha=a)
            beta = self.regret_growth_exponent(s)
            if not np.isnan(beta):
                ax1.annotate(f"b={beta:.2f}", xy=(len(cum)-1, cum[-1]),
                             fontsize=8, color=colors.get(s, "#fff"), alpha=0.8)

        ax1.set_xlabel("Step", color="#9ca3af", fontsize=10)
        ax1.set_ylabel("Cumulative Regret", color="#9ca3af", fontsize=10)
        ax1.set_title(title, color="#e5e7eb", fontsize=12, fontweight="bold")
        ax1.legend(loc="upper left", frameon=True, facecolor="#161b22",
                   edgecolor="#30363d", labelcolor="#e5e7eb", fontsize=9)
        ax1.tick_params(colors="#6b7280")
        for spine in ["bottom", "left"]:
            ax1.spines[spine].set_color("#30363d")
        for spine in ["top", "right"]:
            ax1.spines[spine].set_visible(False)
        ax1.grid(True, alpha=0.15, color="#30363d")

        # Right panel: rolling per-step regret
        ax2.set_facecolor("#0d1117")
        window = 20
        for s in self.strategies:
            smoothed = self.mean_per_step_regret(s, window=window)
            if len(smoothed) == 0:
                continue
            lw = 2.5 if s == "linucb" else 1.5
            a = 1.0 if s == "linucb" else 0.7
            ax2.plot(range(len(smoothed)), smoothed, color=colors.get(s, "#fff"),
                     label=labels.get(s, s), linewidth=lw, alpha=a)

        ax2.set_xlabel("Step", color="#9ca3af", fontsize=10)
        ax2.set_ylabel(f"Per-Step Regret ({window}-step avg)", color="#9ca3af", fontsize=10)
        ax2.set_title("Learning Curve (should trend to 0)", color="#e5e7eb", fontsize=12, fontweight="bold")
        ax2.legend(loc="upper right", frameon=True, facecolor="#161b22",
                   edgecolor="#30363d", labelcolor="#e5e7eb", fontsize=9)
        ax2.tick_params(colors="#6b7280")
        for spine in ["bottom", "left"]:
            ax2.spines[spine].set_color("#30363d")
        for spine in ["top", "right"]:
            ax2.spines[spine].set_visible(False)
        ax2.grid(True, alpha=0.15, color="#30363d")

        plt.tight_layout()
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        print(f"  Saved regret curve: {output_path}")

    def save_json(self, output_path: str) -> None:
        """Save full regret data + summary to JSON."""
        data = {
            "summary": self.summary(),
            "per_step": {
                s: {"regret": self._regret[s], "actual_reward": self._actual[s],
                    "oracle_reward": self._oracle[s]}
                for s in self.strategies
            },
        }
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  Saved regret data:  {output_path}")
