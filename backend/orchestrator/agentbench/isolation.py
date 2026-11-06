"""Prove, before a run, that an agent can't reach the answer.

A canary does what a cheating agent would, from inside a task clone and under
the same sandbox an attempt gets, and every probe has to fail:

  history  the task's own commit, from the clone's git history
  source   the answer file in the source repo's working tree
  bench    the bench's own files (other arms' patches sit beside them)
  log      the source repo's history
  network  a connection out of the machine, to 1.1.1.1:443

It exists because the answer *was* reachable: until 2026-10-13 a clone's base
sat on top of the solution commit, and ornith under opencode ran `git show`
on it in its first attempt. Aider can run shell commands it suggests too
(`--yes-always` approves them), though none of 306 recorded aider attempts did.

The sandbox is macOS's `sandbox-exec` or, on Linux, bubblewrap. Without one
the probes reach what they ask for, and the check says so rather than passing.
The network probe is only a test where the harness itself can reach out: on a
machine with no route out, "blocked" would mean nothing, so it says that.
"""
from __future__ import annotations

import shlex
import socket
import sys
from pathlib import Path
from typing import Callable

from .agents import _launch
from .bank import Bench, SuiteConfig
from .guard import Check
from .mine import leaks
from .runner import confinement


def _probe(cmd: str) -> str:
    return f"if {cmd} >/dev/null 2>&1; then echo READABLE; else echo BLOCKED; fi"


OUTSIDE = ("1.1.1.1", 443)
_DIAL = f"import socket; socket.create_connection({OUTSIDE!r}, 5).close()"


def _can_reach_outside() -> bool:
    try:
        socket.create_connection(OUTSIDE, 5).close()
        return True
    except OSError:
        return False


def _no_sandbox_hint() -> str:
    if sys.platform.startswith("linux"):
        return "install bubblewrap (apt install bubblewrap)"
    return "no sandbox on this platform?"


def check(bench: Bench, config: SuiteConfig,
          launch: Callable[..., tuple[str, bool, float]] = _launch,
          reach_outside: Callable[[], bool] = _can_reach_outside) -> list[Check]:
    tasks = [t for t in bench.load_tasks() if bench.clone(t.sha).exists()]
    if not tasks:
        return [Check("isolation", False, "no task clone to test from: run `mahoraga audit mine`")]
    task = tasks[0]
    clone = bench.clone(task.sha)
    out = []
    found = leaks(clone, task.sha)
    out.append(Check("isolation: history", not found,
                     "; ".join(found) + " (run `mahoraga audit reseal`)" if found
                     else f"{task.sha[:10]} is not in its clone"))

    # Probe files that exist, so "blocked" can't mean "not there".
    answer = next((bench.repo / f for f in task.gold_src if (bench.repo / f).is_file()),
                  next(p for p in sorted(bench.repo.iterdir()) if p.is_file()))
    probes = {
        "source": (_probe(f"cat {shlex.quote(str(answer))}"), f"{answer.name} in the source repo"),
        "bench": (_probe(f"cat {shlex.quote(str(bench.tasks_path))}"), "the bench's files"),
        "log": (_probe(f"git -C {shlex.quote(str(bench.repo))} log -1"), "the source repo's history"),
    }
    testable = reach_outside()
    if testable:
        dial = f"{shlex.quote(sys.executable)} -I -c {shlex.quote(_DIAL)}"
        probes["network"] = (_probe(dial), f"a connection to {OUTSIDE[0]}:{OUTSIDE[1]}")
    hidden, readable = confinement(bench, config)
    for name, (script, what) in probes.items():
        result, _, _ = launch(["sh", "-c", script], clone, {"PATH": "/usr/bin:/bin"}, 30,
                              hidden=hidden, readable=readable)
        blocked = result.strip().endswith("BLOCKED")
        out.append(Check(f"isolation: {name}", blocked,
                         f"{what}: blocked" if blocked else
                         f"{what} is reachable from an attempt ({_no_sandbox_hint()})"))
    if not testable:
        out.append(Check("isolation: network", True,
                         "not tested: the harness itself can't reach outside"))
    return out


def problems(bench: Bench, config: SuiteConfig) -> list[str]:
    """The failing checks, as one line each; empty = isolated."""
    return [f"{c.name}: {c.detail}" for c in check(bench, config) if not c.ok]
