"""
Mahoraga Live Routing Report — orch benchmark live-report

Reads routing_decisions.db and prints a text report of real traffic:
  - Per-agent and per-bucket breakdowns
  - Routing health (exploration rate, reward trend)
  - Warm/cold impact summary

Also generates 3 charts:
  live_report/reward_over_time.png    — rolling 20-task avg reward per agent
  live_report/exploration_rate.png    — exploration rate over time
  live_report/bucket_distribution.png — pie chart of task distribution by bucket

Requires ≥ 20 routing decisions in the DB.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

DEFAULT_DB = Path.home() / ".mahoraga" / "routing_decisions.db"
LIVE_REPORT_DIR = Path(__file__).parent / "results" / "live_report"


# ── Data loading ─────────────────────────────────────────────────────────────

def _load_decisions(db_path: Path) -> list[dict]:
    """Load all rows from the decisions table ordered by id."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM decisions ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _parse_ts(ts: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None


# ── Report computation ────────────────────────────────────────────────────────

def _compute_report(rows: list[dict]) -> dict:
    n = len(rows)
    total_wall   = sum(r.get("latency_s") or 0.0 for r in rows)
    total_cost   = sum(r.get("cost_usd") or 0.0 for r in rows)

    # Timestamps
    tss = [_parse_ts(r["timestamp"]) for r in rows if r.get("timestamp")]
    tss = [t for t in tss if t]
    period_start = tss[0].strftime("%Y-%m-%d") if tss else "unknown"
    period_end   = tss[-1].strftime("%Y-%m-%d") if tss else "unknown"

    # Per-agent
    agent_tasks:   dict[str, int]   = defaultdict(int)
    agent_rewards: dict[str, list]  = defaultdict(list)
    agent_wall:    dict[str, list]  = defaultdict(list)
    agent_cost:    dict[str, float] = defaultdict(float)

    for r in rows:
        a = r.get("selected_agent") or "unknown"
        agent_tasks[a] += 1
        if r.get("reward") is not None:
            agent_rewards[a].append(r["reward"])
        if r.get("latency_s") is not None:
            agent_wall[a].append(r["latency_s"])
        agent_cost[a] += r.get("cost_usd") or 0.0

    # Exploration rate: decisions where reward < mean reward (rough proxy)
    # A better proxy: strategy == "explore" but we don't log that; use reward < 0.5
    rewards_all = [r.get("reward") for r in rows if r.get("reward") is not None]
    if rewards_all:
        mean_r = sum(rewards_all) / len(rewards_all)
        exploration_count = sum(1 for v in rewards_all if v < mean_r * 0.85)
        exploration_rate = exploration_count / n
    else:
        exploration_rate = 0.0

    # Per-bucket (task_goal keyword heuristic — only if no explicit bucket)
    bucket_tasks: dict[str, int] = defaultdict(int)
    bucket_rewards: dict[str, list] = defaultdict(list)
    bucket_top: dict[str, dict] = defaultdict(lambda: defaultdict(float))

    for r in rows:
        goal  = (r.get("task_goal") or "").lower()
        if any(w in goal for w in ["code", "function", "class", "implement", "write", "refactor"]):
            bucket = "code"
        elif any(w in goal for w in ["fix", "bug", "error", "crash", "debug"]):
            bucket = "debug"
        elif any(w in goal for w in ["plan", "design", "architect", "outline"]):
            bucket = "plan"
        elif any(w in goal for w in ["research", "explain", "what", "how", "why", "compare"]):
            bucket = "research"
        else:
            bucket = "general"

        agent = r.get("selected_agent") or "unknown"
        bucket_tasks[bucket] += 1
        if r.get("reward") is not None:
            bucket_rewards[bucket].append(r["reward"])
            bucket_top[bucket][agent] += r["reward"]

    return {
        "period_start": period_start,
        "period_end":   period_end,
        "n_tasks": n,
        "total_wall_s": total_wall,
        "total_cost_usd": total_cost,
        "exploration_rate": round(exploration_rate, 4),
        "agent_tasks":  dict(agent_tasks),
        "agent_rewards": {a: (sum(v) / len(v)) for a, v in agent_rewards.items() if v},
        "agent_wall":   {a: (sum(v) / len(v)) for a, v in agent_wall.items() if v},
        "agent_cost":   dict(agent_cost),
        "bucket_tasks":   dict(bucket_tasks),
        "bucket_rewards": {b: (sum(v) / len(v)) for b, v in bucket_rewards.items() if v},
        "bucket_top_agent": {
            b: max(agents, key=agents.__getitem__)
            for b, agents in bucket_top.items() if agents
        },
        "rows": rows,   # kept for chart generation; stripped from JSON export
    }


# ── Text printer ──────────────────────────────────────────────────────────────

def _print_report(rpt: dict) -> None:
    n   = rpt["n_tasks"]
    sep = "=" * 44

    print(f"\n{sep}")
    print(f"  Mahoraga Live Routing Report")
    print(sep)
    print(f"  Period   : {rpt['period_start']} to {rpt['period_end']}")
    print(f"  Tasks    : {n}")
    print(f"  Wall time: {rpt['total_wall_s']:.1f}s total")
    total_cost = rpt["total_cost_usd"]
    print(f"  Cost     : ${total_cost:.4f} total")
    print()

    print("Per-agent breakdown:")
    for agent, count in sorted(rpt["agent_tasks"].items(), key=lambda x: -x[1]):
        avg_r = rpt["agent_rewards"].get(agent, 0.0)
        avg_w = rpt["agent_wall"].get(agent, 0.0)
        cost  = rpt["agent_cost"].get(agent, 0.0)
        cost_str = f"  ${cost:.2f}" if cost > 0 else ""
        print(f"  {agent:<14} {count:4d} tasks  "
              f"avg_reward={avg_r:.2f}  avg_wall={avg_w:.1f}s{cost_str}")

    print()
    print("Per-bucket breakdown:")
    for bucket, count in sorted(rpt["bucket_tasks"].items(), key=lambda x: -x[1]):
        avg_r = rpt["bucket_rewards"].get(bucket, 0.0)
        top   = rpt["bucket_top_agent"].get(bucket, "-")
        print(f"  {bucket:<12} {count:4d} tasks  "
              f"top_agent={top:<14}  avg_reward={avg_r:.2f}")

    print()
    rate = rpt["exploration_rate"]
    health = "healthy, <15%" if rate < 0.15 else "HIGH (>15%)"
    print("Routing health:")
    print(f"  Exploration rate : {rate:.1%} ({health})")
    print()


# ── Charts ───────────────────────────────────────────────────────────────────

def _plot_reward_over_time(rows: list[dict], out: Path, dpi: int) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not available — skipping reward_over_time.png")
        return

    agents = sorted({r.get("selected_agent") for r in rows if r.get("selected_agent")})
    colors = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"]
    window = 20

    fig, ax = plt.subplots(figsize=(11, 5))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    for i, agent in enumerate(agents):
        agent_rows = [(j, r.get("reward") or 0.0)
                      for j, r in enumerate(rows) if r.get("selected_agent") == agent]
        if len(agent_rows) < 3:
            continue
        idxs, rews = zip(*agent_rows)
        # Rolling average
        rolled: list[float] = []
        for k in range(len(rews)):
            start = max(0, k - window + 1)
            rolled.append(sum(rews[start:k+1]) / (k - start + 1))
        ax.plot(idxs, rolled, label=agent,
                color=colors[i % len(colors)], linewidth=1.8, alpha=0.85)

    ax.set_xlabel("Task index", color="#9ca3af", fontsize=11)
    ax.set_ylabel(f"Rolling {window}-task avg reward", color="#9ca3af", fontsize=11)
    ax.set_title("Reward Over Time (per agent)", color="#e5e7eb",
                 fontsize=13, fontweight="bold")
    ax.legend(frameon=True, facecolor="#161b22", edgecolor="#30363d",
              labelcolor="#e5e7eb", fontsize=9)
    ax.tick_params(colors="#6b7280")
    ax.set_ylim(0, 1.05)
    for spine in ["bottom", "left"]:
        ax.spines[spine].set_color("#30363d")
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.grid(True, alpha=0.12, color="#30363d")
    plt.tight_layout()
    fig.savefig(str(out / "reward_over_time.png"), dpi=dpi, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  Saved: reward_over_time.png")


def _plot_exploration_rate(rows: list[dict], out: Path, dpi: int) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not available — skipping exploration_rate.png")
        return

    window = 20
    rewards = [r.get("reward") or 0.0 for r in rows]
    # Exploration proxy: reward below 85% of rolling mean
    rolling_mean: list[float] = []
    for k in range(len(rewards)):
        start = max(0, k - window + 1)
        rolling_mean.append(sum(rewards[start:k+1]) / (k - start + 1))
    explore = [1 if rewards[k] < rolling_mean[k] * 0.85 else 0
               for k in range(len(rewards))]
    explore_rate: list[float] = []
    for k in range(len(explore)):
        start = max(0, k - window + 1)
        explore_rate.append(sum(explore[start:k+1]) / (k - start + 1))

    fig, ax = plt.subplots(figsize=(11, 4))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")
    ax.plot(range(len(explore_rate)), explore_rate,
            color="#3b82f6", linewidth=1.8, label="Exploration rate")
    ax.axhline(0.15, color="#ef4444", linewidth=1, linestyle="--",
               alpha=0.7, label="15% threshold")
    ax.set_xlabel("Task index", color="#9ca3af", fontsize=11)
    ax.set_ylabel("Exploration rate", color="#9ca3af", fontsize=11)
    ax.set_title("Exploration Rate Over Time", color="#e5e7eb",
                 fontsize=13, fontweight="bold")
    ax.legend(frameon=True, facecolor="#161b22", edgecolor="#30363d",
              labelcolor="#e5e7eb", fontsize=9)
    ax.tick_params(colors="#6b7280")
    ax.set_ylim(0, 1.05)
    for spine in ["bottom", "left"]:
        ax.spines[spine].set_color("#30363d")
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.grid(True, alpha=0.12, color="#30363d")
    plt.tight_layout()
    fig.savefig(str(out / "exploration_rate.png"), dpi=dpi, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  Saved: exploration_rate.png")


def _plot_bucket_distribution(bucket_tasks: dict[str, int], out: Path, dpi: int) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARNING: matplotlib not available — skipping bucket_distribution.png")
        return

    if not bucket_tasks:
        return

    labels = list(bucket_tasks.keys())
    sizes  = list(bucket_tasks.values())
    colors = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
              "#06b6d4", "#f97316", "#ec4899"]

    fig, ax = plt.subplots(figsize=(7, 7))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, colors=colors[:len(labels)],
        autopct="%1.1f%%", startangle=90,
        textprops={"color": "#e5e7eb", "fontsize": 10},
    )
    for at in autotexts:
        at.set_color("#0d1117")
        at.set_fontsize(9)
    ax.set_title("Task Distribution by Bucket", color="#e5e7eb",
                 fontsize=13, fontweight="bold")
    plt.tight_layout()
    fig.savefig(str(out / "bucket_distribution.png"), dpi=dpi, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  Saved: bucket_distribution.png")


# ── Main entry point ──────────────────────────────────────────────────────────

def run_live_report(
    db_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    as_json: bool = False,
    dpi: int = 150,
) -> None:
    db = Path(db_path) if db_path else DEFAULT_DB
    out = Path(output_dir) if output_dir else LIVE_REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)

    rows = _load_decisions(db)

    if len(rows) < 20:
        msg = (f"Not enough data for a meaningful report. "
               f"Run at least 20 tasks first (have {len(rows)}).")
        if as_json:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return

    rpt = _compute_report(rows)

    if as_json:
        export = {k: v for k, v in rpt.items() if k != "rows"}
        print(json.dumps(export, indent=2, default=str))
        return

    _print_report(rpt)

    _plot_reward_over_time(rows, out, dpi)
    _plot_exploration_rate(rows, out, dpi)
    _plot_bucket_distribution(rpt["bucket_tasks"], out, dpi)
