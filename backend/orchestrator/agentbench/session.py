"""One run of the matrix with its manifest: what `audit run` and the queue
worker both do, so a queued run is recorded exactly like one started by hand."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

from . import manifest
from .agents import Agent
from .bank import Bench
from .runner import DEFAULT_TIMEOUT, Attempt, run_matrix

if TYPE_CHECKING:
    from .guard import Guard


def run(bench: Bench, agents: list[Agent], conds: tuple[str, ...] | list[str], *,
        only: set[str] | None = None, timeout: float = DEFAULT_TIMEOUT, repeats: int = 1,
        guard: "Guard | None" = None, deadline: float | None = None,
        settings: dict[str, Any] | None = None,
        progress: Callable[[Attempt], None] = lambda a: None,
        dropped: Callable[[str, str], None] = lambda arm, why: None,
        started: Callable[[str], None] = lambda run_id: None,
        ) -> tuple[str, list[Attempt], str]:
    """(run_id, attempts counted, why it stopped: completed, deadline or
    interrupted). The manifest is written before the first attempt and stamped
    with the stop reason even when the run dies."""
    run_id = manifest.new_run_id()
    manifest.write(bench, manifest.build(
        bench, agents, [t for t in bench.load_tasks() if not only or t.sha in only],
        run_id=run_id, settings=settings or {},
        speed_refs=guard.references() if guard else None,
    ))
    started(run_id)
    done: list[Attempt] = []
    stopped = "interrupted"
    try:
        done = run_matrix(bench, agents, conds, only, timeout, repeats, guard=guard,
                          deadline=deadline, run_id=run_id, progress=progress,
                          dropped=dropped)
        stopped = "deadline" if deadline and time.time() >= deadline else "completed"
    finally:
        manifest.finish(bench, run_id, len(done), stopped)
    return run_id, done, stopped
