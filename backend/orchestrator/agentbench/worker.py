"""Run one queued job, and leave on it why it stopped or what it waits for."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import httpx

from . import isolation, session
from .agents import Agent, parse_arm
from .bank import Bench, SuiteConfig
from .guard import Guard
from .ollama import pulled_models
from .queue import DONE, FAILED, Job, Queue, pin_problems
from .runner import CONDITIONS, Attempt, open_cells


def run_job(queue: Queue, job: Job, deadline: float | None, *,
            pulled: Callable[[], dict[str, str]] = pulled_models,
            guard: Callable[..., Guard] = Guard,
            agents_for: Callable[[list[str]], list[Agent]] = lambda arms: [parse_arm(a) for a in arms],
            which: Callable[[str], str | None] = shutil.which,
            isolated: Callable[[Bench, SuiteConfig], list[str]] = isolation.problems,
            progress: Callable[[Attempt], None] = lambda a: None,
            log: Callable[[str], None] = print) -> int:
    """Attempts counted. Every way out of here sets the job's `why`."""
    def fail(why: str) -> int:
        queue.update(job.id, status=FAILED, why=why)
        log(f"job {job.id} failed: {why}")
        return 0

    try:
        agents = agents_for(job.arms)
    except ValueError as e:
        return fail(str(e))
    try:
        models = pulled()
    except httpx.HTTPError as e:
        queue.update(job.id, why=f"waiting: Ollama not reachable ({e})")
        return 0
    if problems := pin_problems(job.pinned, models):
        return fail("; ".join(problems))
    if missing := sorted({a.name for a in agents if not which(a.name)}):
        return fail(f"not on PATH: {', '.join(missing)}")
    bench = Bench(Path(job.repo), root=queue.root)
    if not bench.load_tasks():
        return fail("the bench has no tasks: run `mahoraga audit mine` on the repo")

    if leaks := isolated(bench, bench.load_config()):
        return fail("not isolated: " + "; ".join(leaks))

    conds = CONDITIONS if job.cond == "both" else (job.cond,)
    only = set(job.tasks) or None
    left, total = open_cells(bench, agents, conds, only, job.repeats)
    if not left:
        queue.update(job.id, status=DONE, why=f"all {total} cells counted")
        return 0

    waited: list[str] = []

    def guard_log(msg: str) -> None:
        log(msg)
        if msg.startswith("paused: "):
            waited.append(msg[len("paused: "):])
            queue.update(job.id, why=f"waiting: {waited[-1]}")
        elif msg == "resumed":
            queue.update(job.id, why="running")

    dropped: list[str] = []
    runs = list(job.runs)

    def started(run_id: str) -> None:
        runs.append(run_id)
        queue.update(job.id, runs=runs, why="running")

    try:
        _, done, stopped = session.run(
            bench, agents, conds, only=only, repeats=job.repeats, deadline=deadline,
            guard=guard(bench.dir.parent, wait_quiet=job.wait_quiet, log=guard_log),
            settings={"conds": list(conds), "repeats": job.repeats, "queue_job": job.id,
                      "window": job.window, "wait_quiet": job.wait_quiet, "guarded": True},
            progress=progress, started=started,
            dropped=lambda arm, why: (dropped.append(f"{arm}: {why}"),
                                      log(f"dropped {arm}: {why}")))
    except BaseException:
        queue.update(job.id, why="interrupted; resumes when the worker restarts")
        raise

    left, total = open_cells(bench, agents, conds, only, job.repeats)
    notes = "; ".join(f"dropped {d}" for d in dropped)
    if not left:
        queue.update(job.id, status=DONE, why=f"all {total} cells counted")
    elif stopped == "completed" and len(dropped) == len(agents):
        fail(notes)
    elif stopped == "completed" and not done:
        # A pass that counted nothing: what's left only degrades.
        queue.update(job.id, status=DONE, why="; ".join(filter(None, [
            f"{left} of {total} cells left open, every try degraded", notes])))
    elif stopped == "deadline":
        reopen = f"{job.window.split('-')[0]}" if job.window else "next start"
        last = f"; last wait: {waited[-1]}" if waited and not done else ""
        queue.update(job.id, why="; ".join(filter(None, [
            f"window closed with {left} of {total} cells open, {len(done)} counted this "
            f"window{last}; resumes {reopen}", notes])))
    else:
        queue.update(job.id, why="; ".join(filter(None, [
            f"pass done, {left} of {total} cells open; running another", notes])))
    return len(done)

