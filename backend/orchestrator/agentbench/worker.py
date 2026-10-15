"""Run one queued job, and leave on it why it stopped or what it waits for."""
from __future__ import annotations

import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import httpx

from . import isolation, limit, session, tonight
from .agents import Agent, parse_arm
from .bank import Bench, SuiteConfig
from .guard import Guard
from .mine import backfill_dates, mine_new
from .proc import git
from .ollama import pulled_models, tagged
from .queue import DONE, FAILED, PENDING, Job, Queue, pin_problems
from .runner import CONDITIONS, Attempt, open_cells


def run_job(queue: Queue, job: Job, deadline: float | None, *,
            pulled: Callable[[], dict[str, str]] = pulled_models,
            guard: Callable[..., Guard] = Guard,
            agents_for: Callable[[list[str]], list[Agent]] = lambda arms: [parse_arm(a) for a in arms],
            which: Callable[[str], str | None] = shutil.which,
            isolated: Callable[[Bench, SuiteConfig], list[str]] = isolation.problems,
            progress: Callable[[Attempt], None] = lambda a: None,
            wrap_up: Callable[[Bench, Job], object] = tonight.wrap_up,
            limit_for: Callable[..., float] = limit.for_agent,
            log: Callable[[str], None] = print) -> int:
    """Attempts counted. Every way out of here sets the job's `why`."""
    bench = Bench(Path(job.repo), root=queue.root)

    def morning() -> None:
        now = next(j for j in queue.jobs() if j.id == job.id)  # with the why just set
        try:
            log(f"job {job.id}: morning report at {wrap_up(bench, now)}")
        except Exception as e:  # noqa: BLE001 — the report is a courtesy, the job's state isn't
            log(f"job {job.id}: no morning report: {e}")

    def fail(why: str) -> int:
        queue.update(job.id, status=FAILED, why=why)
        log(f"job {job.id} failed: {why}")
        if job.notify:  # a night that never started still owes the morning an answer
            morning()
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
    if job.follow:
        try:
            new = follow(bench, job.fetch, log)
        except Exception as e:  # noqa: BLE001 — a failed fetch or mine is reported, not fatal
            queue.update(job.id, why=f"mining new commits failed: {e}"[:300])
            new = []
        if new:
            log(f"job {job.id}: {len(new)} new tasks mined")
    if not bench.load_tasks():
        return fail("the bench has no tasks: run `mahoraga audit mine` on the repo")

    if leaks := isolated(bench, bench.load_config()):
        return fail("not isolated: " + "; ".join(leaks))

    conds = CONDITIONS if job.cond == "both" else (job.cond,)
    only = set(job.tasks) or None
    left, total = open_cells(bench, agents, conds, only, job.repeats)
    if not left:
        return _settle(queue, job, deadline, total, f"all {total} cells counted")

    waited: list[str] = []

    def guard_log(msg: str) -> None:
        log(msg)
        if msg.startswith("paused: "):
            waited.append(msg[len("paused: "):])
            queue.update(job.id, why=f"waiting: {waited[-1]}")
        elif msg == "resumed":
            queue.update(job.id, why="running")

    g = guard(bench.dir.parent, wait_quiet=job.wait_quiet, log=guard_log)
    timeout = job.timeout
    if timeout is None:
        # The reading loads the model at its full context, so it waits for the
        # same conditions an attempt does: on AC, nobody else's model loaded.
        try:
            limits = [limit_for(queue.root, a, models.get(tagged(a.model), "?"), log=log,
                                ready=lambda a=a: g.wait_ready(a, deadline)) for a in agents]
        except (httpx.HTTPError, ValueError) as e:
            queue.update(job.id, why=f"waiting: couldn't measure this machine for the "
                                     f"attempt limit ({e})"[:300])
            return 0
        if None in limits:
            queue.update(job.id, why="window closed before the attempt limit could be "
                                     "measured; resumes at the next window")
            return 0
        timeout = max(limits)

    dropped: list[str] = []
    runs = list(job.runs)

    def started(run_id: str) -> None:
        runs.append(run_id)
        queue.update(job.id, runs=runs, why="running")

    try:
        _, done, stopped = session.run(
            bench, agents, conds, only=only, timeout=timeout, repeats=job.repeats,
            deadline=deadline,
            guard=g,
            settings={"conds": list(conds), "repeats": job.repeats, "timeout": timeout,
                      "queue_job": job.id,
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
        _settle(queue, job, deadline, total, f"all {total} cells counted")
    elif stopped == "completed" and len(dropped) == len(agents):
        fail(notes)
    elif stopped == "completed" and not done:
        # A pass that counted nothing: what's left only degrades.
        _settle(queue, job, deadline, total, "; ".join(filter(None, [
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
    status = next(j for j in queue.jobs() if j.id == job.id).status
    # A failed job has had its morning from fail().
    if job.notify and status != FAILED and (status != PENDING or stopped == "deadline"):
        morning()
    return len(done)



# A follow job with no window looks for new commits this often.
FOLLOW_EVERY = 6 * 3600


def _settle(queue: Queue, job: Job, deadline: float | None, total: int, why: str) -> int:
    """Nothing left to run: a job ends, a follow job rests until its window
    closes (or FOLLOW_EVERY, without one) and looks for new commits again."""
    if not job.follow:
        queue.update(job.id, status=DONE, why=why)
        return 0
    until = deadline if deadline else time.time() + FOLLOW_EVERY
    queue.update(job.id, rest_until=until, why=f"up to date, {why}; looks for new commits "
                                                f"after {datetime.fromtimestamp(until):%m-%d %H:%M}")
    return 0


def follow(bench: Bench, fetch: bool, log: Callable[[str], None]) -> list:
    """Fetch (for a repo others commit to), backfill dates, and mine new commits."""
    rev = "HEAD"
    if fetch:
        git("fetch", "-q", cwd=bench.repo)
        # The branch's upstream; for a checkout pinned to a commit (the outside
        # repos sit detached at the laptop's commits), the remote's default branch.
        rev = next((r for r in (
            git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", cwd=bench.repo,
                check=False).strip(),
            git("rev-parse", "--abbrev-ref", "origin/HEAD", cwd=bench.repo, check=False).strip(),
        ) if r and r != "origin/HEAD"), "HEAD")
    backfill_dates(bench)
    return mine_new(bench, bench.load_config(), rev,
                    progress=lambda sha, task, why: log(
                        f"  mined {sha[:10]}: " + ("ok" if task else f"drop {why}")))
