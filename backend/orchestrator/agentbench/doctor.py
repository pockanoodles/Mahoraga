"""Doctor: can this bench produce an answer, and how many nights will it take?

`preflight` asks whether the *machine* is fit to run now (charger, agent,
models, speed). The doctor adds the *bench*: tasks mined, the test interpreter
present, clones in place, disk space. Then it estimates the work left and the
precision it will buy. That's the question a first-time user actually has:
"if I leave this running, what do I get, and when?"
"""
from __future__ import annotations

import math
import os
import shutil
import statistics
from dataclasses import dataclass

from .agents import Agent
from .bank import Bench
from .guard import Check
from .runner import _artifact, arm_name, load_attempts
from .stats import task_rate, wilson

# From the agent-edit probe: qwen3.5 at 32k on the 16 GB laptop ran 8.4–13.1
# min per attempt. Used only until the arm has attempts on this machine.
DEFAULT_MINUTES = 12.0
ASSUMED = "assumed from the 16 GB probe at 32k"
MIN_FREE_GB = 5.0


def bench_checks(bench: Bench) -> list[Check]:
    checks: list[Check] = []
    tasks = bench.load_tasks()
    checks.append(Check("tasks", bool(tasks), f"{len(tasks)} mined" if tasks
                        else "none — run `mahoraga audit mine`"))
    try:
        cfg = bench.load_config()
    except FileNotFoundError:
        return checks
    py_ok = os.access(cfg.python, os.X_OK)
    checks.append(Check("test interpreter", py_ok, cfg.python if py_ok
                        else f"missing: {cfg.python} — rebuild the repo's test venv"))
    if cfg.pytest_args:
        checks.append(Check("pytest args", True, " ".join(cfg.pytest_args)))
    missing = [t.sha for t in tasks if not bench.clone(t.sha).exists()]
    checks.append(Check("task clones", not missing, "all present" if not missing
                        else f"{len(missing)} missing — re-run `mahoraga audit mine`"))
    bench.dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(bench.dir).free / 2**30
    checks.append(Check("disk", free >= MIN_FREE_GB, f"{free:.0f} GB free"))
    return checks


@dataclass(frozen=True)
class Estimate:
    pending: int
    minutes_per_attempt: float
    measured_minutes: bool
    nights: int
    tasks: int
    expected_half_width: float  # at the end of the run, for one arm/condition
    night_hours: float = 8.0
    source: str = ASSUMED  # where minutes_per_attempt came from
    ahead: int = 0  # attempts the worker runs first, from jobs queued before this one
    ahead_minutes: float = 0.0

    def fmt(self) -> str:
        if not self.pending:
            return "nothing left to run for these arms and conditions"
        behind = (f", behind {self.ahead} attempt{'s' if self.ahead != 1 else ''} already "
                  f"queued (~{self.ahead_minutes / 60:.1f} h)" if self.ahead else "")
        return (f"{self.pending} attempts left at ~{self.minutes_per_attempt:.0f} min each "
                f"({self.source}){behind} ≈ {self.nights} night(s) of {self.night_hours:g} h. "
                f"With {self.tasks} tasks, each arm/condition's rate will come out within "
                f"about ±{self.expected_half_width:.0%}.")


def minutes_per_attempt(bench: Bench, arms: set[str]) -> tuple[float, str]:
    """Median minutes an attempt takes with these arms, and where that came from.
    This bench's attempts come first. Other repos' benches on this machine come
    next, since the same model and context on another repo is a better guess
    than a probe at another context on another machine."""
    secs = [a.secs for a in load_attempts(bench) if a.arm in arms]
    if secs:
        return statistics.median(secs) / 60, "measured here"
    root = bench.dir.parent
    others = [a.secs for d in (sorted(root.iterdir()) if root.is_dir() else [])
              if d != bench.dir and (d / "attempts").is_dir()
              for a in load_attempts(Bench.from_dir(d)) if a.arm in arms]
    if others:
        return (statistics.median(others) / 60,
                f"measured on {len(others)} attempt{'s' if len(others) != 1 else ''} in other repos")
    return DEFAULT_MINUTES, ASSUMED


def estimate(bench: Bench, agents: list[Agent], conds: list[str], repeats: int = 1,
             night_hours: float = 8.0, ahead: int = 0, ahead_minutes: float = 0.0) -> Estimate:
    """`ahead` is work queued before this (see tonight.queued_ahead): the worker
    runs it first, so it moves this run's last night later."""
    tasks = bench.load_tasks()
    attempts = load_attempts(bench)
    pending = sum(
        1 for rep in range(repeats) for a in agents for c in conds for t in tasks
        if not _artifact(bench, arm_name(a), c, t.sha, rep, "json").exists())
    arms = {arm_name(a) for a in agents}
    minutes, source = minutes_per_attempt(bench, arms)
    night = night_hours * 60
    # Precision: at the measured rate if there is one, else the widest case.
    by_task: dict[str, list[bool]] = {}
    for a in attempts:
        if a.arm in arms:
            by_task.setdefault(a.sha, []).append(a.resolved)
    p = task_rate(by_task).point if by_task else 0.5
    lo, hi = wilson(p * len(tasks), len(tasks))
    nights = math.ceil((ahead_minutes + pending * minutes) / night) if pending else 0
    return Estimate(pending, minutes, source != ASSUMED, nights, len(tasks),
                    (hi - lo) / 2 if tasks else 0.5, night_hours, source, ahead, ahead_minutes)
