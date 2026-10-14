"""`mahoraga audit tonight`: from a repo to an answer by morning, in one command.

The command itself lives in the CLI. What's here is what it leans on and what
the worker does when the night ends: write the morning's report next to the
bench and say so with a notification, so the first thing a new user sees the
next day is the result rather than a terminal to go looking in.
"""
from __future__ import annotations

from pathlib import Path

from . import host, manifest
from .bank import Bench
from .queue import PENDING, Job, Queue, parse_window
from .report import render, summarise
from .runner import load_attempts, load_degraded

DEFAULT_ARM = "aider:qwen3.5:latest"
DEFAULT_WINDOW = "21:00-08:00"
# Mining stops at this many candidates: at ~12 min an attempt, one night's worth.
DEFAULT_LIMIT = 25
# Where a larger model starts to fit alongside a 131k context.
LARGE_MEMORY_GB = 48

INSTALL = {
    "aider": "uv tool install --python 3.12 aider-chat",
    "opencode": "npm install -g opencode-ai",
}


def window_hours(window: str) -> float:
    start, end = parse_window(window)
    return ((end - start) % (24 * 60) or 24 * 60) / 60


def larger_model_hint(memory_gb: object) -> str | None:
    """For a machine that can hold more than the default model. qwen3.6 35B-A3B
    is named because it is the one measured: on the 64 GB Studio it resolved
    31 of 92 outside tasks to qwen3.5's 9 (experiments/claims.json)."""
    if not isinstance(memory_gb, (int, float)) or memory_gb < LARGE_MEMORY_GB:
        return None
    return (f"this machine has {memory_gb:g} GB, enough for a larger model. On a 64 GB Mac "
            "Studio, qwen3.6 35B-A3B at a 131k context resolved about three times as many "
            "tasks as qwen3.5. To measure it here, pull it and pass "
            "--arm aider:<its tag>@ctx=131072 (alongside the default to compare them).")


def matching_job(queue: Queue, repo: Path, arms: list[str], cond: str,
                 window: str) -> Job | None:
    """A pending job that would run exactly this, so running `tonight` twice
    doesn't queue the night twice."""
    return next((j for j in queue.jobs() if j.status == PENDING and j.repo == str(repo)
                 and j.arms == list(arms) and j.cond == cond and j.window == window
                 and not j.follow), None)


def verdict_command(repo: Path, arm: str, cond: str) -> str:
    return (f"mahoraga audit verdict {repo} --arm {arm} --cond {cond} \\\n"
            "    --tasks-per-month <how many you give cloud agents> --plan <your plan>")


def morning_path(bench: Bench) -> Path:
    return bench.dir / "morning.txt"


def morning(bench: Bench, job: Job) -> tuple[str, str]:
    """(the morning file, a one-line summary for the notification)."""
    tasks, attempts = bench.load_tasks(), load_attempts(bench)
    mine = [a for a in attempts if a.arm in job.arms]
    report = render(tasks, mine, load_degraded(bench), manifest.load_all(bench))
    lines = [f"Mahoraga: {bench.repo.name}, queued {job.added_at[:10]}",
             f"the queue: {job.status}, {job.why}", "", report, "",
             "next: the verdict, which needs how you use cloud agents"]
    lines += [verdict_command(bench.repo, arm, job.cond) for arm in job.arms]
    summary = "; ".join(f"{s.arm.split(':', 1)[-1]} resolved {s.tasks_solved_ever} of {s.tasks}"
                        for s in summarise(tasks, mine) if s.cond == job.cond)
    return "\n".join(lines) + "\n", summary or "no attempts counted"


def wrap_up(bench: Bench, job: Job, notify=host.notify) -> Path:
    """Write the morning file and say where it is."""
    text, summary = morning(bench, job)
    path = morning_path(bench)
    path.write_text(text)
    notify(f"Mahoraga: {bench.repo.name}", f"{summary}. mahoraga audit report {bench.repo}")
    return path
