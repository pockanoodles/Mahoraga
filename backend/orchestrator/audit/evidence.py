"""Measured evidence for the verdict, pooled from one or more benches.

Tasks from different repos pool into one rate: each (repo, task) is one unit,
whatever its repeat count (stats.py). One arm and condition at a time, named by
the caller. Choosing the best-looking arm after the fact and reporting its rate
would overstate it, because the winner of several noisy measurements is
biased upward.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

from backend.orchestrator.agentbench import manifest
from backend.orchestrator.agentbench.bank import Bench
from backend.orchestrator.agentbench.runner import load_attempts
from backend.orchestrator.agentbench.stats import task_rate

from .verdict import Evidence


def _hardware(benches: list[Bench], run_ids: set[str]) -> str:
    seen = set()
    for b in benches:
        for m in manifest.load_all(b):
            if m["run_id"] in run_ids:
                hw = m.get("machine", {})
                seen.add(f"{hw.get('model', '?')}, {hw.get('memory_gb', '?')} GB")
    if "" in run_ids or not seen:
        seen.add("unrecorded machine (attempts predate run manifests)")
    return "; ".join(sorted(seen))


def from_benches(benches: list[Bench], arm: str, cond: str) -> Evidence:
    per_task: dict[str, list[bool]] = defaultdict(list)
    secs: list[float] = []
    tiers: set[str] = set()
    run_ids: set[str] = set()
    repos = set()
    for b in benches:
        live = {t.sha for t in b.load_tasks()}
        for a in load_attempts(b):
            if a.arm != arm or a.cond != cond or a.sha not in live:
                continue
            per_task[f"{b.dir.name}/{a.sha}"].append(a.resolved)
            secs.append(a.secs)
            tiers.add(a.tier)
            run_ids.add(a.run_id)
            repos.add(b.repo.name)
    if not per_task:
        raise ValueError(f"no attempts for {arm} / {cond} in {[b.repo.name for b in benches]}")
    if len(tiers) > 1:
        raise ValueError(f"{arm} / {cond} mixes grading tiers {sorted(tiers)}")
    return Evidence(
        rate=task_rate(per_task),
        minutes_per_attempt=statistics.median(secs) / 60,
        tier=tiers.pop(),
        label=f"{arm} / {cond}, {len(repos)} repo{'s' if len(repos) != 1 else ''} "
              f"({', '.join(sorted(repos))})",
        hardware=_hardware(benches, run_ids),
    )
