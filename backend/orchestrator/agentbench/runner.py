"""Run and grade attempts.

One attempt = reset the task's clone to its base, let the agent edit, restore
the test files, then hand the tree to a grader (grade.py). A commit-mined
task is graded by its tests, and the attempt records which tier graded it.

Tests are restored before grading so an agent can't pass by editing them;
an attempt that touched them is flagged either way.

Every attempt carries the id of the run that made it; the run's manifest
(manifest.py) says what machine, models and harness that was.

Attempts are written one file per (arm, condition, task), so a run killed
mid-way resumes where it stopped. An attempt that did not measure the agent —
the machine lost power, or ran slow and the attempt timed out or had requests
fail (with a guard), or the agent itself crashed — is filed under
`attempts/degraded/` instead, so it is kept but not counted, and the cell stays
open to be retried.
"""
from __future__ import annotations

import json
import re
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

from .agents import Agent, AgentRun
from .bank import Bench, Task, SuiteConfig
from .grade import TEST_VERIFIED, Grader, TestGrader
from .mine import is_test_path
from .proc import git

if TYPE_CHECKING:
    from .guard import Guard, Watch

CONDITIONS = ("blind", "feedback")
DEFAULT_TIMEOUT = 20 * 60

# Files an agent or the grader leaves behind that are not the agent's edit.
_NOT_AN_EDIT = re.compile(r"(^|/)(\.aider|\.opencode|\.agentbench-junit)")


@dataclass
class Attempt:
    sha: str
    arm: str
    cond: str
    resolved: bool
    f2p_pass: int
    f2p_total: int
    regressions: int
    regression_ids: list[str]
    suite_crashed: bool
    edited_files: list[str]
    touched_tests: list[str]
    hit_gold_file: bool
    secs: float
    timed_out: bool
    llm_calls: int
    tokens_sent: float
    tokens_recv: float
    extra: dict[str, int]
    rep: int = 0  # which repeat of this (arm, cond, task) cell
    # Why the machine invalidated this attempt; empty = it counts.
    degraded: list[str] = field(default_factory=list)
    conditions: dict = field(default_factory=dict)
    # Defaults keep attempts recorded before these fields existed loadable.
    tier: str = TEST_VERIFIED
    run_id: str = ""

    @property
    def no_edit(self) -> bool:
        return not [f for f in self.edited_files if f not in self.touched_tests]


def prompt_for(task: Task) -> str:
    parts = ["Make the following change to this repository.", task.subject, task.body,
             f"Tests covering this change already exist in: {', '.join(task.test_files)}. "
             "Do not modify any test files; change the source code so those tests pass "
             "without breaking anything else."]
    return "\n\n".join(p for p in parts if p)


def arm_name(agent: Agent) -> str:
    return f"{agent.name}:{agent.model}"


def _degraded_dir(bench: Bench) -> Path:
    return bench.attempts / "degraded"


def _artifact(bench: Bench, arm: str, cond: str, sha: str, rep: int, ext: str,
              try_no: int | None = None) -> Path:
    # Built by concatenation: arm names contain dots (qwen3.5), which
    # Path.with_suffix would treat as an extension and overwrite.
    stem = f"{re.sub(r'[^A-Za-z0-9._-]', '_', arm)}__{cond}__{sha}__r{rep}"
    if try_no is not None:  # a degraded attempt: kept apart, one file per try
        return _degraded_dir(bench) / f"{stem}__d{try_no}.{ext}"
    return bench.attempts / f"{stem}.{ext}"


def _next_degraded_try(bench: Bench, arm: str, cond: str, sha: str, rep: int) -> int:
    stem = _artifact(bench, arm, cond, sha, rep, "json", 0).name[:-len("__d0.json")]
    d = _degraded_dir(bench)
    return len(list(d.glob(f"{stem}__d*.json"))) if d.exists() else 0


def _reset(clone: Path, base: str) -> None:
    git("reset", "-q", "--hard", base, cwd=clone)
    git("clean", "-qfdx", "-e", ".aider.tags.cache.*", cwd=clone)


def _degraded(watch: Watch | None, run: AgentRun) -> list[str]:
    """Why this attempt did not measure the agent; empty = it counts. A slow
    machine counts against an attempt only through what slowness does: a
    timeout, or requests that failed."""
    out = list(watch.degraded) if watch else []
    if watch and watch.slow and (run.timed_out or run.request_errors):
        how = "timed out" if run.timed_out else f"had {run.request_errors} failed requests"
        out.append(f"{watch.slow}, and the attempt {how}")
    if run.crashed:
        out.append(f"agent crashed: {run.crashed}")
    return out


def run_attempt(bench: Bench, config: SuiteConfig, task: Task, agent: Agent,
                cond: str, timeout: float = DEFAULT_TIMEOUT, rep: int = 0,
                guard: Guard | None = None, grader: Grader | None = None,
                run_id: str = "") -> Attempt:
    if cond not in CONDITIONS:
        raise ValueError(f"condition must be one of {CONDITIONS}")
    clone = bench.clone(task.sha)
    _reset(clone, task.base)
    test_cmd = ([config.python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                 *config.pytest_args, *task.test_files] if cond == "feedback" else None)
    watching = guard.watch(agent) if guard else nullcontext(None)
    with watching as watch:
        run = agent.attempt(clone, prompt_for(task), test_cmd=test_cmd, timeout=timeout)

    status = git("status", "--porcelain", "--untracked-files=all", cwd=clone)
    edited = sorted({line[3:].strip().strip('"') for line in status.splitlines()
                     if not _NOT_AN_EDIT.search(line[3:])})
    touched_tests = [f for f in edited if is_test_path(f)]
    patch = git("diff", cwd=clone)

    for f in touched_tests:
        if git("ls-tree", "--name-only", task.base, "--", f, cwd=clone).strip():
            git("checkout", "-q", task.base, "--", f, cwd=clone)
        else:
            (clone / f).unlink(missing_ok=True)

    g = (grader or TestGrader()).grade(clone, task, config, bench.home)

    arm = arm_name(agent)
    attempt = Attempt(
        sha=task.sha, arm=arm, cond=cond,
        resolved=g.resolved, f2p_pass=g.f2p_pass, f2p_total=g.f2p_total,
        regressions=g.regressions, regression_ids=g.regression_ids,
        suite_crashed=g.suite_crashed, edited_files=edited, touched_tests=touched_tests,
        hit_gold_file=bool(set(edited) & set(task.gold_src)),
        secs=run.secs, timed_out=run.timed_out, llm_calls=run.llm_calls,
        tokens_sent=run.tokens_sent, tokens_recv=run.tokens_recv, extra=run.extra,
        rep=rep,
        degraded=_degraded(watch, run),
        conditions=watch.conditions if watch else {},
        tier=g.tier, run_id=run_id,
    )
    if attempt.degraded:
        attempt.conditions["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try_no = (_next_degraded_try(bench, arm, cond, task.sha, rep)
              if attempt.degraded else None)
    _artifact(bench, arm, cond, task.sha, rep, "json", try_no).parent.mkdir(
        parents=True, exist_ok=True)
    _artifact(bench, arm, cond, task.sha, rep, "patch", try_no).write_text(patch)
    _artifact(bench, arm, cond, task.sha, rep, "log", try_no).write_text(run.log)
    # The .json is written last: its existence is what marks an attempt done.
    _artifact(bench, arm, cond, task.sha, rep, "json", try_no).write_text(
        json.dumps(asdict(attempt), indent=1))
    _reset(clone, task.base)
    return attempt


def load_attempts(bench: Bench) -> list[Attempt]:
    if not bench.attempts.exists():
        return []
    return [Attempt(**json.loads(p.read_text())) for p in sorted(bench.attempts.glob("*.json"))]


def load_degraded(bench: Bench) -> list[Attempt]:
    d = _degraded_dir(bench)
    return [Attempt(**json.loads(p.read_text())) for p in sorted(d.glob("*.json"))] \
        if d.exists() else []


def run_matrix(bench: Bench, agents: Iterable[Agent], conds: Iterable[str],
               only: set[str] | None = None, timeout: float = DEFAULT_TIMEOUT,
               repeats: int = 1, guard: Guard | None = None,
               deadline: float | None = None, max_degraded: int = 2,
               run_id: str = "",
               progress: Callable[[Attempt], None] = lambda a: None) -> list[Attempt]:
    """Every (repeat, agent, condition, task) without a recorded attempt.

    Repeats are the outer loop: a run stopped part-way still has one full
    pass over every arm, rather than three passes over the first arm. One
    attempt per cell is a noisy measurement — the same model solved and
    missed the same task on consecutive runs — so `repeats` > 1 is what
    turns a resolve count into a stability claim.

    With a guard, each attempt waits for a healthy machine. A degraded
    attempt (unhealthy machine, or the agent crashed) is retried up to
    `max_degraded` times before the cell is left open for the next run. No
    attempt starts after `deadline` (epoch secs).
    """
    config = bench.load_config()
    tasks = [t for t in bench.load_tasks() if not only or t.sha in only]
    agents = list(agents)
    done: list[Attempt] = []
    for rep in range(repeats):
        for agent in agents:
            for cond in conds:
                for task in tasks:
                    if _artifact(bench, arm_name(agent), cond, task.sha, rep, "json").exists():
                        continue
                    for _ in range(max_degraded + 1):
                        if guard and not guard.wait_ready(agent, deadline):
                            return done
                        if deadline is not None and time.time() >= deadline:
                            return done
                        attempt = run_attempt(bench, config, task, agent, cond, timeout,
                                              rep, guard, run_id=run_id)
                        progress(attempt)
                        if not attempt.degraded:
                            done.append(attempt)
                            break
    return done
