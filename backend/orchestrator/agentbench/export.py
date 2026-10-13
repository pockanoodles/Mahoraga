"""Counted attempts, one line each, for publishing the numbers made from them.

A published rate has to be recomputable by someone who doesn't have the bench.
The bench can't be shared: it holds every task clone and agent log, gigabytes
of it, and for a private repo its code. An export keeps what a rate is made
of — which task, which arm, solved or not, how long it took — and what each
attempt was measured with, so `orch bench verify` can recompute the report's
rates and verdicts from it (experiments/claims.json).

Only counted attempts are exported, on tasks still in the bench. Degraded
attempts didn't measure the agent, and no published count includes them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from . import manifest
from .bank import Bench
from .runner import load_attempts


def machine_label(m: dict) -> str:
    """The machine a run was on, as evidence.py names it."""
    hw = m.get("machine", {})
    return f"{hw.get('model', '?')}, {hw.get('memory_gb', '?')} GB"


def rows(bench: Bench, arms: Iterable[str] = (), conds: Iterable[str] = ()) -> list[dict]:
    arms, conds = set(arms), set(conds)
    tasks = {t.sha: t for t in bench.load_tasks()}
    runs = {m["run_id"]: m for m in manifest.load_all(bench)}
    out = []
    for a in load_attempts(bench):
        if a.sha not in tasks or (arms and a.arm not in arms) or (conds and a.cond not in conds):
            continue
        m = runs.get(a.run_id, {})
        model = a.arm.partition(":")[2].split("@")[0]
        out.append({
            "repo": bench.name, "sha": a.sha, "committed": tasks[a.sha].committed,
            "arm": a.arm, "cond": a.cond, "rep": a.rep,
            "resolved": a.resolved, "secs": a.secs, "tier": a.tier,
            "run_id": a.run_id,
            "machine": machine_label(m) if m else "unrecorded",
            "digest": ((m.get("models", {}).get("digests") or {}).get(model) or "")[:12],
            "harness": (m.get("harness", {}).get("commit") or "")[:9],
        })
    return sorted(out, key=lambda r: (r["arm"], r["cond"], r["sha"], r["rep"]))


def write(path: Path, benches: list[Bench], arms: Iterable[str] = (),
          conds: Iterable[str] = ()) -> int:
    arms, conds = list(arms), list(conds)
    out = [r for b in benches for r in rows(b, arms, conds)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in out))
    return len(out)
