from __future__ import annotations
import asyncio
import statistics
from dataclasses import dataclass
from pathlib import Path

import httpx

from .task_suite import TaskSuite, load_suite

BASE_URL = "http://localhost:8001"


@dataclass
class ABSummary:
    suite_name: str
    n_tasks: int
    baseline_agent: str
    off_median_latency_ms: float | None
    on_median_latency_ms: float | None
    off_p90_latency_ms: float | None
    on_p90_latency_ms: float | None
    off_success_rate: float
    on_success_rate: float
    off_mean_reward: float | None
    on_mean_reward: float | None
    off_results: list[dict]
    on_results: list[dict]


async def run_suite(
    suite: TaskSuite,
    routing_mode: str,
    baseline_policy: str | None,
    run_type: str,
    client: httpx.AsyncClient,
) -> tuple[int, list[dict]]:
    r = await client.post(f"{BASE_URL}/api/eval/start", json={
        "run_type": run_type,
        "routing_enabled": routing_mode == "adaptive",
        "baseline_policy": baseline_policy,
        "suite_name": suite.name,
    })
    r.raise_for_status()
    run_id = r.json()["run_id"]

    results = []
    for task in suite.tasks:
        try:
            resp = await client.post(f"{BASE_URL}/api/eval/task", json={
                "run_id": run_id,
                "task_id": task.id,
                "text": task.text,
                "bucket": task.bucket,
                "difficulty": task.difficulty,
                "routing_mode": routing_mode,
            }, timeout=task.timeout_s or 120.0)
            resp.raise_for_status()
            results.append({"task_id": task.id, "bucket": task.bucket,
                            "difficulty": task.difficulty, **resp.json()})
        except Exception as e:
            results.append({"task_id": task.id, "bucket": task.bucket,
                            "difficulty": task.difficulty, "success": False,
                            "latency_ms": 0.0, "reward": 0.0, "error": str(e)})

    await client.post(f"{BASE_URL}/api/eval/finish", json={"run_id": run_id})
    return run_id, results


def _summarize_results(results: list[dict]) -> tuple[float | None, float | None, float, float | None]:
    latencies = sorted(r["latency_ms"] for r in results if r.get("latency_ms"))
    successes = [int(r.get("success", False)) for r in results]
    rewards = [r["reward"] for r in results if r.get("reward") is not None]
    median_lat = latencies[len(latencies) // 2] if latencies else None
    p90_lat = latencies[int(len(latencies) * 0.9)] if latencies else None
    success_rate = sum(successes) / len(successes) if successes else 0.0
    mean_reward = statistics.mean(rewards) if rewards else None
    return median_lat, p90_lat, success_rate, mean_reward


async def run_ab_eval(
    suite_path: Path,
    baseline_agent: str = "ollama:general",
    repeat: int = 1,
) -> ABSummary:
    suite = load_suite(suite_path)
    async with httpx.AsyncClient(timeout=300.0) as client:
        _, off_results = await run_suite(
            suite, f"fixed:{baseline_agent}", f"fixed:{baseline_agent}", "ab_off", client
        )
        _, on_results = await run_suite(suite, "adaptive", None, "ab_on", client)

    off_med, off_p90, off_sr, off_rwd = _summarize_results(off_results)
    on_med, on_p90, on_sr, on_rwd = _summarize_results(on_results)

    return ABSummary(
        suite_name=suite.name,
        n_tasks=len(suite.tasks),
        baseline_agent=baseline_agent,
        off_median_latency_ms=off_med,
        on_median_latency_ms=on_med,
        off_p90_latency_ms=off_p90,
        on_p90_latency_ms=on_p90,
        off_success_rate=off_sr,
        on_success_rate=on_sr,
        off_mean_reward=off_rwd,
        on_mean_reward=on_rwd,
        off_results=off_results,
        on_results=on_results,
    )


def print_ab_report(summary: ABSummary, json_output: bool = False) -> None:
    if json_output:
        import json
        print(json.dumps({
            "suite": summary.suite_name,
            "n_tasks": summary.n_tasks,
            "baseline": summary.baseline_agent,
            "off": {
                "median_latency_ms": summary.off_median_latency_ms,
                "p90_latency_ms": summary.off_p90_latency_ms,
                "success_rate": summary.off_success_rate,
                "mean_reward": summary.off_mean_reward,
            },
            "on": {
                "median_latency_ms": summary.on_median_latency_ms,
                "p90_latency_ms": summary.on_p90_latency_ms,
                "success_rate": summary.on_success_rate,
                "mean_reward": summary.on_mean_reward,
            },
        }, indent=2))
        return

    def fmt_ms(v: float | None) -> str:
        return f"{v/1000:.2f}s" if v is not None else "n/a"

    def fmt_rate(v: float) -> str:
        return f"{v:.0%}"

    def delta(off: float | None, on: float | None, lower_is_better: bool = False) -> str:
        if off is None or on is None:
            return "n/a"
        d = on - off
        sign = "+" if d > 0 else ""
        if lower_is_better:
            indicator = " ✓" if d < 0 else (" ✗" if d > 0 else "")
        else:
            indicator = " ✓" if d > 0 else (" ✗" if d < 0 else "")
        if lower_is_better and off != 0:
            pct = f"{sign}{d/off:.0%}"
        elif not lower_is_better and off != 0:
            pct = f"{sign}{d/off:.0%}"
        else:
            pct = f"{sign}{d:.3f}"
        return f"{pct}{indicator}"

    print(f"\nMahoraga A/B Evaluation — {summary.suite_name}")
    print(f"Routing OFF baseline: {summary.baseline_agent}")
    print(f"Routing ON: adaptive (bandit)")
    print(f"Tasks: {summary.n_tasks}\n")
    print(f"{'Metric':<25} {'OFF':>10} {'ON':>10} {'Delta':>12}")
    print("-" * 60)
    print(f"{'Median latency':<25} {fmt_ms(summary.off_median_latency_ms):>10} {fmt_ms(summary.on_median_latency_ms):>10} {delta(summary.off_median_latency_ms, summary.on_median_latency_ms, lower_is_better=True):>12}")
    print(f"{'P90 latency':<25} {fmt_ms(summary.off_p90_latency_ms):>10} {fmt_ms(summary.on_p90_latency_ms):>10} {delta(summary.off_p90_latency_ms, summary.on_p90_latency_ms, lower_is_better=True):>12}")
    print(f"{'Success rate':<25} {fmt_rate(summary.off_success_rate):>10} {fmt_rate(summary.on_success_rate):>10} {delta(summary.off_success_rate, summary.on_success_rate):>12}")
    print(f"{'Mean reward':<25} {summary.off_mean_reward or 0:.3f}     {summary.on_mean_reward or 0:.3f}     {delta(summary.off_mean_reward, summary.on_mean_reward):>12}")
    print()
