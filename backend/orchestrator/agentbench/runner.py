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
fail (with a guard), or the agent itself crashed or couldn't read the model's
reply — is filed under `attempts/degraded/` instead, so it is kept but not
counted, and the cell stays open to be retried.
"""
from __future__ import annotations

import json
import re
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

from .agents import DEFAULT_NUM_CTX, Agent, AgentRun
from .bank import Bench, SuiteConfig, Task, from_record
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
    # Model requests that failed (cut by a timeout, an Ollama error). None on
    # attempts recorded before this was kept: their log still says.
    request_errors: int | None = None
    # A cloud arm's cost at its vendor's API prices; None for a local arm.
    cost_usd: float | None = None
    # A try the account's usage limit ended: when the limit resets (epoch
    # seconds; 0 = the agent didn't say) and which window ran out.
    limited_until: float | None = None
    limit_window: str = ""

    @property
    def no_edit(self) -> bool:
        return not [f for f in self.edited_files if f not in self.touched_tests]


# Lines that say who wrote or checked a change rather than what it is: git's
# attribution trailers, and a tool's own credit, which is a link and nothing
# else ("Generated with [Claude Code](https://claude.com/claude-code)"). A
# "Generated with protoc 28.2; use --python_out=src" line is a requirement.
_WHO = re.compile(r"^(?:co-authored-by|signed-off-by|reviewed-by|acked-by|tested-by|"
                  r"reported-by|suggested-by|helped-by|cc):\s", re.IGNORECASE)
_CREDIT = re.compile(r"^\W*generated with \[[^\]]+\]\([^)\s]+\)\s*$", re.IGNORECASE)


def without_trailers(body: str) -> str:
    """A commit body without the lines at its end that say who wrote the change.
    Only trailing paragraphs are read, a trailer's folded continuation goes with
    it, and every other line stays, so a "Fixes: #12" or a closing "Why:" line is
    still part of the task."""
    paragraphs = [p for p in re.split(r"\r?\n[ \t]*\r?\n", body.strip()) if p.strip()]
    while paragraphs:
        lines = paragraphs[-1].splitlines()
        kept, dropping = [], False
        for line in lines:
            if _WHO.match(line) or _CREDIT.match(line):
                dropping = True
            elif not (dropping and line[:1].isspace()):
                dropping = False
                kept.append(line)
        if len(kept) == len(lines):
            break  # nothing here says who, so nothing before it is a trailer block
        if kept:
            paragraphs[-1] = "\n".join(kept)
            break
        paragraphs.pop()
    return "\n\n".join(paragraphs)


def prompt_for(task: Task) -> str:
    # Every arm reads the same prompt, so its trailers leave for every model at once.
    parts = ["Make the following change to this repository.", task.subject,
             without_trailers(task.body),
             f"Tests covering this change already exist in: {', '.join(task.test_files)}. "
             "Do not modify any test files; change the source code so those tests pass "
             "without breaking anything else."]
    return "\n\n".join(p for p in parts if p)


def arm_name(agent: Agent) -> str:
    """The arm as `parse_arm` reads it back. A non-default context is named, so
    attempts at two sizes never share a cell; the default isn't, so attempts
    recorded before arms carried a context keep their names."""
    ctx = getattr(agent, "num_ctx", DEFAULT_NUM_CTX)
    return f"{agent.name}:{agent.model}" + (f"@ctx={ctx}" if ctx != DEFAULT_NUM_CTX else "")


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


def attempt_log(bench: Bench, a: Attempt) -> Path:
    """The agent's own output for a counted attempt."""
    return _artifact(bench, a.arm, a.cond, a.sha, a.rep, "log")


def attempt_patch(bench: Bench, a: Attempt) -> Path:
    """The diff a counted attempt left in its clone, as the grader saw it."""
    return _artifact(bench, a.arm, a.cond, a.sha, a.rep, "patch")


def _next_degraded_try(bench: Bench, arm: str, cond: str, sha: str, rep: int) -> int:
    stem = _artifact(bench, arm, cond, sha, rep, "json", 0).name[:-len("__d0.json")]
    d = _degraded_dir(bench)
    return len(list(d.glob(f"{stem}__d*.json"))) if d.exists() else 0


def _reset(clone: Path, base: str) -> None:
    git("reset", "-q", "--hard", base, cwd=clone)
    git("clean", "-qfdx", "-e", ".aider.tags.cache.*", cwd=clone)


HARNESS_CRASH = "agent crashed"
HARNESS_UNREADABLE = "agent couldn't read the reply"
USAGE_LIMIT = "usage limit"

# How long a run waits for a cloud account's usage limit to reset: a
# five-hour window and some slack. A weekly limit ends the run instead.
MAX_LIMIT_WAIT = 6 * 3600
LIMIT_SLACK = 60  # the reset is the vendor's clock, not this machine's
MAX_LIMITED_TRIES = 3  # a limit back after every reset ends the run
_sleep = time.sleep


def is_harness_failure(reason: str) -> bool:
    return reason.startswith((HARNESS_CRASH, HARNESS_UNREADABLE))


def describe_limit(window: str, until: float) -> str:
    who = f"the {window} window" if window else "the account"
    if not until:
        return f"{who} is out of usage, and the agent didn't say until when"
    return f"{who} is out of usage until {time.strftime('%a %H:%M', time.localtime(until))}"


class UsageLimited(Exception):
    """A cloud account out of usage for longer than the run can wait. `done`
    holds the attempts the run counted before it, so they're still reported."""

    def __init__(self, arm: str, window: str, until: float, done: list["Attempt"]) -> None:
        super().__init__(f"{arm}: {describe_limit(window, until)}")
        self.arm, self.window, self.until, self.done = arm, window, until, done


def _harness_failures(run: AgentRun, edited: bool) -> list[str]:
    """Ways the agent itself failed to carry the model's work, whatever the
    machine. A reply the agent couldn't read only voids an attempt that edited
    nothing: one that edited something measured the model anyway."""
    out = []
    if run.crashed:
        out.append(f"{HARNESS_CRASH}: {run.crashed}")
    if run.unreadable and not edited:
        out.append(f"{HARNESS_UNREADABLE}: {run.unreadable}")
    return out


def _degraded(watch: Watch | None, run: AgentRun, edited: bool) -> list[str]:
    """Why this attempt did not measure the agent; empty = it counts. A slow
    machine counts against an attempt only through what slowness does: a
    timeout, or requests that failed."""
    out = list(watch.degraded) if watch else []
    if watch and watch.slow and (run.timed_out or run.request_errors):
        how = "timed out" if run.timed_out else f"had {run.request_errors} failed requests"
        out.append(f"{watch.slow}, and the attempt {how}")
    if run.limited_until is not None:
        # The account ran out, not the agent: no harness failure to count.
        return out + [f"{USAGE_LIMIT}: {describe_limit(run.limit_window, run.limited_until)}"]
    return out + _harness_failures(run, edited)


def confinement(bench: Bench, config: SuiteConfig) -> tuple[list[Path], list[Path]]:
    """(hidden, readable) for an agent. Hidden: the source repo, whose working
    tree and history hold the answer to every task mined from it, and the
    bench root, whose other clones sit at later commits and whose attempts
    hold other arms' patches. Readable again: the test interpreter's
    environment, wherever it lives. The agent's own clone and HOME are
    writable, which keeps them readable too.

    Another checkout of the same repo elsewhere on the machine is not hidden;
    an agent would have to go looking for it."""
    hidden = [bench.repo.resolve(), bench.dir.parent.resolve()]
    # Named even where nothing hides it: an agent that hides more (the cloud
    # arm hides the whole home) still has to run the tests.
    return hidden, [suite_env(config.python)]


def suite_env(python: str) -> Path:
    """The environment a test interpreter runs in: its venv, the dir holding
    pyvenv.cfg, else the install it resolves to. Never just two levels up,
    which for ~/bin/python is the whole home."""
    p = Path(python)
    if (p.parent.parent / "pyvenv.cfg").is_file():
        return p.parent.parent.resolve()
    real = p.resolve()
    return real.parent.parent if real.parent.name == "bin" else real.parent


def suite_python(python: str) -> str:
    """The test interpreter as an agent can reach it. A venv's own python stays
    as named: the whole venv is put back, and resolving it would lose the
    venv. Any other link (~/bin/python -> /usr/bin/python3) is run by its real
    path, which survives a sandbox that hides the dir the link sits in."""
    p = Path(python)
    return python if (p.parent.parent / "pyvenv.cfg").is_file() else str(p.resolve())


def run_attempt(bench: Bench, config: SuiteConfig, task: Task, agent: Agent,
                cond: str, timeout: float = DEFAULT_TIMEOUT, rep: int = 0,
                guard: Guard | None = None, grader: Grader | None = None,
                run_id: str = "") -> Attempt:
    if cond not in CONDITIONS:
        raise ValueError(f"condition must be one of {CONDITIONS}")
    clone = bench.clone(task.sha)
    _reset(clone, task.base)
    test_cmd = ([suite_python(config.python), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                 *config.pytest_args, *task.test_files] if cond == "feedback" else None)
    watching = guard.watch(agent) if guard else nullcontext(None)
    with watching as watch:
        if watch and watch.degraded:  # the machine was given back before the agent began
            run = AgentRun(log=f"not started: {watch.degraded[0]}", secs=0, timed_out=False)
        else:
            hidden, readable = confinement(bench, config)
            run = agent.attempt(clone, prompt_for(task), test_cmd=test_cmd, timeout=timeout,
                                hidden=hidden, readable=readable)

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
        degraded=_degraded(watch, run, edited=bool(set(edited) - set(touched_tests))),
        conditions=watch.conditions if watch else {},
        tier=g.tier, run_id=run_id, request_errors=run.request_errors,
        cost_usd=run.cost_usd, limited_until=run.limited_until, limit_window=run.limit_window,
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


def open_cells(bench: Bench, agents: Iterable[Agent], conds: Iterable[str],
               only: set[str] | None = None, repeats: int = 1) -> tuple[int, int]:
    """(cells without a counted attempt, all cells) for this matrix."""
    tasks = [t for t in bench.load_tasks() if not only or t.sha in only]
    cells = [(arm_name(a), c, t.sha, r) for r in range(repeats) for a in agents
             for c in conds for t in tasks]
    left = sum(not _artifact(bench, arm, c, sha, r, "json").exists() for arm, c, sha, r in cells)
    return left, len(cells)


def load_attempts(bench: Bench) -> list[Attempt]:
    if not bench.attempts.exists():
        return []
    return [from_record(Attempt, json.loads(p.read_text()))
            for p in sorted(bench.attempts.glob("*.json"))]


def load_degraded(bench: Bench) -> list[Attempt]:
    d = _degraded_dir(bench)
    return [from_record(Attempt, json.loads(p.read_text())) for p in sorted(d.glob("*.json"))] \
        if d.exists() else []


def _wait_out(a: Attempt, limited: int, deadline: float | None,
              note: Callable[[str], None], done: list[Attempt]) -> None:
    """Sleep until a usage limit resets, or raise `UsageLimited` when the run
    can't: no reset given, past the deadline or `MAX_LIMIT_WAIT` away, or the
    same cell limited `MAX_LIMITED_TRIES` times."""
    until, now = a.limited_until or 0.0, time.time()
    resume = max(until, now) + LIMIT_SLACK
    if (not until or limited >= MAX_LIMITED_TRIES or resume - now > MAX_LIMIT_WAIT
            or (deadline is not None and resume >= deadline)):
        raise UsageLimited(a.arm, a.limit_window, until, done)
    note(f"{a.arm}: {describe_limit(a.limit_window, until)}; waiting for it")
    _sleep(resume - now)


def run_matrix(bench: Bench, agents: Iterable[Agent], conds: Iterable[str],
               only: set[str] | None = None, timeout: float = DEFAULT_TIMEOUT,
               repeats: int = 1, guard: Guard | None = None,
               deadline: float | None = None, max_degraded: int = 2,
               run_id: str = "", max_harness_failures: int = 3,
               progress: Callable[[Attempt], None] = lambda a: None,
               dropped: Callable[[str, str], None] = lambda arm, why: None,
               note: Callable[[str], None] = lambda msg: None,
               ) -> list[Attempt]:
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

    An arm whose agent fails `max_harness_failures` cells in a row — every try
    crashed, or edited nothing because the agent couldn't read the model — is
    dropped from the rest of the run and reported through `dropped`. Left in,
    it fills a night with attempts that can't count: ornith under aider, and
    aider on the Studio's broken scipy, would both have.

    A try a cloud account's usage limit ended is neither a retry spent nor a
    harness failure: the run waits for the reset (saying so through `note`)
    and tries the cell again. When it can't wait, because the reset is
    unknown, past the deadline or over `MAX_LIMIT_WAIT` away, it raises
    `UsageLimited` with the attempts counted so far. Rerun after the reset:
    filled cells are skipped.
    """
    config = bench.load_config()
    tasks = [t for t in bench.load_tasks() if not only or t.sha in only]
    agents = list(agents)
    done: list[Attempt] = []
    streak: dict[str, int] = {}
    for rep in range(repeats):
        for agent in agents:
            arm = arm_name(agent)
            for cond in conds:
                for task in tasks:
                    if streak.get(arm, 0) >= max_harness_failures:
                        break
                    if _artifact(bench, arm, cond, task.sha, rep, "json").exists():
                        continue
                    attempt = None
                    tries = limited = 0
                    while tries <= max_degraded:
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
                        if attempt.limited_until is None:
                            tries += 1
                        else:
                            limited += 1
                            _wait_out(attempt, limited, deadline, note, done)
                    failed = [r for r in attempt.degraded if is_harness_failure(r)]
                    streak[arm] = streak.get(arm, 0) + 1 if failed else 0
                    if streak[arm] == max_harness_failures:
                        dropped(arm, f"{max_harness_failures} tasks in a row where the "
                                     f"agent failed, not the model (last: {failed[0]})")
    return done
