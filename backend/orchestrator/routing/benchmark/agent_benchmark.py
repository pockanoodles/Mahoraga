"""Short benchmark sweep for a single agent, used during orch agent add."""
from __future__ import annotations
import asyncio
import statistics
from dataclasses import dataclass

import httpx

BASE_URL = "http://localhost:8001"

_SMOKE_TASKS = [
    {"text": "What is 2 + 2? Reply with just the number.", "bucket": "research", "difficulty": "simple"},
    {"text": "Write a Python function called hello() that returns 'hello world'.", "bucket": "code", "difficulty": "simple"},
    {"text": "What does the ls command do?", "bucket": "research", "difficulty": "simple"},
]

_BENCHMARK_TASKS = [
    {"text": "Write a Python function that returns the sum of a list.", "bucket": "code", "difficulty": "simple"},
    {"text": "Find the bug: def add(a, b): return a - b", "bucket": "debug", "difficulty": "simple"},
    {"text": "What is the difference between a list and a tuple?", "bucket": "research", "difficulty": "simple"},
    {"text": "Implement a Python class for a stack with push, pop, and is_empty.", "bucket": "code", "difficulty": "medium"},
    {"text": "Explain what causes a KeyError in Python and how to handle it.", "bucket": "debug", "difficulty": "medium"},
    {"text": "Compare SQLite and PostgreSQL for a local single-user app.", "bucket": "research", "difficulty": "medium"},
]


@dataclass
class BenchmarkResult:
    agent: str
    smoke_passed: bool
    smoke_details: list[dict]
    benchmark_n: int
    benchmark_success_rate: float
    benchmark_mean_latency_ms: float | None
    benchmark_mean_reward: float | None


async def run_smoke(agent_id: str, client: httpx.AsyncClient) -> tuple[bool, list[dict]]:
    results = []
    for task in _SMOKE_TASKS:
        try:
            r = await client.post(f"{BASE_URL}/api/eval/task", json={
                "text": task["text"],
                "bucket": task["bucket"],
                "difficulty": task["difficulty"],
                "routing_mode": f"fixed:{agent_id}",
            }, timeout=60.0)
            r.raise_for_status()
            results.append({"ok": r.json()["success"], **task})
        except Exception as e:
            results.append({"ok": False, "error": str(e), **task})
    passed = all(r["ok"] for r in results)
    return passed, results


async def run_short_benchmark(agent_id: str, client: httpx.AsyncClient) -> list[dict]:
    results = []
    run_id_resp = await client.post(f"{BASE_URL}/api/eval/start", json={
        "run_type": "benchmark",
        "routing_enabled": False,
        "baseline_policy": f"fixed:{agent_id}",
        "suite_name": "agent_onboarding",
    })
    run_id = run_id_resp.json()["run_id"]

    for task in _BENCHMARK_TASKS:
        try:
            r = await client.post(f"{BASE_URL}/api/eval/task", json={
                "run_id": run_id,
                "text": task["text"],
                "bucket": task["bucket"],
                "difficulty": task["difficulty"],
                "routing_mode": f"fixed:{agent_id}",
            }, timeout=120.0)
            r.raise_for_status()
            results.append(r.json())
        except Exception as e:
            results.append({"success": False, "latency_ms": 0.0, "reward": 0.0})

    await client.post(f"{BASE_URL}/api/eval/finish", json={"run_id": run_id})
    return results


async def run_agent_benchmark(agent_id: str) -> BenchmarkResult:
    async with httpx.AsyncClient(timeout=300.0) as client:
        smoke_ok, smoke_details = await run_smoke(agent_id, client)
        if not smoke_ok:
            return BenchmarkResult(
                agent=agent_id, smoke_passed=False, smoke_details=smoke_details,
                benchmark_n=0, benchmark_success_rate=0.0,
                benchmark_mean_latency_ms=None, benchmark_mean_reward=None,
            )
        bench_results = await run_short_benchmark(agent_id, client)

    latencies = [r["latency_ms"] for r in bench_results if r.get("latency_ms")]
    successes = [r["success"] for r in bench_results]
    rewards = [r["reward"] for r in bench_results if r.get("reward") is not None]

    return BenchmarkResult(
        agent=agent_id,
        smoke_passed=True,
        smoke_details=smoke_details,
        benchmark_n=len(bench_results),
        benchmark_success_rate=sum(successes) / len(successes) if successes else 0.0,
        benchmark_mean_latency_ms=statistics.mean(latencies) if latencies else None,
        benchmark_mean_reward=statistics.mean(rewards) if rewards else None,
    )
