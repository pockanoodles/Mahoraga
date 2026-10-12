"""The audit over HTTP: what the UI shows, and what `mahoraga audit` prints, as JSON.

Read-only over the bench store (agentbench/bank.py). POST /api/audit/verdict
computes and writes nothing. Every number comes from the engine's own code:
agentbench.report and .stats for the per-arm tables and their precision plan,
audit.evidence to pool attempts into one rate, audit.verdict for the call and
its break-even, audit.pricing for plans. This layer finds the right benches and
shapes the answer; it does no arithmetic of its own. The shapes are the
contract with the UI (audit_schemas.py).

A bench is a directory under the bench root with a config.json or a tasks.jsonl,
and its id is the directory's name. Ids are looked up in the listing, never
joined into a path, so a request can't reach outside the root.

Handlers are plain `def`: they read files and probe Ollama, so FastAPI runs them
in its thread pool instead of on the event loop.
"""
from __future__ import annotations

import json
import platform
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException

from ..agentbench import manifest, ollama
from ..agentbench.agents import AGENTS
from ..agentbench.bank import Bench, Task, bench_root, find_benches
from ..agentbench.report import ArmSummary, degraded_note, outcome, precision_plan, summarise
from ..agentbench.runner import Attempt, load_attempts, load_degraded
from ..audit import pricing
from ..audit.evidence import from_benches
from ..audit.verdict import Assumptions, Billing, breakeven_rate, decide, render
from ..version import package_version
from . import audit_schemas as schemas
from .ui import flag, headless, ui_dir

API_VERSION = 1
# Seconds. The engine and arms pages must not hang on a stopped or wedged Ollama.
PROBE_TIMEOUT = 1.5

router = APIRouter(tags=["audit"])


# ── Engine ────────────────────────────────────────────────────────────────────


@cache
def _platform() -> str:
    # The first call runs `uname -p` once on macOS; cached, so the endpoint stays cheap.
    return platform.platform()


def _ollama() -> dict[str, Any]:
    try:
        up, version = True, ollama.version(timeout=PROBE_TIMEOUT)
    except Exception:  # noqa: BLE001 — stopped, slow, or not Ollama: all "not reachable"
        up, version = False, None
    return {"url": ollama.BASE_URL, "up": up, "version": version}


@router.get("/api/engine", response_model=schemas.Engine)
def engine() -> dict[str, Any]:
    """What this engine is and where it runs. Cheap: no machine probes, and one
    short call to Ollama."""
    return {
        "name": "mahoraga", "version": package_version(), "api": API_VERSION,
        "headless": headless(), "ui": not headless() and ui_dir() is not None,
        "demo": flag("MAHORAGA_DEMO"),
        "bench_root": str(bench_root().absolute()),
        "data_dir": str(Path.home() / ".mahoraga-v2"),
        "python": platform.python_version(), "platform": _platform(),
        "ollama": _ollama(),
    }


# ── Reading a bench ───────────────────────────────────────────────────────────


def _seconds(stamp: Any) -> float | None:
    """A manifest timestamp (strftime's %Y-%m-%dT%H:%M:%S%z) as epoch seconds."""
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return None


def _iso(stamp: str | None) -> str | None:
    """A manifest timestamp as ISO 8601 with a colon in the offset: %z writes
    -0400, which not every browser parses."""
    if stamp is None:
        return None
    try:
        return datetime.fromisoformat(stamp).isoformat(timespec="seconds")
    except ValueError:
        return stamp


def _probe(value: Any) -> str | None:
    """A manifest probe's value; None where the probe failed (recorded as 'error: ...')."""
    return value if isinstance(value, str) and not value.startswith("error:") else None


def _manifests(bench: Bench) -> list[dict[str, Any]]:
    """The bench's run manifests, newest first. A file that doesn't say which
    run it is and when it began is not one the engine wrote, and is skipped."""
    found = [m for m in manifest.load_all(bench) if isinstance(m, dict)
             and isinstance(m.get("run_id"), str) and isinstance(m.get("started_at"), str)]
    return sorted(found, key=lambda m: (_seconds(m["started_at"]) or 0.0, m["run_id"]), reverse=True)


def _mtimes(folder: Path) -> Iterator[float]:
    for f in folder.glob("*.json"):
        try:
            yield f.stat().st_mtime
        except OSError:  # removed while we looked
            continue


def _last_activity(bench: Bench) -> float | None:
    """When a run last wrote to this bench: its newest attempt (counted or
    degraded) or manifest file. None if nothing has run. Mining is not activity."""
    folders = (bench.attempts, bench.attempts / "degraded", bench.dir / "runs")
    return max((t for f in folders for t in _mtimes(f)), default=None)


def _dropped(bench: Bench) -> list[dict[str, str]]:
    """dropped.json is {sha: reason}. Best effort: a missing, half-written or
    hand-edited file must not take the bench's page down."""
    try:
        data = json.loads(bench.dropped_path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return [{"sha": str(sha), "reason": str(reason)} for sha, reason in data.items()]


def _config(bench: Bench) -> dict[str, Any] | None:
    try:
        return asdict(bench.load_config())
    except (OSError, ValueError, TypeError):  # none yet, or not one we can read
        return None


@dataclass
class _Loaded:
    bench: Bench
    tasks: list[Task]
    attempts: list[Attempt]  # counted ones on tasks still in the bench: what summarise() counts
    degraded: list[Attempt]
    manifests: list[dict[str, Any]]  # newest first


def _load(bench: Bench) -> _Loaded:
    tasks = bench.load_tasks()
    live = {t.sha for t in tasks}
    return _Loaded(bench, tasks, [a for a in load_attempts(bench) if a.sha in live],
                   load_degraded(bench), _manifests(bench))


def _repo(manifests: list[dict[str, Any]]) -> str | None:
    """Where the repo was when it was last run: the newest manifest that says."""
    return next((m["repo"] for m in manifests if isinstance(m.get("repo"), str) and m["repo"]), None)


def _bench(bench_id: str) -> Bench:
    for b in find_benches():
        if b.dir.name == bench_id:
            return b
    raise HTTPException(status_code=404, detail=f"unknown bench {bench_id!r}")


def _run(m: dict[str, Any], bench: Bench) -> dict[str, Any]:
    machine, harness, models = (m.get(k) or {} for k in ("machine", "harness", "models"))
    memory = machine.get("memory_gb")
    return {
        "run_id": m["run_id"], "bench_id": bench.dir.name, "repo_name": bench.name,
        "started_at": _iso(m["started_at"]), "ended_at": _iso(m.get("ended_at")),
        "stopped": m.get("stopped"), "attempts": m.get("attempts"),
        "arms": list(m.get("arms") or []),
        "machine": {"model": _probe(machine.get("model")),
                    "memory_gb": memory if isinstance(memory, int) else None,
                    "os": _probe(machine.get("os"))},
        "harness": {"commit": _probe(harness.get("commit")), "dirty": harness.get("dirty")},
        "ollama": _probe(models.get("ollama")),
    }


def _task(t: Task) -> dict[str, Any]:
    return {"sha": t.sha, "subject": t.subject, "body": t.body, "gold_src": t.gold_src,
            "gold_lines": t.gold_lines, "test_files": t.test_files,
            "f2p": len(t.f2p), "p2p": len(t.p2p)}


def _arm(a: ArmSummary) -> dict[str, Any]:
    plan = precision_plan(a)
    return {
        "arm": a.arm, "cond": a.cond, "n": a.n, "resolved": a.resolved, "tasks": a.tasks,
        "tasks_solved_ever": a.tasks_solved_ever, "tasks_solved_always": a.tasks_solved_always,
        "repeats": a.repeats, "rate": asdict(a.interval), "median_minutes": a.median_minutes,
        "outcomes": a.outcomes, "broke_other_code": a.broke_other_code,
        "touched_tests": a.touched_tests, "multi_file_resolved": a.multi_file_resolved,
        "multi_file_n": a.multi_file_n, "tier": a.tier,
        "plan": {**asdict(plan), "text": plan.fmt()},
    }


def _matrix(tasks: list[Task], attempts: list[Attempt]) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {t.sha: {} for t in tasks}
    for a in sorted(attempts, key=lambda a: (a.arm, a.cond, a.rep)):
        out[a.sha].setdefault(f"{a.arm} / {a.cond}", []).append(outcome(a))
    return out


def _stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds).astimezone().isoformat(timespec="seconds")


# ── Benches ───────────────────────────────────────────────────────────────────


@router.get("/api/audit/benches", response_model=schemas.Benches)
def list_benches() -> dict[str, Any]:
    rows: list[tuple[float | None, dict[str, Any]]] = []
    for bench in find_benches():
        d = _load(bench)
        seen = _last_activity(bench)
        rows.append((seen, {
            "id": bench.dir.name, "name": bench.name, "repo": _repo(d.manifests),
            "tasks": len(d.tasks), "dropped": len(_dropped(bench)),
            "attempts": len(d.attempts), "degraded": len(d.degraded),
            "arms": sorted({a.arm for a in d.attempts}),
            "conds": sorted({a.cond for a in d.attempts}),
            "runs": len(d.manifests),
            "last_activity": None if seen is None else _stamp(seen),
        }))
    # Most recently run first, benches that never ran last. Sorting the seconds,
    # not the strings: two offsets across a clock change don't compare as text.
    rows.sort(key=lambda r: (r[0] is None, -(r[0] or 0.0)))
    return {"benches": [row for _, row in rows]}


@router.get("/api/audit/benches/{bench_id}", response_model=schemas.BenchDetail)
def bench_detail(bench_id: str) -> dict[str, Any]:
    d = _load(_bench(bench_id))
    try:
        arms = summarise(d.tasks, d.attempts)
    except ValueError as e:  # an arm whose attempts were graded by different tiers
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {
        "id": d.bench.dir.name, "name": d.bench.name, "repo": _repo(d.manifests),
        "config": _config(d.bench),
        "tasks": [_task(t) for t in d.tasks],
        "dropped": _dropped(d.bench),
        "arms": [_arm(a) for a in arms],
        "matrix": _matrix(d.tasks, d.attempts),
        "degraded_note": degraded_note(d.degraded),
        "runs": [_run(m, d.bench) for m in d.manifests],
    }


# ── Runs ──────────────────────────────────────────────────────────────────────


def _all_runs() -> list[tuple[dict[str, Any], Bench]]:
    """Every manifest in every bench, newest first."""
    runs = [(m, b) for b in find_benches() for m in _manifests(b)]
    return sorted(runs, key=lambda r: (_seconds(r[0]["started_at"]) or 0.0, r[0]["run_id"]),
                  reverse=True)


@router.get("/api/audit/runs", response_model=schemas.Runs)
def list_runs() -> dict[str, Any]:
    return {"runs": [_run(m, b) for m, b in _all_runs()]}


@router.get("/api/audit/runs/{run_id}", response_model=schemas.RunDetail)
def run_detail(run_id: str) -> dict[str, Any]:
    found = next(((m, b) for m, b in _all_runs() if m["run_id"] == run_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    m, bench = found
    subjects = {t.sha: t.subject for t in bench.load_tasks()}
    attempts = [{**asdict(a), "outcome": outcome(a), "subject": subjects.get(a.sha, "")}
                for a in load_attempts(bench) if a.run_id == run_id]
    return {"run": _run(m, bench), "manifest": m, "attempts": attempts}


# ── Arms and pricing ──────────────────────────────────────────────────────────


def _agent_and_model(arm: str) -> tuple[str, str]:
    """`<agent>:<model>[@ctx=N]`, as agents.parse_arm reads it, without building
    the agent: an arm recorded in a bench is data, and may name one that isn't installed."""
    agent, _, model = arm.partition("@")[0].partition(":")
    return agent, model


def _pooled_rate(benches: list[Bench], arm: str, cond: str) -> dict[str, Any] | None:
    try:
        return asdict(from_benches(benches, arm, cond).rate)
    except ValueError:  # attempts graded by different tiers are not one rate
        return None


@router.get("/api/audit/arms", response_model=schemas.Arms)
def arms() -> dict[str, Any]:
    pooled: dict[str, dict[str, Any]] = {}
    for bench in find_benches():
        live = {t.sha for t in bench.load_tasks()}
        for a in load_attempts(bench):
            if a.sha in live:
                row = pooled.setdefault(a.arm, {"benches": {}, "attempts": 0, "conds": set()})
                row["benches"][bench.dir.name] = bench
                row["attempts"] += 1
                row["conds"].add(a.cond)
    rows = []
    for arm, row in sorted(pooled.items()):
        agent, model = _agent_and_model(arm)
        conds = sorted(row["conds"])
        rows.append({
            "arm": arm, "agent": agent, "model": model, "benches": len(row["benches"]),
            "attempts": row["attempts"], "conds": conds,
            "rate": (_pooled_rate(list(row["benches"].values()), arm, conds[0])
                     if len(conds) == 1 else None),
        })
    try:
        models = [{"name": name, "digest": digest} for name, digest in
                  sorted(ollama.pulled_models(timeout=PROBE_TIMEOUT).items())]
    except Exception:  # noqa: BLE001 — stopped, slow, or not Ollama: no models to list
        models = []
    return {
        "arms": rows, "models": models,
        "agents": [{"name": name, "on_path": shutil.which(name) is not None}
                   for name in sorted(AGENTS)],
    }


@router.get("/api/audit/pricing", response_model=schemas.Pricing)
def prices() -> dict[str, Any]:
    return {"plans": [asdict(p) for p in pricing.load().values()]}


# ── The verdict ───────────────────────────────────────────────────────────────


def _billing(b: schemas.ApiBilling | schemas.SubscriptionBilling) -> Billing:
    if isinstance(b, schemas.ApiBilling):
        return Billing.api(b.monthly_usd)
    try:
        plan = pricing.plan(b.plan, b.plan_price)
        return Billing.subscription(plan, pricing.lower_tier(plan))
    except KeyError as e:
        raise HTTPException(status_code=400, detail=e.args[0]) from None
    except pricing.UnverifiedPrice as e:
        # Worded for the CLI's flag; over HTTP the same thing is a field.
        raise HTTPException(status_code=400,
                            detail=str(e).replace("--plan-price", "billing.plan_price")) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


def _pick(ids: list[str] | None) -> list[Bench]:
    benches = find_benches()
    if ids is None:
        return benches
    by_id = {b.dir.name: b for b in benches}
    for bench_id in ids:
        if bench_id not in by_id:
            raise HTTPException(status_code=404, detail=f"unknown bench {bench_id!r}")
    return [by_id[i] for i in dict.fromkeys(ids)]


@router.post("/api/audit/verdict", response_model=schemas.Verdict)
def verdict(req: schemas.VerdictRequest) -> dict[str, Any]:
    """Stay, split or switch, for the evidence pooled from the chosen benches.
    Computes only: nothing is written."""
    billing = _billing(req.billing)
    assumptions = Assumptions(**req.assumptions.model_dump(exclude_none=True))
    benches = _pick(req.benches)
    try:
        ev = from_benches(benches, req.arm, req.cond)
        v = decide(ev, assumptions, billing)
    except ValueError as e:  # no attempts for this arm and condition, mixed tiers, or evidence v1 won't decide on
        raise HTTPException(status_code=409, detail=str(e)) from None
    cloud_per_task = billing.monthly_usd / assumptions.tasks_per_month
    return {
        "call": v.call, "confident": v.confident, "reason": v.reason,
        "evidence": asdict(v.evidence),
        "assumptions": {**asdict(v.assumptions), "lines": v.assumptions.lines()},
        "billing": asdict(v.billing),
        "monthly_usd": (None if v.monthly_usd is None
                        else dict(zip(("lo", "point", "hi"), v.monthly_usd))),
        "breakeven": (breakeven_rate(ev, assumptions, cloud_per_task)
                      if billing.kind == "api" else None),
        "sensitivity": v.sensitivity,
        "rendered": render(v),
    }
