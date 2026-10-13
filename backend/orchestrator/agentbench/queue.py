"""A queue of audit runs that a worker drains on its own.

Until this, every night's run was a hand-written shell script armed by hand,
and one that never started looked exactly like one that hadn't run yet: the
2026-10-02 night left an empty log and nobody knew for days. A queued job
says, at any moment, what it is waiting for or why it stopped.

  add      a job: repo, arms, condition, tasks, repeats, and an optional
           window ("21:00-08:00") outside which no attempt starts. Each model's
           digest is pinned when the job is added, because a tag can move
           upstream (qwen3.5:latest did): a job whose model changed under it
           fails rather than measuring a different model under the same name.
  worker   takes the first pending job whose window is open and runs it
           guarded, through the same `session.run` as `audit run`. A job is
           done when every cell has a counted attempt, or when a pass adds
           none, since those cells only degrade and retrying them all night
           measures nothing. Out of window, it stays pending and resumes.

The file is `<bench root>/queue.json`, rewritten under a lock, so a job can be
added while the worker runs.
"""
from __future__ import annotations

import fcntl
import json
import secrets
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator

PENDING, DONE, FAILED = "pending", "done", "failed"


@dataclass
class Job:
    id: str
    repo: str
    arms: list[str]
    cond: str
    tasks: list[str]
    repeats: int
    window: str | None
    pinned: dict[str, str]  # model -> digest when the job was added
    added_at: str
    status: str = PENDING
    # What the job last waited for, or why it ended. Never blank once the
    # worker has looked at it: this is the line that says why nothing ran.
    why: str = ""
    checked_at: str = ""
    runs: list[str] = field(default_factory=list)
    wait_quiet: bool = False


def _stamp(now: float) -> str:
    return datetime.fromtimestamp(now).strftime("%Y-%m-%dT%H:%M:%S")


def parse_window(window: str) -> tuple[int, int]:
    """"21:00-08:00" -> (start, end) in minutes past midnight."""
    try:
        start, end = window.split("-")
        (h1, m1), (h2, m2) = (map(int, start.split(":")), map(int, end.split(":")))
    except ValueError:
        raise ValueError(f"a window is HH:MM-HH:MM, not {window!r}")
    if not (0 <= h1 < 24 and 0 <= h2 < 24 and 0 <= m1 < 60 and 0 <= m2 < 60):
        raise ValueError(f"a window is HH:MM-HH:MM, not {window!r}")
    return h1 * 60 + m1, h2 * 60 + m2


def window_deadline(window: str | None, now: float) -> float | None:
    """When the open window closes (epoch secs), or None if it isn't open now.
    No window is always open, and closes never: float('inf')."""
    if window is None:
        return float("inf")
    start, end = parse_window(window)
    t = datetime.fromtimestamp(now)
    minute = t.hour * 60 + t.minute
    midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
    wraps = end <= start  # 21:00-08:00 runs through midnight
    if not wraps and start <= minute < end:
        return (midnight + timedelta(minutes=end)).timestamp()
    if wraps and minute >= start:
        return (midnight + timedelta(days=1, minutes=end)).timestamp()
    if wraps and minute < end:
        return (midnight + timedelta(minutes=end)).timestamp()
    return None


class Queue:
    def __init__(self, root: Path) -> None:
        """`root` is the bench root: its benches are the ones the jobs run."""
        self.root = Path(root)
        self.path = self.root / "queue.json"
        self._lock = Path(root) / ".queue.lock"

    @contextmanager
    def edit(self) -> Iterator[list[Job]]:
        """The jobs, locked; whatever the block leaves in the list is saved."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            jobs = self._read()
            yield jobs
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(j) for j in jobs], indent=1))
            tmp.replace(self.path)

    def _read(self) -> list[Job]:
        return [Job(**j) for j in json.loads(self.path.read_text())] if self.path.exists() else []

    def jobs(self) -> list[Job]:
        return self._read()

    def add(self, repo: str, arms: list[str], cond: str, *, pinned: dict[str, str],
            tasks: list[str] | None = None, repeats: int = 1, window: str | None = None,
            wait_quiet: bool = False, now: float | None = None) -> Job:
        if window is not None:
            parse_window(window)
        job = Job(id=secrets.token_hex(3), repo=repo, arms=list(arms), cond=cond,
                  tasks=list(tasks or []), repeats=repeats, window=window, pinned=pinned,
                  added_at=_stamp(now or time.time()), wait_quiet=wait_quiet)
        with self.edit() as jobs:
            jobs.append(job)
        return job

    def update(self, job_id: str, now: float | None = None, **fields) -> Job:
        with self.edit() as jobs:
            job = next(j for j in jobs if j.id == job_id)
            for k, v in fields.items():
                setattr(job, k, v)
            job.checked_at = _stamp(now or time.time())
            return job

    def next_open(self, now: float) -> tuple[Job, float] | None:
        """The first pending job whose window is open, and when it closes."""
        for job in self.jobs():
            if job.status != PENDING:
                continue
            deadline = window_deadline(job.window, now)
            if deadline is not None:
                return job, deadline
        return None


def pin_problems(pinned: dict[str, str], pulled: dict[str, str]) -> list[str]:
    """Models that are missing, or whose tag now names different weights."""
    from .ollama import tagged

    def short(digest: str) -> str:
        return digest.split(":")[-1][:12]

    out = []
    for model, digest in sorted(pinned.items()):
        now = pulled.get(tagged(model))
        if now is None:
            out.append(f"{model} is not pulled")
        elif now != digest:
            out.append(f"{model} changed since the job was added "
                       f"(pinned {short(digest)}, now {short(now)})")
    return out


def drain(queue: Queue, run_job: Callable[[Job, float | None], None], *,
          now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
          poll: float = 300, once: bool = False, log: Callable[[str], None] = print) -> None:
    """Run jobs as their windows open, forever (or until nothing is runnable,
    with `once`). `run_job` gets the job and its window's close, None for no
    window, and records its own outcome on the queue."""
    idle_said = False
    while True:
        found = queue.next_open(now())
        if found:
            job, deadline = found
            idle_said = False
            log(f"job {job.id}: {Path(job.repo).name} {' '.join(job.arms)}")
            run_job(job, None if deadline == float("inf") else deadline)
            continue
        if once:
            return
        if not idle_said:
            log("nothing to run now; checking every %d min" % (poll // 60))
            idle_said = True
        sleep(poll)
