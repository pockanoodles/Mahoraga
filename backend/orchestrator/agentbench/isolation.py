"""Prove, before a run, that an agent can't reach the answer.

A canary does what a cheating agent would, from inside a task clone and under
the same sandbox an attempt gets, and every probe has to fail:

  history  the task's own commit, from the clone's git history
  source   the answer file in the source repo's working tree
  bench    the bench's own files (other arms' patches sit beside them)
  log      the source repo's history

It exists because the answer *was* reachable: until 2026-10-13 a clone's base
sat on top of the solution commit, and ornith under opencode ran `git show`
on it in its first attempt. Aider can run shell commands it suggests too
(`--yes-always` approves them), though none of 306 recorded aider attempts did.

The sandbox is macOS's `sandbox-exec`. Elsewhere the three file probes read
what they ask for, and the check says so rather than passing.
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Callable

from .agents import _launch
from .bank import Bench, SuiteConfig
from .guard import Check
from .mine import leaks
from .runner import confinement


def _probe(cmd: str) -> str:
    return f"if {cmd} >/dev/null 2>&1; then echo READABLE; else echo BLOCKED; fi"


def check(bench: Bench, config: SuiteConfig,
          launch: Callable[..., tuple[str, bool, float]] = _launch) -> list[Check]:
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
    hidden, readable = confinement(bench, config)
    for name, (script, what) in probes.items():
        result, _, _ = launch(["sh", "-c", script], clone, {"PATH": "/usr/bin:/bin"}, 30,
                              hidden=hidden, readable=readable)
        blocked = result.strip().endswith("BLOCKED")
        out.append(Check(f"isolation: {name}", blocked,
                         f"{what}: blocked" if blocked else
                         f"{what} is readable from an attempt (no sandbox on this platform?)"))
    return out


def problems(bench: Bench, config: SuiteConfig) -> list[str]:
    """The failing checks, as one line each; empty = isolated."""
    return [f"{c.name}: {c.detail}" for c in check(bench, config) if not c.ok]
