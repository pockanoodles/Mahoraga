"""
`mahoraga audit` (also `orch agentbench`): benchmark coding agents on a repo's own history.

Subcommands
-----------

  mahoraga audit tonight [REPO]               — mine, check, queue and start the worker:
                                                an answer by morning
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

from backend.orchestrator.agentbench import manifest, session, tonight
from backend.orchestrator.agentbench.agents import kill_live_agents, parse_arm
from backend.orchestrator.agentbench.bank import Bench, SuiteConfig, bench_root
from backend.orchestrator.agentbench.guard import Guard
from backend.orchestrator.agentbench.mine import find_candidates, layout_pytest_args, mine, reseal
from backend.orchestrator.agentbench.report import (
    degraded_note, ledgers, outcome, render, summarise,
)
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
    if _mine(Bench(repo), limit, max_lines, rev, commits, python, pytest_args) is None:
        raise typer.Exit(1)


def _mine(bench: Bench, limit: int, max_lines: int = 250, rev: str = "HEAD",
          commits: Optional[List[str]] = None, python: Optional[str] = None,
          pytest_args: Optional[List[str]] = None):
    """(tasks, dropped) after mining, or None when there was no commit to try."""
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
        return None
    typer.echo(f"building {len(shas)} candidates into {bench.dir}")

    def progress(sha, task, reason):
        typer.echo(f"  {sha[:10]}  " + (
            f"ok  f2p={len(task.f2p)} p2p={len(task.p2p)} files={len(task.gold_src)} "
            f"lines={task.gold_lines}" if task else f"drop  {reason}"))

    tasks, dropped = mine(bench, config, shas, progress)
    typer.echo(f"{len(tasks)} tasks in the bench, {len(dropped)} dropped")
    return tasks, dropped


@app.command("preflight")
def preflight_cmd(
    repo: Path = _REPO,
    arms: List[str] = _ARMS,
    ratio: float = _RATIO,
) -> None:
    """Check this machine can produce valid results: charger, agent, models, speed."""
    from backend.orchestrator.agentbench import isolation

    agents = _agents(arms)
    ok = _print_checks(Guard(bench_root(), ratio=ratio).preflight(agents))
    bench = Bench(repo)
    n = len(bench.load_tasks())
    typer.echo(f"  {'ok  ' if n else 'FAIL'}  {'tasks':24} {n or 'none — run `mahoraga audit mine`'}")
    isolated = _print_checks(isolation.check(bench, bench.load_config())) if n else False
    raise typer.Exit(0 if ok and n and isolated else 1)


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


@app.command("tonight")
def tonight_cmd(
    repo: Path = _REPO,
    arms: Optional[List[str]] = typer.Option(None, "--arm", help="<agent>:<model>[@ctx=N], repeatable (default: aider:qwen3.5:latest@ctx=131072)."),
    cond: str = typer.Option("blind", "--cond", help="blind, feedback, or both."),
    window: str = typer.Option(tonight.DEFAULT_WINDOW, "--window", help="HH:MM-HH:MM; no attempt starts outside it."),
    limit: int = typer.Option(tonight.DEFAULT_LIMIT, "--limit", help="Newest commits to mine, if the repo has no tasks yet."),
    python: Optional[str] = typer.Option(None, "--python", help="Interpreter for the repo's tests."),
    wait_quiet: bool = typer.Option(False, "--wait-quiet", help="Pause while someone else's model is loaded in Ollama."),
    timeout: Optional[float] = typer.Option(None, "--timeout", help="Seconds per attempt (default: sized to this machine's speed, measured once)."),
    tasks_per_month: Optional[int] = typer.Option(None, "--tasks-per-month", help="Coding tasks you give cloud agents a month (asked once, then saved)."),
    plan_id: Optional[str] = typer.Option(None, "--plan", help="Your plan id from audit/pricing.json, e.g. claude-max-5x."),
    plan_price: Optional[float] = typer.Option(None, "--plan-price", help="Your plan's monthly price, if it isn't verified in pricing.json."),
    api_spend: Optional[float] = typer.Option(None, "--api-spend", help="Monthly API spend, USD, if you pay per token."),
    ratio: float = _RATIO,
) -> None:
    """From a repo to an answer by morning: mine, check, queue, start the worker."""
    import time

    from backend.orchestrator.agentbench import host, isolation
    from backend.orchestrator.agentbench.doctor import estimate
    from backend.orchestrator.audit import pricing
    from backend.orchestrator.agentbench.queue import Queue, parse_window, window_deadline

    if cond not in (*CONDITIONS, "both"):
        raise typer.BadParameter(f"--cond must be blind, feedback or both, not {cond!r}")
    try:
        parse_window(window)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    chosen = bool(arms)
    arms = list(arms or [tonight.DEFAULT_ARM])
    agents = _agents(arms)
    conds = list(CONDITIONS) if cond == "both" else [cond]
    bench = Bench(repo)

    typer.echo("1. tasks from your history")
    if bench.load_tasks():
        typer.echo(f"  {len(bench.load_tasks())} already mined (`mahoraga audit mine` adds more)")
    else:
        mined = _mine(bench, limit, python=python)
        if not mined or not mined[0]:
            if mined:
                from collections import Counter
                for why, n in Counter(mined[1].values()).most_common(3):
                    typer.echo(f"  {n} dropped: {why}")
            typer.echo("no tasks to run: a task is a commit that changed source and a test "
                       "that fails before it and passes after")
            raise typer.Exit(1)

    typer.echo("2. this machine")
    checks = Guard(bench_root(), ratio=ratio).preflight(agents)
    checks += isolation.check(bench, bench.load_config())
    _print_checks(checks)
    blocking = [c for c in checks if not c.ok and not c.waits]
    for c in blocking:
        if c.name in tonight.INSTALL:
            typer.echo(f"  install {c.name}: {tonight.INSTALL[c.name]}")
        elif c.name == "ollama":
            typer.echo("  start Ollama (open the app, or `ollama serve`)")
    if blocking:
        typer.echo("fix the FAIL lines above and run this again; nothing was queued")
        raise typer.Exit(1)
    if any(not c.ok for c in checks):
        typer.echo("  the run waits these out: it starts each attempt only on a healthy machine")
    if not chosen and (hint := tonight.larger_model_hint(manifest.machine().get("memory_gb"))):
        typer.echo(f"  {hint}")

    typer.echo("3. the queue")
    queue = Queue(bench_root())
    job = tonight.matching_job(queue, bench.repo, arms, cond, window)
    ahead, ahead_minutes = tonight.queued_ahead(queue, window, job)
    est = estimate(bench, agents, conds, night_hours=tonight.window_hours(window),
                   ahead=ahead, ahead_minutes=ahead_minutes)
    typer.echo(f"  {est.fmt()}")
    if not est.pending:
        typer.echo(f"\nthe report is ready: mahoraga audit report {bench.repo}")
        return
    if job:
        typer.echo(f"  already queued as {job.id}")
    else:
        from backend.orchestrator.agentbench.ollama import pulled_models, tagged

        pulled = pulled_models()
        job = queue.add(str(bench.repo), arms, cond, window=window, wait_quiet=wait_quiet,
                        notify=True, timeout=timeout,
                        pinned={a.model: pulled[tagged(a.model)] for a in agents})
        typer.echo(f"  queued {job.id}: {' '.join(arms)} {cond}, {window}")
    opens = window_deadline(window, time.time()) is None
    typer.echo(f"  the window opens at {window.split('-')[0]}" if opens else
               "  the window is open: the first attempt starts within a few minutes")

    typer.echo("4. how you use cloud agents")
    saved = _tonight_usage(tasks_per_month, plan_id, plan_price, api_spend)

    typer.echo("5. the worker")
    _ensure_worker()
    power = host.power()
    if power.percent is not None:
        typer.echo("  leave it plugged in with the lid open: the worker keeps it awake on "
                   "power, but closing the lid sleeps it")

    if missing := tonight.cli_missing():
        typer.echo(f"\n{missing}")
    typer.echo(f"\nin the morning: mahoraga audit report {bench.repo}")
    typer.echo(f"  (also written to {tonight.morning_path(bench)}, with a notification, "
               "when the window closes)")
    if saved:
        typer.echo("  with the verdict for each arm, also on demand:")
    else:
        typer.echo("then the verdict, which needs how you use cloud agents:")
    for arm in arms:
        for c in tonight.conds_of(cond):
            typer.echo(f"  {tonight.verdict_command(bench.repo, arm, c, saved=bool(saved))}")
    if not saved:
        typer.echo("  plans: " + ", ".join(sorted(pricing.load())) + ", or --api-spend <USD a month>")


def _interactive() -> bool:
    return sys.stdin.isatty()


def _tonight_usage(tasks_per_month: Optional[int], plan_id: Optional[str],
                   plan_price: Optional[float], api_spend: Optional[float]):
    """What the verdict needs from you: from the flags, what was saved, or
    asked once in a terminal. Saved under the bench root, never sent anywhere."""
    from backend.orchestrator.audit import pricing, usage

    saved = usage.load(bench_root())
    given = any(x is not None for x in (tasks_per_month, plan_id, api_spend))
    if not given and saved:
        typer.echo(f"  {saved.describe()}, as saved (change it with --tasks-per-month, "
                   "--plan or --api-spend)")
        return saved
    if not given and not _interactive():
        typer.echo("  skipped: pass --tasks-per-month and --plan (or --api-spend) and the "
                   "morning file carries the verdict")
        return None
    if given:
        if plan_id is not None and api_spend is not None:
            raise typer.BadParameter("give exactly one of --api-spend or --plan")
        tasks = tasks_per_month or (saved.tasks_per_month if saved else None)
        if tasks is None:
            raise typer.BadParameter("--plan and --api-spend need --tasks-per-month")
        if plan_id is not None or api_spend is not None:
            paid = {"plan": plan_id, "plan_price": plan_price, "api_spend": api_spend}
        elif saved:
            paid = {"plan": saved.plan, "plan_price": saved.plan_price, "api_spend": saved.api_spend}
        else:
            raise typer.BadParameter("--tasks-per-month needs --plan or --api-spend")
        u = usage.Usage(tasks_per_month=tasks, **paid)
        try:
            usage.save(bench_root(), u)
        except (ValueError, KeyError) as e:
            raise typer.BadParameter(str(e))
    else:
        typer.echo("  the verdict weighs your results against what the cloud costs you. "
                   "Asked once, kept on this machine (blank to skip):")
        while True:
            tasks = typer.prompt("  coding tasks you give cloud agents a month", default="",
                                 show_default=False).strip()
            if not tasks:
                typer.echo("  skipped: the morning file prints the verdict command instead")
                return None
            if tasks.isdigit() and int(tasks) > 0:
                break
            typer.echo(f"  a count of tasks, like 120, not {tasks!r}")
        while True:
            answer = typer.prompt(f"  how you pay: a plan ({', '.join(sorted(pricing.load()))}), "
                                  "a plan and its price, or USD a month of API")
            try:
                u = usage.Usage(tasks_per_month=int(tasks), **usage.parse_payment(answer))
                usage.save(bench_root(), u)
                break
            except (ValueError, KeyError) as e:
                typer.echo(f"  {e}")
    typer.echo(f"  saved: {u.describe()}")
    return u


def _ensure_worker() -> None:
    """The launchd worker running, installed if it isn't. An install that
    already exists keeps its own settings (a workdir, an env)."""
    if sys.platform != "darwin":
        typer.echo("  start it and leave it running: mahoraga audit worker "
                   "(in tmux, or as a systemd user service)")
        return
    from backend.orchestrator.cli.commands.service import _is_loaded, install_job, start_job

    job = _worker_job(bench_root())
    if job.plist.exists() and _is_loaded(job):
        typer.echo("  running under launchd")
    elif job.plist.exists():
        start_job(job, _SERVICE_CLI)
    else:
        install_job(job, _SERVICE_CLI)


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
        from backend.orchestrator.agentbench import isolation

        machine_ok = _print_checks(guard.preflight(agents))
        isolated = _print_checks(isolation.check(bench, bench.load_config()))
        if not (machine_ok and isolated) and not force:
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
    follow: bool = typer.Option(False, "--follow", help="Never finish: mine new commits each window and run them."),
    fetch: bool = typer.Option(False, "--fetch", help="With --follow, git fetch first (a repo others commit to)."),
    timeout: Optional[float] = typer.Option(None, "--timeout", help="Seconds per attempt (default: sized to this machine's speed, measured once)."),
) -> None:
    """Queue a run. Each model's digest is pinned now, so the job fails rather
    than measure different weights if a tag moves before it runs."""
    if fetch and not follow:
        raise typer.BadParameter("--fetch only applies with --follow")
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
    try:
        job = Queue(bench_root()).add(
            str(bench.repo), arms, cond, tasks=tasks, repeats=repeats, window=window,
            wait_quiet=wait_quiet, follow=follow, fetch=fetch, timeout=timeout,
            pinned={a.model: pulled[tagged(a.model)] for a in agents})
    except ValueError as e:
        raise typer.BadParameter(str(e))
    typer.echo(f"queued {job.id}: {bench.repo.name} {' '.join(arms)} {cond}"
               + (f", {window}" if window else "") + (", following new commits" if follow else ""))


@queue_app.command("ls")
def queue_ls_cmd() -> None:
    """Every job: its state, and what it waits for or why it ended."""
    import time

    from backend.orchestrator.agentbench.queue import Queue, window_deadline
    from backend.orchestrator.agentbench.runner import open_cells

    jobs = Queue(bench_root()).jobs()
    if not jobs:
        typer.echo("the queue is empty")
        return
    now = time.time()
    ahead = None  # the job a worker takes first, among those it could run now
    for j in jobs:
        try:
            conds = CONDITIONS if j.cond == "both" else (j.cond,)
            left, total = open_cells(Bench(Path(j.repo)), _agents(j.arms), conds,
                                     set(j.tasks) or None, j.repeats)
            cells = f"{total - left}/{total} cells"
        except Exception as e:  # noqa: BLE001 — a broken job still gets its line
            cells = f"? cells ({e})"[:60]
        typer.echo(f"{j.id}  {j.status:8} {Path(j.repo).name:16} {' '.join(j.arms)}  "
                   f"{j.cond}  {j.window or 'any time'}  {cells}"
                   + ("  follow" + ("+fetch" if j.fetch else "") if j.follow else ""))
        runnable = (j.status == "pending" and j.rest_until <= now
                    and window_deadline(j.window, now) is not None)
        if j.why:
            why = j.why
        elif j.window and window_deadline(j.window, now) is None:
            why = f"waiting for its window to open at {j.window.split('-')[0]}"
        elif runnable and ahead:
            why = f"queued behind {ahead}"
        else:
            why = "its window is open but no worker has looked at it: is one running?"
        if runnable and ahead is None:
            ahead = j.id
        typer.echo(f"        {why}" + (f"  ({j.checked_at})" if j.checked_at else ""))


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
    since: Optional[str] = typer.Option(None, "--since", help="Only tasks committed on or after YYYY-MM-DD, e.g. a model's release: work it can't have trained on."),
) -> None:
    """Per-arm resolve rates, failure modes, and a per-task matrix."""
    bench = Bench(repo)
    tasks, attempts, degraded = bench.load_tasks(), load_attempts(bench), load_degraded(bench)
    if since:
        try:
            datetime.strptime(since, "%Y-%m-%d")
        except ValueError:
            raise typer.BadParameter(f"--since takes YYYY-MM-DD, not {since!r}")
        undated = sum(not t.committed for t in tasks)
        tasks = [t for t in tasks if t.committed and t.committed[:10] >= since]
        live = {t.sha for t in tasks}
        attempts = [a for a in attempts if a.sha in live]
        degraded = [a for a in degraded if a.sha in live]
        if not json_out:
            typer.echo(f"tasks committed on or after {since}: {len(tasks)}"
                       + (f" ({undated} undated left out: run `mahoraga audit reseal`)" if undated else ""))
    if json_out:
        typer.echo(json.dumps({
            "repo": str(bench.repo), "tasks": len(tasks),
            "arms": [asdict(s) for s in summarise(tasks, attempts)],
            "degraded": len(degraded), "degraded_note": degraded_note(degraded),
            "ledgers": [asdict(g) for g in ledgers(attempts, degraded, manifest.load_all(bench))],
        }, indent=2))
        return
    typer.echo(render(tasks, attempts, degraded, manifest.load_all(bench)))


@app.command("export")
def export_cmd(
    repos: List[Path] = typer.Argument(..., help="Benched repos to export."),
    out: Path = typer.Option(..., "--out", help="The JSONL file to write."),
    arms: List[str] = typer.Option([], "--arm", help="Only this arm (repeatable); default all."),
    conds: List[str] = typer.Option([], "--cond", help="Only this condition (repeatable)."),
) -> None:
    """Counted attempts, one line each: what `orch bench verify` recomputes the
    published rates and verdicts from."""
    from backend.orchestrator.agentbench import export

    n = export.write(out, [Bench(r) for r in repos], arms, conds)
    typer.echo(f"{n} attempts -> {out}")


@app.command("verdict")
def verdict_cmd(
    repos: List[Path] = typer.Argument(..., help="Benched repos to pool evidence from."),
    arm: str = typer.Option(..., "--arm", help="The one arm to judge, <agent>:<model>."),
    cond: str = typer.Option("feedback", "--cond", help="blind or feedback (a real queue can run tests)."),
    tasks_per_month: Optional[int] = typer.Option(None, "--tasks-per-month", help="Coding tasks you give cloud agents a month (default: what `tonight` saved)."),
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
    from backend.orchestrator.audit import usage
    from backend.orchestrator.audit.verdict import render as render_verdict

    if api_spend is not None and plan_id is not None:
        raise typer.BadParameter("give exactly one of --api-spend or --plan")
    saved = usage.load(bench_root())
    if tasks_per_month is None and saved is None:
        raise typer.BadParameter("give --tasks-per-month (`mahoraga audit tonight` asks once "
                                 "and saves it)")
    if api_spend is not None:
        paid = {"api_spend": api_spend}
    elif plan_id is not None:
        paid = {"plan": plan_id, "plan_price": plan_price}
    elif saved is not None:
        paid = {"plan": saved.plan, "plan_price": saved.plan_price, "api_spend": saved.api_spend}
    else:
        raise typer.BadParameter("give exactly one of --api-spend or --plan")
    u = usage.Usage(tasks_per_month=tasks_per_month or saved.tasks_per_month, **paid)
    try:
        v = usage.judge([Bench(r) for r in repos], arm, cond, u, deferrable_share=deferrable,
                        triage_minutes=triage, hourly_usd=hourly, watts=watts,
                        usd_per_kwh=kwh, usage_of_cap=usage_of_cap)
    except (ValueError, KeyError) as e:
        raise typer.BadParameter(str(e))
    ev, a = v.evidence, v.assumptions
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
