"""
`orch agentbench` — benchmark coding agents on a repo's own history.

Subcommands
-----------

  orch agentbench mine [REPO]                  — turn commits into gated tasks
  orch agentbench preflight [REPO] --arm ...   — is this machine fit to run?
  orch agentbench run [REPO] --arm aider:qwen3.5:latest [--cond blind|feedback|both]
                             [--repeats K] [--until HH:MM] [--wait-idle MIN]
  orch agentbench report [REPO] [--json]

REPO defaults to the current directory and must be a git repo with a pytest
suite. State lives under ~/.mahoraga-v2/agentbench/; REPO is only read.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import typer

from backend.orchestrator.agentbench import manifest
from backend.orchestrator.agentbench.agents import kill_live_agents, parse_arm
from backend.orchestrator.agentbench.bank import DEFAULT_ROOT, Bench, SuiteConfig
from backend.orchestrator.agentbench.guard import Guard
from backend.orchestrator.agentbench.mine import find_candidates, mine
from backend.orchestrator.agentbench.report import degraded_note, outcome, render, summarise
from backend.orchestrator.agentbench.runner import (
    CONDITIONS, DEFAULT_TIMEOUT, load_attempts, load_degraded, run_matrix,
)

app = typer.Typer(
    name="agentbench",
    help="Benchmark coding agents + local models on a repo's own commit history.",
    no_args_is_help=True,
)

_REPO = typer.Argument(Path("."), help="Git repo to benchmark (default: cwd).")
_ARMS = typer.Option(..., "--arm", help="<agent>:<model>, repeatable.")
_RATIO = typer.Option(0.5, "--min-speed", help="Degrade attempts under this share of the model's best tok/s.")


def _agents(arms: list[str]):
    try:
        return [parse_arm(a) for a in arms]
    except ValueError as e:
        raise typer.BadParameter(str(e))


def _root() -> Path:
    return Path(os.environ.get("MAHORAGA_AGENTBENCH_ROOT", DEFAULT_ROOT))


def parse_until(hhmm: str, now: datetime | None = None) -> float:
    """The next occurrence of HH:MM, as epoch seconds."""
    now = now or datetime.now()
    try:
        h, m = (int(x) for x in hhmm.split(":"))
        at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    except ValueError:
        raise typer.BadParameter(f"--until takes HH:MM, not {hhmm!r}")
    return (at if at > now else at + timedelta(days=1)).timestamp()


def _print_checks(checks) -> bool:
    for c in checks:
        typer.echo(f"  {'ok  ' if c.ok else 'FAIL'}  {c.name:24} {c.detail}")
    return all(c.ok for c in checks)


def _stay_awake() -> None:
    """Hold off idle and system sleep (on AC) for this process's lifetime.
    Closing the lid still sleeps the machine; nothing here can prevent that."""
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        subprocess.Popen(["caffeinate", "-s", "-i", "-w", str(os.getpid())])


def _default_python(repo: Path) -> str:
    venv = repo / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


@app.command("mine")
def mine_cmd(
    repo: Path = _REPO,
    limit: int = typer.Option(25, "--limit", help="Newest candidate commits to try."),
    max_lines: int = typer.Option(250, "--max-lines", help="Skip commits with more changed source lines."),
    rev: str = typer.Option("HEAD", "--rev", help="History to mine from."),
    commits: Optional[List[str]] = typer.Option(None, "--commit", help="Build exactly these commits."),
    python: Optional[str] = typer.Option(None, "--python", help="Interpreter for the repo's tests."),
    pytest_args: Optional[List[str]] = typer.Option(None, "--pytest-arg", help="Extra pytest arg, e.g. '-m not slow'."),
) -> None:
    """Build gated tasks from commits that changed both source and tests."""
    bench = Bench(repo)
    try:
        prior = bench.load_config()
    except FileNotFoundError:
        prior = None
    config = SuiteConfig(
        python=python or (prior.python if prior else _default_python(bench.repo)),
        pytest_args=list(pytest_args) if pytest_args else (prior.pytest_args if prior else []),
    )
    shas = list(commits) if commits else [
        c.sha for c in find_candidates(bench.repo, rev, limit, max_lines)]
    if not shas:
        typer.echo("no candidate commits (need commits changing both source and a test module)")
        raise typer.Exit(1)
    typer.echo(f"building {len(shas)} candidates into {bench.dir}")

    def progress(sha, task, reason):
        typer.echo(f"  {sha[:10]}  " + (
            f"ok  f2p={len(task.f2p)} p2p={len(task.p2p)} files={len(task.gold_src)} "
            f"lines={task.gold_lines}" if task else f"drop  {reason}"))

    tasks, dropped = mine(bench, config, shas, progress)
    typer.echo(f"{len(tasks)} tasks in the bench, {len(dropped)} dropped")


@app.command("preflight")
def preflight_cmd(
    repo: Path = _REPO,
    arms: List[str] = _ARMS,
    ratio: float = _RATIO,
) -> None:
    """Check this machine can produce valid results: charger, agent, models, speed."""
    agents = _agents(arms)
    ok = _print_checks(Guard(_root(), ratio=ratio).preflight(agents))
    n = len(Bench(repo).load_tasks())
    typer.echo(f"  {'ok  ' if n else 'FAIL'}  {'tasks':24} {n or 'none — run `orch agentbench mine`'}")
    raise typer.Exit(0 if ok and n else 1)


@app.command("run")
def run_cmd(
    repo: Path = _REPO,
    arms: List[str] = _ARMS,
    cond: str = typer.Option("blind", "--cond", help="blind, feedback, or both."),
    tasks: Optional[List[str]] = typer.Option(None, "--task", help="Only these task shas."),
    timeout: float = typer.Option(DEFAULT_TIMEOUT, "--timeout", help="Seconds per attempt."),
    repeats: int = typer.Option(1, "--repeats", min=1, help="Attempts per cell; >1 measures stability."),
    until: Optional[str] = typer.Option(None, "--until", help="Start no attempt after HH:MM (next occurrence)."),
    wait_idle: float = typer.Option(0, "--wait-idle", help="Only start attempts after this many idle minutes."),
    ratio: float = _RATIO,
    guarded: bool = typer.Option(True, "--guard/--no-guard", help="Preflight, pause on bad conditions, drop degraded attempts."),
    force: bool = typer.Option(False, "--force", help="Run even if preflight fails."),
) -> None:
    """Attempt every task with every arm. Resumable: finished attempts are skipped."""
    conds = CONDITIONS if cond == "both" else (cond,)
    if not set(conds) <= set(CONDITIONS):
        raise typer.BadParameter(f"--cond must be blind, feedback or both, not {cond!r}")
    agents = _agents(arms)
    deadline = parse_until(until) if until else None
    bench = Bench(repo)
    if not bench.load_tasks():
        typer.echo("no tasks — run `orch agentbench mine` first")
        raise typer.Exit(1)

    guard = None
    if guarded:
        guard = Guard(_root(), ratio=ratio, wait_idle=wait_idle * 60, log=typer.echo)
        typer.echo("preflight")
        if not _print_checks(guard.preflight(agents)) and not force:
            typer.echo("preflight failed; fix the above or pass --force")
            raise typer.Exit(1)
    _stay_awake()

    def stop(signum, frame):  # take the running agent down with the run
        kill_live_agents()
        raise typer.Exit(130)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if deadline:
        typer.echo(f"no attempt starts after {datetime.fromtimestamp(deadline):%a %H:%M}")

    def progress(a):
        tag = f"DEGRADED ({'; '.join(a.degraded)})" if a.degraded else outcome(a)
        typer.echo(f"  {a.sha[:10]} {a.arm} {a.cond} r{a.rep}: {tag}  "
                   f"f2p={a.f2p_pass}/{a.f2p_total} regressions={a.regressions} "
                   f"{a.secs / 60:.1f}m")

    only = set(tasks) if tasks else None
    run_id = manifest.new_run_id()
    manifest.write(bench, manifest.build(
        bench, agents, [t for t in bench.load_tasks() if not only or t.sha in only],
        run_id=run_id,
        settings={"conds": list(conds), "repeats": repeats, "timeout": timeout,
                  "until": until, "wait_idle_min": wait_idle, "guarded": guarded,
                  "min_speed_ratio": ratio, "forced": force},
        speed_refs=guard.references() if guard else None,
    ))
    typer.echo(f"run {run_id} (manifest: {manifest.path(bench, run_id)})")
    done: list = []
    stopped = "interrupted"
    try:
        done = run_matrix(bench, agents, conds, only, timeout, repeats, guard=guard,
                          deadline=deadline, run_id=run_id, progress=progress)
        stopped = ("deadline" if deadline and datetime.now().timestamp() >= deadline
                   else "completed")
    finally:
        manifest.finish(bench, run_id, len(done), stopped)
    typer.echo(f"{len(done)} attempts run ({stopped})")


@app.command("report")
def report_cmd(
    repo: Path = _REPO,
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Per-arm resolve rates, failure modes, and a per-task matrix."""
    bench = Bench(repo)
    tasks, attempts, degraded = bench.load_tasks(), load_attempts(bench), load_degraded(bench)
    if json_out:
        typer.echo(json.dumps({
            "repo": str(bench.repo), "tasks": len(tasks),
            "arms": [asdict(s) for s in summarise(tasks, attempts)],
            "degraded": len(degraded), "degraded_note": degraded_note(degraded),
        }, indent=2))
        return
    typer.echo(render(tasks, attempts, degraded))


@app.command("verdict")
def verdict_cmd(
    repos: List[Path] = typer.Argument(..., help="Benched repos to pool evidence from."),
    arm: str = typer.Option(..., "--arm", help="The one arm to judge, <agent>:<model>."),
    cond: str = typer.Option("feedback", "--cond", help="blind or feedback (a real queue can run tests)."),
    tasks_per_month: int = typer.Option(..., "--tasks-per-month", help="Coding tasks you give cloud agents a month."),
    api_spend: Optional[float] = typer.Option(None, "--api-spend", help="Monthly API spend, USD (API billing)."),
    plan_id: Optional[str] = typer.Option(None, "--plan", help="Subscription plan id from audit/pricing.json, e.g. claude-max-5x."),
    plan_price: Optional[float] = typer.Option(None, "--plan-price", help="Your plan's monthly price, if it isn't verified in pricing.json."),
    usage_of_cap: float = typer.Option(1.0, "--usage-of-cap", help="Share of your plan's cap you use (1.0 = you hit it)."),
    deferrable: float = typer.Option(0.25, "--deferrable", help="Share of tasks that could wait for an overnight queue."),
    hourly: float = typer.Option(50.0, "--hourly", help="Value of your time, USD/h."),
    triage: float = typer.Option(3.0, "--triage-min", help="Minutes to triage a failed local attempt."),
    watts: float = typer.Option(40.0, "--watts", help="Machine draw while an attempt runs."),
    kwh: float = typer.Option(0.30, "--kwh", help="Electricity price, USD/kWh."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Stay, split, or switch: is local worth it for your work, on this machine?"""
    from backend.orchestrator.audit import pricing
    from backend.orchestrator.audit.evidence import from_benches
    from backend.orchestrator.audit.verdict import Assumptions, Billing, decide, render as render_verdict

    if (api_spend is None) == (plan_id is None):
        raise typer.BadParameter("give exactly one of --api-spend or --plan")
    try:
        ev = from_benches([Bench(r) for r in repos], arm, cond)
        if plan_id:
            p = pricing.plan(plan_id, plan_price)
            billing = Billing.subscription(p, pricing.lower_tier(p))
        else:
            billing = Billing.api(api_spend)
    except (ValueError, KeyError) as e:
        raise typer.BadParameter(str(e))
    a = Assumptions(tasks_per_month=tasks_per_month, deferrable_share=deferrable,
                    triage_minutes=triage, hourly_usd=hourly, watts=watts,
                    usd_per_kwh=kwh, usage_of_cap=usage_of_cap)
    v = decide(ev, a, billing)
    if json_out:
        typer.echo(json.dumps({
            "call": v.call, "confident": v.confident, "reason": v.reason,
            "monthly_usd": v.monthly_usd, "sensitivity": v.sensitivity,
            "evidence": {"label": ev.label, "rate": ev.rate.point, "lo": ev.rate.lo,
                         "hi": ev.rate.hi, "tasks": ev.rate.tasks, "tier": ev.tier,
                         "hardware": ev.hardware},
            "assumptions": asdict(a),
        }, indent=2))
        return
    typer.echo(render_verdict(v))
