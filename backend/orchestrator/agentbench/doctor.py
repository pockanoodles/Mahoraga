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

# From the agent-edit probe: qwen3.5 medians ran 8.4–13.1 min per attempt.
# Used only until this bench has attempts of its own.
DEFAULT_MINUTES = 12.0
MIN_FREE_GB = 5.0


def bench_checks(bench: Bench) -> list[Check]:
    checks: list[Check] = []
    tasks = bench.load_tasks()
    checks.append(Check("tasks", bool(tasks), f"{len(tasks)} mined" if tasks
                        else "none — run `orch agentbench mine`"))
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
                        else f"{len(missing)} missing — re-run `orch agentbench mine`"))
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

    def fmt(self) -> str:
        src = "measured here" if self.measured_minutes else "assumed from the probe"
        if not self.pending:
            return "nothing left to run for these arms and conditions"
        return (f"{self.pending} attempts left at ~{self.minutes_per_attempt:.0f} min each "
                f"({src}) ≈ {self.nights} night(s) of 8 h. With {self.tasks} tasks, each "
                f"arm/condition's rate will come out within about ±{self.expected_half_width:.0%}.")


def estimate(bench: Bench, agents: list[Agent], conds: list[str], repeats: int = 1,
             night_hours: float = 8.0) -> Estimate:
    tasks = bench.load_tasks()
    attempts = load_attempts(bench)
    pending = sum(
        1 for rep in range(repeats) for a in agents for c in conds for t in tasks
        if not _artifact(bench, arm_name(a), c, t.sha, rep, "json").exists())
    arms = {arm_name(a) for a in agents}
    secs = [a.secs for a in attempts if a.arm in arms]
    minutes = statistics.median(secs) / 60 if secs else DEFAULT_MINUTES
    per_night = max(1, int(night_hours * 60 // max(minutes, 0.1)))
    # Precision: at the measured rate if there is one, else the widest case.
    by_task: dict[str, list[bool]] = {}
    for a in attempts:
        if a.arm in arms:
            by_task.setdefault(a.sha, []).append(a.resolved)
    p = task_rate(by_task).point if by_task else 0.5
    lo, hi = wilson(p * len(tasks), len(tasks))
    return Estimate(pending, minutes, bool(secs), math.ceil(pending / per_night) if pending else 0,
                    len(tasks), (hi - lo) / 2 if tasks else 0.5)
