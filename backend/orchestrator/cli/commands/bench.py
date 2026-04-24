"""
bench.py — batch routing experiments against a running Mahoraga server.

Reads a JSONL file of `{"prompt": "...", "bucket": "..."}` records and runs
each prompt through `POST /api/task`. Two modes:

  force-explore  For each prompt, pin every agent in `--agents` via
                 `agent_override`. Bypasses bandit selection so new arms
                 get the 10-20 samples per bucket they need to generate
                 signal. Bandit still observes the outcome and updates.

  bandit         Let the bandit route normally. Used to measure convergence
                 after force-explore has seeded per-bucket priors.

Requires `uvicorn backend.orchestrator.service.app:app` to be running.
"""
from __future__ import annotations
import asyncio
import json
import os
import random
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import typer

try:
    import psutil as _psutil
except ImportError:
    _psutil = None

app = typer.Typer(
    name="bench",
    help="Batch routing experiments — force-explore or bandit mode",
    no_args_is_help=True,
)


DEFAULT_AGENTS = [
    "ollama:qwen3-4b",
    "ollama:gemma4-e4b",
    "ollama:deepseek-r1",
    "ollama:lfm2",
    "codex-cli",
    "aider",
    "gemini-cli",
    "goose",
    "opencode",
]


async def _capture_run_context() -> dict[str, Any]:
    """Capture git SHA, Ollama version, hostname, and battery state.

    Ollama is probed at its default local URL (localhost:11434); override via
    `OLLAMA_BASE_URL` env var if the server runs elsewhere.
    """
    ctx: dict[str, Any] = {}

    ctx["hostname"] = socket.gethostname()

    # git SHA + dirty flag
    try:
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if sha_result.returncode == 0:
            ctx["git_sha"] = sha_result.stdout.strip()
            dirty_result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, timeout=5,
            )
            ctx["git_dirty"] = 1 if dirty_result.stdout.strip() else 0
        else:
            ctx["git_sha"] = None
            ctx["git_dirty"] = None
    except Exception:
        ctx["git_sha"] = None
        ctx["git_dirty"] = None

    # Ollama version
    try:
        ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{ollama_base}/api/version")
            if resp.status_code == 200:
                ctx["ollama_version"] = resp.json().get("version")
            else:
                ctx["ollama_version"] = None
    except Exception:
        ctx["ollama_version"] = None

    # Battery / charger state
    if _psutil is not None:
        try:
            battery = _psutil.sensors_battery()
            ctx["on_charger"] = 1 if (battery is not None and battery.power_plugged) else 0
        except Exception:
            ctx["on_charger"] = None
    else:
        ctx["on_charger"] = None

    return ctx


def _load_prompts(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                typer.echo(f"skip malformed line: {line[:60]} ({exc})", err=True)
    return items


async def _run_one(
    client: httpx.AsyncClient,
    base_url: str,
    prompt: str,
    bucket: Optional[str],
    agent: Optional[str],
    bench_run_id: Optional[int] = None,
) -> dict[str, Any]:
    """POST a single task. Returns a flat record for summary aggregation."""
    body: dict[str, Any] = {"prompt": prompt}
    if bucket:
        body["capability_hint"] = bucket
    if agent:
        body["agent_override"] = agent
    if bench_run_id is not None:
        body["bench_run_id"] = bench_run_id
    t0 = time.time()
    try:
        resp = await client.post(f"{base_url}/api/task", json=body)
        elapsed = time.time() - t0
        if resp.status_code != 200:
            return {
                "prompt": prompt[:60],
                "bucket": bucket,
                "requested_agent": agent,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "elapsed_s": round(elapsed, 2),
            }
        data = resp.json()
        # Response shape from /api/task:
        #   status: "success" | "failed"
        #   agent, elapsed_s
        #   metrics: { tokens, tps, model_was_warm, ... }
        #   routing: { exploration, ucb_score, ... }
        metrics = data.get("metrics", {}) or {}
        routing = data.get("routing", {}) or {}
        return {
            "prompt": prompt[:60],
            "bucket": bucket,
            "requested_agent": agent,
            "actual_agent": data.get("agent") or agent,
            "success": data.get("status") == "success",
            "elapsed_s": data.get("elapsed_s") or round(elapsed, 2),
            "tokens": metrics.get("tokens", 0),
            "tps": metrics.get("tps", 0.0),
            "exploration": routing.get("exploration"),
            "ucb_score": routing.get("ucb_score"),
            "output_preview": (data.get("output") or "")[:120],
        }
    except Exception as exc:
        return {
            "prompt": prompt[:60],
            "bucket": bucket,
            "requested_agent": agent,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.time() - t0, 2),
        }


def _build_schedule(
    prompts: list[dict[str, Any]],
    agents: list[str],
    mode: str,
    repeats: int,
    prompt_seed: Optional[int] = None,
) -> list[tuple[str, Optional[str], Optional[str]]]:
    """Return (prompt, bucket, pinned_agent) triples.

    Agent-major order: run all prompts through each agent before switching.
    Keeps each Ollama model loaded for its block of tasks instead of paying
    a 30-60s cold-start every swap. Cheaper for local models; neutral for
    CLI agents.

    If prompt_seed is given:
      - force-explore: agent-major order is preserved; prompts within each
        agent block are shuffled deterministically.
      - bandit: the entire schedule is shuffled deterministically.
    """
    out: list[tuple[str, Optional[str], Optional[str]]] = []
    valid_prompts = [
        (p["prompt"], p.get("bucket"))
        for p in prompts
        if p.get("prompt")
    ]
    if mode == "force-explore":
        for a in agents:
            block: list[tuple[str, Optional[str], Optional[str]]] = []
            for prompt_text, bucket in valid_prompts:
                for _ in range(repeats):
                    block.append((prompt_text, bucket, a))
            if prompt_seed is not None:
                random.Random(prompt_seed).shuffle(block)
            out.extend(block)
    else:  # bandit
        for prompt_text, bucket in valid_prompts:
            for _ in range(repeats):
                out.append((prompt_text, bucket, None))
        if prompt_seed is not None:
            random.Random(prompt_seed).shuffle(out)
    return out


def _print_summary(results: list[dict[str, Any]]) -> None:
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        a = r.get("actual_agent") or r.get("requested_agent") or "unknown"
        by_agent.setdefault(a, []).append(r)

    typer.echo("")
    header = f"{'Agent':<24} {'N':>4} {'Pass%':>6} {'Err':>5} {'Lat(s)':>8} {'t/s':>7} {'Tokens':>7}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for agent, rows in sorted(by_agent.items(), key=lambda kv: -len(kv[1])):
        n = len(rows)
        errors = sum(1 for r in rows if "error" in r)
        passed = sum(1 for r in rows if r.get("success"))
        latencies = [r["elapsed_s"] for r in rows if r.get("elapsed_s") is not None]
        tps_values = [r.get("tps", 0) for r in rows if r.get("tps")]
        tokens = [r.get("tokens", 0) for r in rows if r.get("tokens")]
        pass_pct = (passed / max(1, n - errors)) * 100 if n - errors > 0 else 0.0
        avg_l = sum(latencies) / len(latencies) if latencies else 0.0
        avg_tps = sum(tps_values) / len(tps_values) if tps_values else 0.0
        avg_tok = sum(tokens) / len(tokens) if tokens else 0.0
        typer.echo(
            f"{agent:<24} {n:>4} {pass_pct:>5.0f}% {errors:>5} {avg_l:>8.1f} {avg_tps:>7.1f} {avg_tok:>7.0f}"
        )
    typer.echo("")
    typer.echo("Reward + quality aren't in the /api/task response; pull from the")
    typer.echo("decisions DB or the Performance page for those aggregates.")


@app.command("run")
def bench_run(
    prompts_path: Path = typer.Option(..., "--prompts", "-p", help="JSONL file of prompts"),
    mode: str = typer.Option("force-explore", "--mode", "-m", help="force-explore | bandit"),
    agents: str = typer.Option(
        "", "--agents", "-a", help="Comma-separated agent names (default: all 9)"
    ),
    repeats: int = typer.Option(1, "--repeats", "-r", help="Runs per (prompt, agent) pair"),
    base_url: str = typer.Option("http://localhost:8000", "--base-url"),
    timeout: int = typer.Option(180, "--timeout", help="Per-task timeout in seconds"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write raw results JSONL"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Cap total tasks (smoke testing)"),
) -> None:
    """Run a batch of prompts through Mahoraga for data collection."""
    if mode not in ("force-explore", "bandit"):
        typer.echo(f"Invalid mode: {mode!r}. Use force-explore or bandit.", err=True)
        raise typer.Exit(1)

    prompt_items = _load_prompts(prompts_path)
    if not prompt_items:
        typer.echo("No prompts loaded.", err=True)
        raise typer.Exit(1)

    _prompt_seed_env = os.getenv("MAHORAGA_PROMPT_SEED")
    prompt_seed: Optional[int] = None
    if _prompt_seed_env is not None:
        try:
            prompt_seed = int(_prompt_seed_env)
        except ValueError:
            typer.echo(f"warn: MAHORAGA_PROMPT_SEED={_prompt_seed_env!r} is not an integer; ignoring", err=True)

    agent_list = [a.strip() for a in agents.split(",") if a.strip()] or DEFAULT_AGENTS
    schedule = _build_schedule(prompt_items, agent_list, mode, repeats, prompt_seed=prompt_seed)
    if limit is not None:
        schedule = schedule[:limit]

    results: list[dict[str, Any]] = []
    start = time.time()

    async def go() -> None:
        run_ctx = await _capture_run_context()
        bench_run_id: Optional[int] = None

        bandit_seed: Optional[int] = None
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                seed_resp = await client.get(f"{base_url}/api/bench_run/seed")
                if seed_resp.status_code == 200:
                    bandit_seed = seed_resp.json().get("bandit_seed")
            except Exception:
                pass

            typer.echo(
                f"mode={mode}  prompts={len(prompt_items)}  agents={len(agent_list)}"
                f"  bandit_seed={bandit_seed}  prompt_seed={prompt_seed}"
            )
            typer.echo(f"tasks_to_run={len(schedule)}  est_wall_min={len(schedule)*8/60:.1f} (at 8s/task)")
            typer.echo("")

            try:
                bench_payload: dict[str, Any] = {
                    "mode": mode,
                    "prompts_file": str(prompts_path),
                    "agents": json.dumps(agent_list),
                    "repeats": repeats,
                    "task_count_planned": len(schedule),
                    **{k: v for k, v in run_ctx.items() if v is not None},
                }
                if bandit_seed is not None:
                    bench_payload["bandit_seed"] = bandit_seed
                if prompt_seed is not None:
                    bench_payload["prompt_seed"] = prompt_seed
                resp = await client.post(f"{base_url}/api/bench_run", json=bench_payload)
                if resp.status_code == 200:
                    bench_run_id = resp.json().get("bench_run_id")
            except Exception as exc:
                typer.echo(f"warn: could not create bench_run row: {exc}", err=True)

            for i, (p, b, a) in enumerate(schedule, start=1):
                r = await _run_one(client, base_url, p, b, a, bench_run_id=bench_run_id)
                results.append(r)
                elapsed = time.time() - start
                remaining = (elapsed / i) * (len(schedule) - i) if i > 0 else 0
                tag = a or "bandit"
                status = "✓" if r.get("success") else ("x" if "error" in r else "–")
                sys.stdout.write(
                    f"\r  [{i:>3}/{len(schedule)}] {status} {tag:<20} "
                    f"elapsed={elapsed:>5.0f}s eta={remaining:>5.0f}s           "
                )
                sys.stdout.flush()
            sys.stdout.write("\n")

            if bench_run_id is not None:
                completed = sum(1 for r in results if r.get("success"))
                try:
                    await client.post(
                        f"{base_url}/api/bench_run/{bench_run_id}/finalize",
                        json={"task_count_completed": completed},
                    )
                except Exception as exc:
                    typer.echo(f"warn: could not finalize bench_run: {exc}", err=True)

    asyncio.run(go())

    if output:
        with open(output, "w") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
        typer.echo(f"raw results → {output}")

    _print_summary(results)
    errors = sum(1 for r in results if "error" in r)
    if errors:
        typer.echo(f"\n{errors} error(s) out of {len(results)} — see --output for details.", err=True)


@app.command("validate")
def bench_validate(
    prompts_path: Path = typer.Argument(..., help="JSONL file to validate"),
) -> None:
    """Sanity-check a prompts JSONL file without running anything."""
    items = _load_prompts(prompts_path)
    typer.echo(f"Loaded {len(items)} prompts")
    by_bucket: dict[str, int] = {}
    for p in items:
        b = p.get("bucket") or "(none)"
        by_bucket[b] = by_bucket.get(b, 0) + 1
    for b, n in sorted(by_bucket.items(), key=lambda kv: -kv[1]):
        typer.echo(f"  {b:<12} {n}")
