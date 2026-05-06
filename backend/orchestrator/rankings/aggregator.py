from __future__ import annotations
import logging
import sqlite3
from math import sqrt
from pathlib import Path

log = logging.getLogger(__name__)

LIVE_WEIGHT = 0.7
HARNESS_WEIGHT = 0.3

_BUCKETS = ["code", "test", "refactor", "debug", "research", "plan", "review", "security"]
_DIFFICULTIES = ["simple", "medium", "complex"]
_DECISION_LOG_PATH = Path.home() / ".mahoraga-v2" / "routing_decisions.db"


def _load_decision_log(limit: int = 5000) -> list[dict]:
    """Read routing decisions directly from the bandit decision log."""
    if not _DECISION_LOG_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(_DECISION_LOG_PATH), check_same_thread=False)
        cur = conn.execute(
            "SELECT selected_agent, success, reward, latency_s "
            "FROM decisions WHERE reward IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = [
            {"agent_name": r[0], "success": r[1], "reward_score": r[2], "wall_time_ms": (r[3] or 0) * 1000}
            for r in cur.fetchall()
        ]
        conn.close()
        return rows
    except Exception as e:
        log.warning("could not read decision log: %s", e)
        return []


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binary success rate."""
    if total == 0:
        return (0.0, 0.0)
    phat = successes / total
    denom = 1 + z * z / total
    center = (phat + z * z / (2 * total)) / denom
    margin = z * sqrt((phat * (1 - phat) + z * z / (4 * total)) / total) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def build_rankings_rows(metrics: list[dict]) -> list[dict]:
    """Sort agent metrics into a ranked list with Wilson CI.

    Input dicts must have: agent, sample_count, success_count, mean_reward, median_latency_ms
    """
    rows = []
    for m in metrics:
        n = m["sample_count"]
        s = m.get("success_count", 0)
        ci_low, ci_high = wilson_interval(s, n)
        win_rate = s / n if n > 0 else 0.0
        rows.append({
            "agent": m["agent"],
            "win_rate": win_rate,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "avg_reward": m.get("mean_reward"),
            "avg_latency_ms": m.get("median_latency_ms"),
            "sample_count": n,
        })
    rows.sort(key=lambda r: (
        -(r["avg_reward"] or 0.0),
        -(r["win_rate"] or 0.0),
        (r["avg_latency_ms"] or float("inf")),
        -(r["sample_count"] or 0),
    ))
    for i, row in enumerate(rows):
        row["rank"] = i + 1
    return rows


async def rebuild_rankings(metrics_store, rankings_store) -> None:
    """Recompute and persist all ranking scopes from live history + harness data."""

    # Combine task_metrics (orchestrator runs) + decision log (bandit routing history)
    task_history = await metrics_store.get_history(limit=5000)
    decision_log_rows = _load_decision_log(limit=5000)
    live_history = task_history + decision_log_rows

    def _agg_live(rows: list[dict]) -> dict[str, dict]:
        agents: dict[str, dict] = {}
        for row in rows:
            a = row.get("agent_name", "")
            if not a:
                continue
            if a not in agents:
                agents[a] = {
                    "sample_count": 0, "success_count": 0,
                    "rewards": [], "latencies": [],
                    "buckets": {},
                }
            d = agents[a]
            d["sample_count"] += 1
            if row.get("success"):
                d["success_count"] += 1
            if row.get("reward_score") is not None:
                d["rewards"].append(row["reward_score"])
            if row.get("wall_time_ms") is not None:
                d["latencies"].append(row["wall_time_ms"])
            bucket = row.get("capability_bucket", "general")
            bd = d["buckets"].setdefault(bucket, {
                "sample_count": 0, "success_count": 0, "rewards": [], "latencies": []
            })
            bd["sample_count"] += 1
            if row.get("success"):
                bd["success_count"] += 1
            if row.get("reward_score") is not None:
                bd["rewards"].append(row["reward_score"])
            if row.get("wall_time_ms") is not None:
                bd["latencies"].append(row["wall_time_ms"])
        return agents

    live_agents = _agg_live(live_history)

    harness_rows = await rankings_store.get_benchmark_runs()

    def _agg_harness(rows: list[dict]) -> dict[str, dict]:
        agents: dict[str, dict] = {}
        for row in rows:
            a = row["agent"]
            if a not in agents:
                agents[a] = {"sample_count": 0, "success_count": 0, "rewards": [], "latencies": []}
            n = row.get("sample_count", 0)
            wr = row.get("win_rate") or 0.0
            agents[a]["sample_count"] += n
            agents[a]["success_count"] += int(wr * n)
            if row.get("reward_mean") is not None:
                agents[a]["rewards"].extend([row["reward_mean"]] * max(n, 1))
            if row.get("median_latency_ms") is not None:
                agents[a]["latencies"].extend([row["median_latency_ms"]] * max(n, 1))
        return agents

    harness_agents = _agg_harness(harness_rows)
    all_agents = set(live_agents) | set(harness_agents)

    def _merge(agent: str) -> dict | None:
        live = live_agents.get(agent, {})
        harness = harness_agents.get(agent, {})
        total = live.get("sample_count", 0) + harness.get("sample_count", 0)
        if total == 0:
            return None

        def _wmean(lv: list, hv: list) -> float | None:
            if not lv and not hv:
                return None
            lm = sum(lv) / len(lv) if lv else None
            hm = sum(hv) / len(hv) if hv else None
            if lm is None:
                return hm
            if hm is None:
                return lm
            return LIVE_WEIGHT * lm + HARNESS_WEIGHT * hm

        lats = live.get("latencies", [])
        hlats = harness.get("latencies", [])
        med_lat = _wmean(lats, hlats)
        return {
            "agent": agent,
            "sample_count": total,
            "success_count": live.get("success_count", 0) + harness.get("success_count", 0),
            "mean_reward": _wmean(live.get("rewards", []), harness.get("rewards", [])),
            "median_latency_ms": med_lat,
        }

    overall_metrics = [m for a in all_agents if (m := _merge(a)) is not None]
    if overall_metrics:
        ranked = build_rankings_rows(overall_metrics)
        await rankings_store.replace_scope_rankings("overall", "all", ranked)

    for bucket in _BUCKETS:
        bucket_metrics = []
        for agent in all_agents:
            bd = live_agents.get(agent, {}).get("buckets", {}).get(bucket, {})
            n = bd.get("sample_count", 0)
            if n == 0:
                continue
            lats = bd.get("latencies", [])
            bucket_metrics.append({
                "agent": agent,
                "sample_count": n,
                "success_count": bd.get("success_count", 0),
                "mean_reward": sum(bd.get("rewards", [])) / len(bd["rewards"]) if bd.get("rewards") else None,
                "median_latency_ms": sorted(lats)[len(lats) // 2] if lats else None,
            })
        if bucket_metrics:
            ranked = build_rankings_rows(bucket_metrics)
            await rankings_store.replace_scope_rankings("bucket", bucket, ranked)

    log.info("rankings rebuilt: %d agents across %d bucket scopes", len(all_agents), len(_BUCKETS))
