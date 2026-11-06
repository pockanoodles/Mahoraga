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

from .verdict import CloudSide, Evidence, cloud_side


def _hardware(benches: list[Bench], run_ids: set[str]) -> str:
    """The machine behind the evidence. A verdict is for one machine: a solve
    rate pooled from the laptop and the Studio describes neither, so attempts
    from two recorded machines are refused rather than labelled."""
    seen = set()
    for b in benches:
        for m in manifest.load_all(b):
            if m["run_id"] in run_ids:
                hw = m.get("machine", {})
                seen.add(f"{hw.get('model', '?')}, {hw.get('memory_gb', '?')} GB")
    if len(seen) > 1:
        raise ValueError(f"the attempts come from {len(seen)} machines ({'; '.join(sorted(seen))}); "
                         "judge each machine's benches separately")
    if "" in run_ids or not seen:
        seen.add("unrecorded machine (attempts predate run manifests)")
    return "; ".join(sorted(seen))


def from_benches(benches: list[Bench], arm: str, cond: str) -> Evidence:
    per_task: dict[str, list[bool]] = defaultdict(list)
    secs: list[float] = []
    tiers: set[str] = set()
    run_ids: set[str] = set()
    repos = set()
    failed = timed_out = 0
    for b in benches:
        live = {t.sha for t in b.load_tasks()}
        for a in load_attempts(b):
            if a.arm != arm or a.cond != cond or a.sha not in live:
                continue
            per_task[f"{b.dir.name}/{a.sha}"].append(a.resolved)
            secs.append(a.secs)
            tiers.add(a.tier)
            run_ids.add(a.run_id)
            repos.add(b.name)
            failed += not a.resolved
            timed_out += a.timed_out and not a.resolved
    if not per_task:
        raise ValueError(f"no attempts for {arm} / {cond} in {[b.name for b in benches]}")
    if len(tiers) > 1:
        raise ValueError(f"{arm} / {cond} mixes grading tiers {sorted(tiers)}")
    return Evidence(
        rate=task_rate(per_task),
        minutes_per_attempt=statistics.median(secs) / 60,
        tier=tiers.pop(),
        label=f"{arm} / {cond}, {len(repos)} repo{'s' if len(repos) != 1 else ''} "
              f"({', '.join(sorted(repos))})",
        hardware=_hardware(benches, run_ids),
        failed=failed,
        timed_out=timed_out,
    )


def cloud_from_benches(benches: list[Bench], arm: str, cloud_arm: str, cond: str) -> CloudSide:
    """`cloud_arm` on the tasks `arm` ran, in the same condition. Its attempts
    may come from another machine: what a cloud task costs doesn't depend on
    the machine that sent it."""
    if cloud_arm == arm:
        raise ValueError(f"{arm} can't price itself: name a cloud arm run on the same tasks")
    local: dict[str, list[bool]] = defaultdict(list)
    cloud: dict[str, list[tuple[bool, float]]] = defaultdict(list)
    for b in benches:
        live = {t.sha for t in b.load_tasks()}
        for a in load_attempts(b):
            if a.cond != cond or a.sha not in live:
                continue
            key = f"{b.dir.name}/{a.sha}"
            if a.arm == arm:
                local[key].append(a.resolved)
            elif a.arm == cloud_arm:
                cloud[key].append((a.resolved, a.cost_usd))
    if not cloud:
        raise ValueError(f"no attempts for {cloud_arm} / {cond} in {[b.name for b in benches]}")
    # The verdict reads local's rate off every task local ran, so the cloud's price has to
    # come from those same tasks. Its attempts on tasks local never ran don't count, priced
    # or not.
    missing = [k for k in sorted(local) if k not in cloud]
    if missing:
        raise ValueError(f"{cloud_arm} ran {len(local) - len(missing)} of {arm}'s {len(local)} "
                         f"tasks (not {missing[0]}), and the verdict needs it on every one")
    for k in sorted(local):
        if any(c is None for _, c in cloud[k]):
            raise ValueError(f"{cloud_arm}'s attempt on {k} records no cost, so it "
                             "can't price the verdict")
    return cloud_side(f"{cloud_arm} / {cond}", [
        (statistics.mean(local[k]), statistics.mean(r for r, _ in cloud[k]),
         statistics.mean(c for _, c in cloud[k]))
        for k in sorted(local)])
