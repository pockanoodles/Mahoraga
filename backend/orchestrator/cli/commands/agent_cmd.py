"""orch agent — agent onboarding commands."""
from __future__ import annotations
import asyncio

import httpx
import typer

BASE_URL = "http://localhost:8001"

app = typer.Typer(name="agent", help="Agent management", no_args_is_help=True)


@app.command("add")
def add_agent(
    model: str = typer.Argument(..., help="Agent ID to add, e.g. ollama:qwen3 or gemini:flash"),
    skip_benchmark: bool = typer.Option(False, "--skip-benchmark", help="Skip benchmark, just smoke test"),
) -> None:
    """Register a new agent, run smoke test, benchmark it, and update rankings."""

    async def _run():
        from ..routing.benchmark.agent_benchmark import run_agent_benchmark

        # 1. Check the agent is registered and healthy
        typer.echo(f"Checking agent: {model}")
        try:
            r = httpx.get(f"{BASE_URL}/workers/health", timeout=10.0)
            r.raise_for_status()
            workers = r.json()
            registered = any(
                w.get("worker_id") == model or w.get("id") == model
                for w in (workers if isinstance(workers, list) else workers.get("workers", []))
            )
        except httpx.ConnectError:
            typer.echo("Cannot connect to Mahoraga server. Start it first: python -m backend.main", err=True)
            raise typer.Exit(1)

        if not registered:
            typer.echo(f"Agent '{model}' is not registered in the server.", err=True)
            typer.echo("To add a new agent type, register it in backend/orchestrator/service/app.py lifespan()", err=True)
            typer.echo("Then restart the server and run this command again.", err=True)
            raise typer.Exit(1)

        # 2. Run smoke + benchmark
        typer.echo(f"Running smoke test...")
        result = await run_agent_benchmark(model)

        if not result.smoke_passed:
            typer.echo(f"\nSmoke test FAILED for {model}")
            for s in result.smoke_details:
                status = "✓" if s.get("ok") else "✗"
                typer.echo(f"  {status} {s['text'][:60]}")
            raise typer.Exit(1)

        typer.echo(f"Smoke test: PASSED")

        # Record benchmark results in benchmark_runs table and rebuild rankings.
        if not skip_benchmark and result.benchmark_n > 0:
            typer.echo("Recording benchmark results and rebuilding rankings...")
            successes = int(result.benchmark_success_rate * result.benchmark_n)
            win_rate = successes / result.benchmark_n if result.benchmark_n > 0 else 0.0
            httpx.post(f"{BASE_URL}/api/rankings/benchmark", json={
                "agent": model,
                "avg_latency_ms": result.benchmark_mean_latency_ms,
                "median_latency_ms": result.benchmark_mean_latency_ms,
                "win_rate": win_rate,
                "reward_mean": result.benchmark_mean_reward,
                "sample_count": result.benchmark_n,
                "source": "harness",
            }, timeout=60.0)
        elif not skip_benchmark:
            typer.echo("Rebuilding rankings...")
            httpx.get(f"{BASE_URL}/api/rankings", params={"refresh": "true"}, timeout=60.0)

        # 3. Show summary
        typer.echo(f"\nAgent: {model}")
        typer.echo(f"Smoke test: PASSED")
        if not skip_benchmark:
            typer.echo(f"Benchmark tasks: {result.benchmark_n}")
            typer.echo(f"Success rate: {result.benchmark_success_rate:.0%}")
            if result.benchmark_mean_latency_ms:
                typer.echo(f"Mean latency: {result.benchmark_mean_latency_ms/1000:.1f}s")
            if result.benchmark_mean_reward:
                typer.echo(f"Mean reward: {result.benchmark_mean_reward:.2f}")
        typer.echo(f"\nRankings updated. Run 'orch rankings' to see current standings.")

    asyncio.run(_run())
