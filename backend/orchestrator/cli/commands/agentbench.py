"""
`mahoraga audit` (also `orch agentbench`): benchmark coding agents on a repo's own history.

Subcommands
-----------

  mahoraga audit mine [REPO]                  — turn commits into gated tasks
  mahoraga audit preflight [REPO] --arm ...   — is this machine fit to run?
  mahoraga audit run [REPO] --arm aider:qwen3.5:latest [--cond blind|feedback|both]
                             [--repeats K] [--until HH:MM] [--wait-idle MIN]
  mahoraga audit report [REPO] [--json]
  mahoraga audit reseal [REPO]                — take the answer out of old task clones
  mahoraga audit queue add [REPO] --arm ... [--window 21:00-08:00]
  mahoraga audit queue ls                     — every job, and why it is where it is
  mahoraga audit worker                       — run queued jobs as their windows open
  mahoraga audit service install|status|stop|start|uninstall — the worker under launchd

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

from backend.orchestrator.agentbench import manifest, session
from backend.orchestrator.agentbench.agents import kill_live_agents, parse_arm
from backend.orchestrator.agentbench.bank import Bench, SuiteConfig, bench_root
from backend.orchestrator.agentbench.guard import Guard
from backend.orchestrator.agentbench.mine import find_candidates, layout_pytest_args, mine, reseal
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
_ARMS = typer.Option(..., "--arm", help="<agent>:<model>[@ctx=N], repeatable. "
                     "Context defaults to 32768 tokens.")
_RATIO = typer.Option(0.5, "--min-speed", help="Degrade attempts under this share of the model's median idle tok/s.")


def _agents(arms: list[str]):
    try:
        return [parse_arm(a) for a in arms]
    except ValueError as e:
        raise typer.BadParameter(str(e))


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
    args = (list(pytest_args) if pytest_args
            else prior.pytest_args if prior else layout_pytest_args(bench.repo))
    if args and not pytest_args and not prior:
        typer.echo(f"src layout: testing the task's code with pytest {' '.join(args)}")
    config = SuiteConfig(
        python=python or (prior.python if prior else _default_python(bench.repo)),
        pytest_args=args,
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
    ok = _print_checks(Guard(bench_root(), ratio=ratio).preflight(agents))
    n = len(Bench(repo).load_tasks())
    typer.echo(f"  {'ok  ' if n else 'FAIL'}  {'tasks':24} {n or 'none — run `mahoraga audit mine`'}")
    raise typer.Exit(0 if ok and n else 1)


@app.command("doctor")
def doctor_cmd(
    repo: Path = _REPO,
    arms: List[str] = _ARMS,
    cond: str = typer.Option("feedback", "--cond", help="blind, feedback, or both."),
    repeats: int = typer.Option(1, "--repeats", min=1),
    ratio: float = _RATIO,
) -> None:
    """Machine + bench checks, and how many nights an answer will take."""
    from backend.orchestrator.agentbench.doctor import bench_checks, estimate

    conds = list(CONDITIONS) if cond == "both" else [cond]
    agents = _agents(arms)
    bench = Bench(repo)
    typer.echo("machine")
    machine_ok = _print_checks(Guard(bench_root(), ratio=ratio).preflight(agents))
    typer.echo("bench")
    bench_ok = _print_checks(bench_checks(bench))
    if bench.load_tasks():
        typer.echo("")
        typer.echo(estimate(bench, agents, conds, repeats).fmt())
    if not (machine_ok and bench_ok):
        raise typer.Exit(1)


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
    wait_quiet: bool = typer.Option(False, "--wait-quiet", help="Pause while someone else's model is loaded in Ollama."),
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
        typer.echo("no tasks — run `mahoraga audit mine` first")
        raise typer.Exit(1)

    guard = None
    if guarded:
        guard = Guard(bench_root(), ratio=ratio, wait_idle=wait_idle * 60, wait_quiet=wait_quiet,
                      log=typer.echo)
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

    progress = _echo_attempt

    only = set(tasks) if tasks else None
    _, done, stopped = session.run(
        bench, agents, conds, only=only, timeout=timeout, repeats=repeats, guard=guard,
        deadline=deadline, progress=progress,
        settings={"conds": list(conds), "repeats": repeats, "timeout": timeout,
                  "until": until, "wait_idle_min": wait_idle, "guarded": guarded,
                  "wait_quiet": wait_quiet, "min_speed_ratio": ratio, "forced": force},
        started=lambda run_id: typer.echo(f"run {run_id} (manifest: {manifest.path(bench, run_id)})"),
        dropped=lambda arm, why: typer.echo(f"dropped {arm}: {why}"))
    typer.echo(f"{len(done)} attempts run ({stopped})")


def _echo_attempt(a) -> None:
    tag = f"DEGRADED ({'; '.join(a.degraded)})" if a.degraded else outcome(a)
    typer.echo(f"  {a.sha[:10]} {a.arm} {a.cond} r{a.rep}: {tag}  "
               f"f2p={a.f2p_pass}/{a.f2p_total} regressions={a.regressions} "
               f"{a.secs / 60:.1f}m")


@app.command("reseal")
def reseal_cmd(repo: Path = _REPO) -> None:
    """Take the answer out of task clones mined before 2026-10-13, keeping attempts.

    Each clone's tree is carried over object for object and checked equal, so
    the attempts already recorded ran on exactly what the sealed clone holds."""
    bench = Bench(repo)
    n = reseal(bench, lambda sha, msg: typer.echo(f"  {sha[:10]}  {msg}"))
    typer.echo(f"{n} clones sealed in {bench.dir}")


queue_app = typer.Typer(help="Runs that wait for their window and start on their own.",
                        no_args_is_help=True)
app.add_typer(queue_app, name="queue")


@queue_app.command("add")
def queue_add_cmd(
    repo: Path = _REPO,
    arms: List[str] = _ARMS,
    cond: str = typer.Option("blind", "--cond", help="blind, feedback, or both."),
    tasks: Optional[List[str]] = typer.Option(None, "--task", help="Only these task shas."),
    repeats: int = typer.Option(1, "--repeats", min=1),
    window: Optional[str] = typer.Option(None, "--window", help="HH:MM-HH:MM; no attempt starts outside it."),
    wait_quiet: bool = typer.Option(False, "--wait-quiet", help="Pause while someone else's model is loaded in Ollama."),
) -> None:
    """Queue a run. Each model's digest is pinned now, so the job fails rather
    than measure different weights if a tag moves before it runs."""
    from backend.orchestrator.agentbench.ollama import pulled_models, tagged
    from backend.orchestrator.agentbench.queue import Queue, parse_window

    if cond not in (*CONDITIONS, "both"):
        raise typer.BadParameter(f"--cond must be blind, feedback or both, not {cond!r}")
    if window:
        try:
            parse_window(window)
        except ValueError as e:
            raise typer.BadParameter(str(e))
    agents = _agents(arms)
    bench = Bench(repo)
    if not bench.load_tasks():
        typer.echo("no tasks — run `mahoraga audit mine` first")
        raise typer.Exit(1)
    pulled = pulled_models()
    missing = [a.model for a in agents if tagged(a.model) not in pulled]
    if missing:
        typer.echo(f"not pulled: {', '.join(missing)}")
        raise typer.Exit(1)
    job = Queue(bench_root()).add(
        str(bench.repo), arms, cond, tasks=tasks, repeats=repeats, window=window,
        wait_quiet=wait_quiet, pinned={a.model: pulled[tagged(a.model)] for a in agents})
    typer.echo(f"queued {job.id}: {bench.repo.name} {' '.join(arms)} {cond}"
               + (f", {window}" if window else ""))


@queue_app.command("ls")
def queue_ls_cmd() -> None:
    """Every job: its state, and what it waits for or why it ended."""
    from backend.orchestrator.agentbench.queue import Queue
    from backend.orchestrator.agentbench.runner import open_cells

    jobs = Queue(bench_root()).jobs()
    if not jobs:
        typer.echo("the queue is empty")
        return
    for j in jobs:
        try:
            conds = CONDITIONS if j.cond == "both" else (j.cond,)
            left, total = open_cells(Bench(Path(j.repo)), _agents(j.arms), conds,
                                     set(j.tasks) or None, j.repeats)
            cells = f"{total - left}/{total} cells"
        except Exception as e:  # noqa: BLE001 — a broken job still gets its line
            cells = f"? cells ({e})"[:60]
        typer.echo(f"{j.id}  {j.status:8} {Path(j.repo).name:16} {' '.join(j.arms)}  "
                   f"{j.cond}  {j.window or 'any time'}  {cells}")
        typer.echo(f"        {j.why or 'not looked at yet: is a worker running?'}"
                   + (f"  ({j.checked_at})" if j.checked_at else ""))


@queue_app.command("cancel")
def queue_cancel_cmd(job_id: str) -> None:
    """Stop a job from running again. Its attempts are kept."""
    from backend.orchestrator.agentbench.queue import FAILED, Queue

    Queue(bench_root()).update(job_id, status=FAILED, why="cancelled")
    typer.echo(f"cancelled {job_id}")


@queue_app.command("retry")
def queue_retry_cmd(job_id: str) -> None:
    """Put a finished or failed job back in the queue, e.g. after pulling its model."""
    from backend.orchestrator.agentbench.queue import PENDING, Queue

    Queue(bench_root()).update(job_id, status=PENDING, why="requeued")
    typer.echo(f"requeued {job_id}")


service_app = typer.Typer(help="Keep the queue worker running under launchd.",
                          no_args_is_help=True)
app.add_typer(service_app, name="service")
_SERVICE_CLI = "mahoraga audit service"


def _worker_job(workdir: Optional[Path] = None, env: Optional[List[str]] = None):
    from backend.orchestrator.cli.commands.service import Job

    pairs = {}
    for kv in env or []:
        key, sep, value = kv.partition("=")
        if not sep or not key:
            raise typer.BadParameter(f"--env takes KEY=VALUE, not {kv!r}")
        pairs[key] = value
    return Job("com.mahoraga.audit-worker", ("agentbench", "worker"),
               bench_root() / "worker.log", workdir=(workdir or Path.cwd()).resolve(), env=pairs)


@service_app.command("install")
def service_install_cmd(
    workdir: Optional[Path] = typer.Option(None, "--workdir", help="The worker's working directory (default: here)."),
    env: Optional[List[str]] = typer.Option(None, "--env", help="KEY=VALUE for the worker, repeatable."),
) -> None:
    """Run the queue worker in the background, restarted if it dies."""
    from backend.orchestrator.cli.commands.service import install_job

    install_job(_worker_job(workdir, env), _SERVICE_CLI)


@service_app.command("uninstall")
def service_uninstall_cmd() -> None:
    """Stop the worker and remove its launchd job. The queue is kept."""
    from backend.orchestrator.cli.commands.service import uninstall_job

    uninstall_job(_worker_job())


@service_app.command("start")
def service_start_cmd() -> None:
    from backend.orchestrator.cli.commands.service import start_job

    start_job(_worker_job(), _SERVICE_CLI)


@service_app.command("stop")
def service_stop_cmd() -> None:
    from backend.orchestrator.cli.commands.service import stop_job

    stop_job(_worker_job(), _SERVICE_CLI)


@service_app.command("status")
def service_status_cmd() -> None:
    """Whether the worker runs, its last log lines, and the queue."""
    from backend.orchestrator.cli.commands.service import status_job

    status_job(_worker_job())
    typer.echo("")
    queue_ls_cmd()


@app.command("worker")
def worker_cmd(
    once: bool = typer.Option(False, "--once", help="Exit when nothing can run now, instead of waiting."),
    poll: float = typer.Option(5, "--poll", help="Minutes between looks at the queue when idle."),
) -> None:
    """Run queued jobs as their windows open. Meant to stay up (launchd, tmux)."""
    from backend.orchestrator.agentbench.queue import Queue, drain
    from backend.orchestrator.agentbench.worker import run_job

    queue = Queue(bench_root())
    _stay_awake()

    def stop(signum, frame):
        kill_live_agents()
        raise typer.Exit(130)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def stamp(msg: str) -> None:
        typer.echo(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}")

    drain(queue, lambda job, deadline: run_job(queue, job, deadline, progress=_echo_attempt,
                                               log=stamp),
          poll=poll * 60, once=once, log=stamp)


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
