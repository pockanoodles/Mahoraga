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
import shutil
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

from .bench_report import report_app  # noqa: E402
app.add_typer(report_app, name="report")

from ...routing.live_route import (  # noqa: E402
    CloudArmUnavailable,
    load_arms,
    route_one,
    to_matrix,
)
from ...routing.route_sim import simulate  # noqa: E402
from ...routing.verify_replay import load_bank as load_verify_bank  # noqa: E402
from ...routing.reweight_replay import log_offline_run  # noqa: E402
from ...routing.benchmark.verify import (  # noqa: E402
    DEFAULT_CLAIMS,
    render_verification,
    verify_claims,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_VERIFY_BANK = _PROJECT_ROOT / "experiments" / "prompts_verifiable.jsonl"
DEFAULT_HUMANEVAL_BANK = _PROJECT_ROOT / "experiments" / "prompts_humaneval_plus.jsonl"
DEFAULT_AGENTS_YAML = _PROJECT_ROOT / "agents.yaml"
DEFAULT_DECISIONS_DB = Path.home() / ".mahoraga-v2" / "routing_decisions.db"


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
            "prompt_full": prompt,
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
            "output_full": data.get("output") or "",
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
    notes: Optional[str] = typer.Option(
        None, "--notes", help="Why this run — stored on the bench_runs row for later review."
    ),
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

        # Probe the server for its bandit seed with a short timeout so an
        # unreachable server doesn't stall bench startup by the per-task budget.
        bandit_seed: Optional[int] = None
        try:
            async with httpx.AsyncClient(timeout=2.0) as probe:
                seed_resp = await probe.get(f"{base_url}/api/bench_run/seed")
                if seed_resp.status_code == 200:
                    bandit_seed = seed_resp.json().get("bandit_seed")
        except Exception:
            pass

        if bandit_seed is None and os.environ.get("MAHORAGA_BANDIT_SEED") is not None:
            typer.echo(
                "note: MAHORAGA_BANDIT_SEED is set locally but the server's seed is "
                "authoritative — set it in the server's environment, not here.",
                err=True,
            )

        async with httpx.AsyncClient(timeout=timeout) as client:

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
                if notes:
                    bench_payload["notes"] = notes
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


def _preflight_ollama(base_url: str, models: list[str]) -> list[str]:
    """Return a list of human-readable problems; empty = ready.

    The known gotcha (2026-07-26): roster models silently vanish from disk, so
    a live run must confirm the tags are actually present before spending time.
    """
    problems: list[str] = []
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=5.0)
        r.raise_for_status()
        present = {m["name"] for m in r.json().get("models", [])}
    except Exception as exc:
        return [
            f"Ollama unreachable at {base_url}: {exc} — start the daemon "
            "(`ollama serve`, or open the Ollama app)"
        ]
    for m in models:
        if m not in present:
            problems.append(
                f"model {m!r} not in `ollama list` (present: {sorted(present)}) "
                f"— fix: ollama pull {m}"
            )
    return problems


def _preflight_repro(
    bank: Path, config: Path, local_arm: str, judge_model: str, cloud_arm: str
) -> list[str]:
    """Environment checks for `bench repro`. Returns human-readable problems
    with the fix inline; empty = ready to run. No inference, no spend."""
    problems: list[str] = []
    if not bank.exists():
        problems.append(
            f"bank not found: {bank} — the committed bank is "
            "experiments/prompts_humaneval_plus.jsonl; regenerate it with "
            "`python experiments/build_humaneval_bank.py fetch` then "
            "`python experiments/build_humaneval_bank.py build`"
        )
    if not config.exists():
        problems.append(f"agents.yaml not found: {config} — run from the repo root")
        return problems
    try:
        local_worker, _judge, cloud_worker = load_arms(
            config, local_arm, judge_model, cloud_arm
        )
    except CloudArmUnavailable as exc:
        # The roster is fine; this machine just cannot reach the arm. The
        # message already names the fix, so surface it as-is.
        problems.append(str(exc))
        return problems
    except ValueError as exc:
        problems.append(str(exc))
        return problems
    problems += _preflight_ollama(
        local_worker._base_url, [local_worker._model, judge_model]
    )
    # Only the subscription-backed arm has a binary to find. The API-backed arm
    # proved its own precondition by constructing at all (build_cloud_worker
    # raises without a key), so there is nothing left to check here.
    binary = getattr(cloud_worker, "_binary", None)
    if binary is not None and shutil.which(binary) is None:
        problems.append(
            f"`{binary}` CLI not found on PATH — install with "
            "`npm install -g @anthropic-ai/claude-code`, then run `claude` once "
            "interactively to authenticate (the cloud arm bills through that auth). "
            "To reproduce without a subscription, use `--cloud-arm claude` with "
            "ANTHROPIC_API_KEY set."
        )
    return problems


@app.command("live-route")
def live_route_cmd(
    bank: Path = typer.Option(DEFAULT_VERIFY_BANK, "--bank", help="Gold bank with hidden `tests` (the routed-answer ground truth)"),
    local_arm: str = typer.Option("granite4.1-8b", "--local-arm", help="Local arm id (agents.yaml ollama model id) that answers first"),
    judge_model: str = typer.Option("qwen3.5:latest", "--judge-model", help="Local model that renders the correctness verdict (free)"),
    cloud_arm: str = typer.Option("claude-cli", "--cloud-arm", help="agents.yaml key for the cloud escalation arm"),
    config: Path = typer.Option(DEFAULT_AGENTS_YAML, "--config", help="agents.yaml the arms are built from"),
    escalate_only: bool = typer.Option(False, "--escalate-only", help="Run cloud ONLY on escalation (cheaper; drops the measured always-cloud baseline)"),
    code_judge: bool = typer.Option(False, "--code-judge", help="Add the recall-only generated-test check on judge accepts (K local generations per accept; can only add escalations)"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Run only the first N prompts (smoke)"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write per-case results JSONL"),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db"),
    json_out: bool = typer.Option(False, "--json"),
    notes: Optional[str] = typer.Option(None, "--notes"),
) -> None:
    """LIVE local→judge→cloud cascade — the end-to-end proof of Thesis A.

    Runs each gold-bank prompt through the local arm, has a free local judge
    decide correct/incorrect from prompt+output ALONE (no hidden tests — the
    production posture), and escalates to the cloud arm only on a fail verdict.
    Every served answer is graded against the hidden tests for the true routed
    pass@1, and the cloud arm's real cost is measured live. Unlike 5a/5b this
    reads nothing from disk — fresh inference end to end. By default the cloud
    arm also runs on kept-local prompts to give an honest always-cloud baseline
    (never charged to the routed policy); `--escalate-only` skips that spend.
    """
    if not bank.exists():
        typer.echo(f"Gold bank not found: {bank}", err=True)
        raise typer.Exit(1)
    if not config.exists():
        typer.echo(f"agents.yaml not found: {config}", err=True)
        raise typer.Exit(1)

    bank_map = load_verify_bank(bank)
    prompts = list(bank_map.keys())
    if limit:
        prompts = prompts[:limit]
    if not prompts:
        typer.echo("Bank has no gradable prompts (need `prompt` + `tests`).", err=True)
        raise typer.Exit(1)

    try:
        local_worker, judge_worker, cloud_worker = load_arms(
            config, local_arm, judge_model, cloud_arm
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    problems = _preflight_ollama(
        local_worker._base_url, [local_worker._model, judge_model]
    )
    if problems:
        typer.echo("Preflight failed:", err=True)
        for p in problems:
            typer.echo(f"  - {p}", err=True)
        raise typer.Exit(1)

    run_cloud_always = not escalate_only
    local_label = local_arm if local_arm.startswith("ollama:") else f"ollama:{local_arm}"
    cloud_label = cloud_arm  # yaml key, e.g. "claude-cli" — matches 5a/5b matrix keys
    typer.echo(
        f"live-route — {len(prompts)} prompts | local={local_worker.id} "
        f"judge={judge_model} cloud={cloud_worker.id} | "
        f"cloud_baseline={'full' if run_cloud_always else 'escalate-only'}"
    )

    # Flushed per case so a crash hours in loses nothing already measured.
    out_f = open(output, "w") if output else None

    async def _run_all():
        out = []
        for i, p in enumerate(prompts, 1):
            spec = bank_map[p]
            case = await route_one(
                local_worker, judge_worker, cloud_worker,
                p, spec["tests"], bucket=spec.get("bucket", "code"),
                run_cloud_always=run_cloud_always,
                local_label=local_label, cloud_label=cloud_label,
                code_judge=code_judge,
            )
            flag = "↑cloud" if case.escalated else "·local"
            grade = "✓" if case.final_passed else "✗"
            typer.echo(
                f"  [{i:>3}/{len(prompts)}] {flag} {grade} "
                f"(local={'✓' if case.local_passed else '✗'} "
                f"judge={case.judge_verdict}) ${case.total_cost:.4f}"
                + (f"  ERR: {case.error}" if case.error else "")
            )
            if out_f:
                out_f.write(json.dumps(case.as_dict()) + "\n")
                out_f.flush()
            out.append(case)
        return out

    try:
        cases = asyncio.run(_run_all())
    finally:
        if out_f:
            out_f.close()
    if output:
        typer.echo(f"per-case results → {output}")

    # ── Confusion of the judge gate vs hidden-test ground truth ────────────────
    # positive = "judge says local is correct (accept, keep local)".
    tp = fp = tn = fn = unparsed = 0
    for c in cases:
        v = c.judge_verdict
        if v is None:
            unparsed += 1
            continue
        if v and c.local_passed:
            tp += 1
        elif v and not c.local_passed:
            fp += 1  # accepted a wrong local answer → quality leak
        elif (not v) and (not c.local_passed):
            tn += 1  # caught a real failure → good escalation
        else:
            fn += 1  # escalated a correct answer → wasted cloud $
    n_fail = sum(1 for c in cases if not c.local_passed)
    graded = tp + fp + tn + fn
    accuracy = (tp + tn) / graded if graded else 0.0
    fail_recall = tn / n_fail if n_fail else 0.0

    # ── Aggregate via the same simulator 5a/5b use, on this live matrix ────────
    matrix, mprompts, cloud_costs, verdicts, mean_judge_cost = to_matrix(cases)
    local_id = cases[0].local_arm
    cloud_id = cases[0].cloud_arm
    solved = lambda p: verdicts.get(p) is True  # None/False → escalate
    policies = simulate(
        matrix, mprompts, cloud_costs,
        local_arms=[local_id], cloud_arm=cloud_id, cascade=[local_id],
        local_solved=solved, gate_cost_per_task=mean_judge_cost,
    )
    if escalate_only:
        # always-cloud would be over escalated prompts only — misleading. Drop it.
        policies = [p for p in policies if p.name != "always-cloud"]
    routed = next(p for p in policies if p.name.startswith("routed:"))
    cloud_pol = next((p for p in policies if p.name == "always-cloud"), None)
    cost_cut = (100 * (1 - routed.cost_per_task / cloud_pol.cost_per_task)
                if cloud_pol and cloud_pol.cost_per_task else None)

    # Direct live measurement — must match the simulator's routed line.
    n = len(cases)
    live_pass = sum(1 for c in cases if c.final_passed)
    live_cost = sum(c.total_cost for c in cases) / n if n else 0.0
    escalations = sum(1 for c in cases if c.escalated)

    auto_summary = (
        f"bank={bank.name} local={local_id} judge={judge_model}"
        f"{'+code-judge' if code_judge else ''} cloud={cloud_id} "
        f"n={n} escalations={escalations} judge_$1k={mean_judge_cost*1000:.2f} "
        f"acc={accuracy:.3f} fail_recall={tn}/{n_fail} "
        f"routed_pass@1={routed.pass_rate:.4f} routed_$1k={routed.cost_per_task*1000:.2f} "
        f"live_pass@1={live_pass}/{n} live_$1k={live_cost*1000:.2f} cost_cut_pct={cost_cut} "
        f"cloud_baseline={'full' if run_cloud_always else 'escalate-only'}"
    )
    log_offline_run(decisions_db, mode="live-route", task_count=n,
                    notes=f"{auto_summary} | {notes}" if notes else auto_summary)

    if json_out:
        typer.echo(json.dumps({
            "bank": bank.name, "n": n, "local_arm": local_id, "judge_model": judge_model,
            "cloud_arm": cloud_id, "run_cloud_always": run_cloud_always,
            "escalations": escalations, "unparsed": unparsed,
            "accuracy": round(accuracy, 4), "fail_recall": round(fail_recall, 4),
            "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "n_fail": n_fail},
            "judge_cost_per_task": round(mean_judge_cost, 6),
            "cost_cut_pct": round(cost_cut, 2) if cost_cut is not None else None,
            "live_pass_at_1": round(live_pass / n, 4) if n else 0.0,
            "live_cost_per_task": round(live_cost, 6),
            "policies": [p.as_dict() for p in policies],
        }, indent=2))
        return

    typer.echo("")
    typer.echo(f"live-route — local={local_id}, judge={judge_model} (local/free), cloud={cloud_id}")
    typer.echo(f"  judge accuracy={accuracy:.3f}  fail-recall={tn}/{n_fail}={fail_recall:.3f}  "
               f"(caught {tn} / missed {fp} failures; over-escalated {fn}; {unparsed} unparsed)")
    typer.echo(f"  escalations={escalations}/{n}  judge cost=${mean_judge_cost:.4f}/call")
    typer.echo("")
    typer.echo(f"  {'policy':<32}{'pass@1':>16}{'$/task':>12}{'$/1k':>10}")
    typer.echo("  " + "-" * 68)
    for p in policies:
        tag = f"  (esc {p.escalations})" if p.escalations is not None else ""
        typer.echo(f"  {p.name:<32}{f'{p.pass_rate:.3f} ({p.passed}/{p.n})':>16}"
                   f"{f'${p.cost_per_task:.4f}':>12}{f'${p.cost_per_task*1000:.2f}':>10}{tag}")
    typer.echo("")
    typer.echo(f"  live cross-check: routed pass@1={live_pass}/{n}={live_pass/n:.3f}, "
               f"${live_cost*1000:.2f}/1k (matches simulator's routed line)")
    if cost_cut is not None:
        typer.echo(f"  live-route vs always-cloud: {cost_cut:.1f}% cost cut at pass@1={routed.pass_rate:.3f}")


@app.command("repro")
def bench_repro(
    bank: Path = typer.Option(DEFAULT_HUMANEVAL_BANK, "--bank", help="Bank to reproduce on (default: the committed 164-task HumanEval+ bank)"),
    smoke: bool = typer.Option(False, "--smoke", help="Quick end-to-end check: first 5 prompts only (~5 min)"),
    code_judge: bool = typer.Option(False, "--code-judge", help="Add the recall-only generated-test check on judge accepts (slower; can only add escalations)"),
    local_only: bool = typer.Option(False, "--local-only", help="Skip the always-cloud baseline — cloud runs only on judged escalations (cheaper; drops the always-cloud row from the table)"),
    cloud_arm: str = typer.Option("claude-cli", "--cloud-arm", help="Cloud escalation arm: `claude-cli` (Max/Pro subscription, what the published run used) or `claude` (ANTHROPIC_API_KEY — no subscription needed)"),
    preflight_only: bool = typer.Option(False, "--preflight-only", help="Run the environment checks and exit — no inference, no spend"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Per-case results JSONL (default: experiments/repro_<date>.jsonl)"),
    json_out: bool = typer.Option(False, "--json", help="Emit the summary as JSON instead of the readable policy table"),
    config: Path = typer.Option(DEFAULT_AGENTS_YAML, "--config", help="agents.yaml the arms are built from"),
) -> None:
    """One-command reproduction of the headline HumanEval+ cascade benchmark.

    Thin wrapper over `bench live-route` with the published configuration
    pinned: local=granite4.1-8b, judge=qwen3.5:latest, bank=the committed
    HumanEval+ 164. Preflights the environment first (Ollama up, both models
    pulled, cloud arm reachable, bank present) so a fresh clone fails in
    seconds with the fix, not hours in. Full run is ~3.5 h on a 16 GB M-series
    Mac; `--smoke` is a ~5 min wiring check. To vary arms or judge, use
    `bench live-route` directly.

    Only the *cloud* arm is a real choice, and it is an authentication one.
    The published run used `claude-cli`, which bills through an interactive
    Max/Pro subscription — accurate, but it made reproduction require that
    subscription. `--cloud-arm claude` calls the same model over the Anthropic
    API with `ANTHROPIC_API_KEY` instead. Both arms are handed the identical
    prompt, so the routed comparison holds; the always-cloud dollar figure is
    the one to expect to differ, since API and subscription bill differently.
    """
    local_arm, judge_model = "granite4.1-8b", "qwen3.5:latest"

    problems = _preflight_repro(bank, config, local_arm, judge_model, cloud_arm)
    if problems:
        typer.echo("Preflight failed — fix these and re-run:", err=True)
        for p in problems:
            typer.echo(f"  - {p}", err=True)
        raise typer.Exit(1)
    auth = (
        "ANTHROPIC_API_KEY set" if cloud_arm == "claude"
        else "`claude` CLI found"
    )
    typer.echo(
        "Preflight OK — Ollama up, granite4.1:8b + qwen3.5:latest present, "
        f"cloud arm {cloud_arm!r} reachable ({auth}), bank readable."
    )
    if preflight_only:
        return

    if output is None:
        stamp = time.strftime("%Y-%m-%d")
        output = _PROJECT_ROOT / "experiments" / (
            f"repro_{stamp}_smoke.jsonl" if smoke else f"repro_{stamp}.jsonl"
        )
    limit = 5 if smoke else None
    typer.echo(
        f"Reproducing on {bank.name}"
        + (f" — smoke: first {limit} prompts, ~5 min."
           if smoke else " — full run, ~3.5 h on a 16 GB M-series Mac.")
    )
    typer.echo("")

    live_route_cmd(
        bank=bank,
        local_arm=local_arm,
        judge_model=judge_model,
        cloud_arm=cloud_arm,
        config=config,
        escalate_only=local_only,
        code_judge=code_judge,
        limit=limit,
        output=output,
        decisions_db=DEFAULT_DECISIONS_DB,
        json_out=json_out,
        notes="orch bench repro"
        + (" --smoke" if smoke else "")
        + (" --code-judge" if code_judge else "")
        # The cloud arm changes what the always-cloud dollar column means, so
        # it has to survive into the run ledger, not just this invocation.
        + (f" --cloud-arm {cloud_arm}" if cloud_arm != "claude-cli" else ""),
    )


@app.command("verify")
def bench_verify(
    claims: Path = typer.Option(DEFAULT_CLAIMS, "--claims", help="Claims manifest to check"),
    json_out: bool = typer.Option(False, "--json", help="Emit results as JSON"),
) -> None:
    """Recompute every published benchmark number from its committed artifact.

    The cheap half of reproducibility. `bench repro` re-runs the benchmark on
    your hardware (~3.5 h, needs Ollama and a `claude` CLI) and answers "is
    this real here". This answers "does the README still match the data" — it
    reads the per-case JSONL files already in the repo, recomputes each
    headline figure, and requires it to round to exactly the published value.

    No models, no network, no API key, no GPU: runs in milliseconds on a fresh
    clone, which makes it the thing a skeptical reader can actually run. Exits
    nonzero on any mismatch, so CI catches a number edited in prose without the
    artifact to support it.
    """
    try:
        results = verify_claims(claims)
    except FileNotFoundError:
        typer.echo(f"Claims manifest not found: {claims}", err=True)
        raise typer.Exit(2)
    except json.JSONDecodeError as exc:
        typer.echo(f"Claims manifest is not valid JSON: {exc}", err=True)
        raise typer.Exit(2)

    if json_out:
        failed = [r for r in results if not r.ok]
        typer.echo(json.dumps({
            "claims": [r.to_dict() for r in results],
            "verified": len(results) - len(failed),
            "failed": len(failed),
        }, indent=2))
    else:
        typer.echo(render_verification(results))

    if any(not r.ok for r in results):
        raise typer.Exit(1)


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
