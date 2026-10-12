#!/usr/bin/env python3
"""Export the numbers the landing page shows, computed by the engine, to docs/site/site.json.

    python scripts/export_site.py

kaitosoeno.com/mahoraga renders only what is in this file, so no number in it is
typed or rounded here. Each comes out of the engine's own code:

    claims        experiments/claims.json, re-verified against its artifacts first
                  (the check `orch bench verify` runs), so a number that drifted
                  from its data is never exported
    probe         the demo bench (the agent-edit probe) through
                  agentbench.report.summarise and .outcome, with every rate (the
                  replay's running rates too) from agentbench.stats.task_rate
    verdict_demo  audit.evidence.from_benches, audit.verdict.decide and
                  audit.verdict.breakeven_rate

The output is deterministic: sorted keys, no timestamps, so running it twice
gives the same bytes. Nothing in it changes unless the engine or its data does,
apart from source.commit. The private ops repo's figures are never exported.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # the checkout this script sits in, not an installed copy

from backend.orchestrator import demo  # noqa: E402
from backend.orchestrator.agentbench.report import outcome, summarise  # noqa: E402
from backend.orchestrator.agentbench.runner import Attempt, load_attempts  # noqa: E402
from backend.orchestrator.agentbench.stats import task_rate  # noqa: E402
from backend.orchestrator.audit.evidence import from_benches  # noqa: E402
from backend.orchestrator.audit.verdict import (  # noqa: E402
    Assumptions, Billing, breakeven_rate, decide,
)
from backend.orchestrator.routing.benchmark.verify import verify_claims  # noqa: E402

SCHEMA = 1
REPO = "pockanoodles/Mahoraga"
OUT = ROOT / "docs" / "site" / "site.json"
CLAIM_FIELDS = ("id", "description", "artifact", "config", "metrics")

# The arm the landing page follows: the replay and the verdict curve both read it.
ARM, COND = "aider:qwen3.5:latest", "blind"
REPLAY_ORDER = ("The order of experiments/agent_edit/tasks.jsonl, as listed there, with no "
                "sorting by outcome, size or commit date.")

TASKS_PER_MONTH = 200
# Cloud cost per task: log-spaced values, plus the round figures a reader would try.
GRID_LO, GRID_HI, GRID_POINTS = 0.10, 30.0, 24
GRID_ROUND = (0.25, 0.5, 0.75, 1, 2, 5, 10, 20)


class ExportError(Exception):
    """The engine or its data can't back an export."""


def _claims(path: Path, root: Path) -> list[dict]:
    failed = [r.claim_id for r in verify_claims(path, root) if not r.ok]
    if failed:
        raise ExportError(f"claims their artifacts no longer back: {', '.join(failed)} "
                          "(run `orch bench verify`)")
    return [{k: c[k] for k in CLAIM_FIELDS} for c in json.loads(path.read_text())["claims"]]


def _cells(attempts: list[Attempt]) -> dict[tuple[str, str, str], Attempt]:
    cells = {(a.arm, a.cond, a.sha): a for a in attempts}
    if len(cells) != len(attempts):
        raise ExportError("more than one attempt for a task, arm and condition; "
                          "the export reads one")
    return cells


def _replay(tasks: list, cells: dict) -> dict:
    """The probe task by task: the rate, and its interval, after the first n."""
    steps = []
    for n, t in enumerate(tasks, 1):
        first_n = {s.sha: [cells[ARM, COND, s.sha].resolved] for s in tasks[:n]}
        steps.append({"n": n, "sha": t.sha, "outcome": outcome(cells[ARM, COND, t.sha]),
                      "rate": asdict(task_rate(first_n))})
    return {"arm": ARM, "cond": COND, "order": REPLAY_ORDER, "steps": steps}


def _probe(bench, info: dict) -> dict:
    tasks, attempts = bench.load_tasks(), load_attempts(bench)
    cells, arms = _cells(attempts), summarise(tasks, attempts)
    return {
        "repo": bench.repo.name, "dates": info["dates"], "machine": info["machine"],
        "tasks": [{
            "sha": t.sha, "subject": t.subject, "body": t.body,
            "files": len(t.gold_src), "lines": t.gold_lines,
            "outcomes": {f"{s.arm} / {s.cond}": {
                "outcome": outcome(cells[s.arm, s.cond, t.sha]),
                "minutes": round(cells[s.arm, s.cond, t.sha].secs / 60, 1)} for s in arms},
        } for t in tasks],
        "arms": [{
            "arm": s.arm, "cond": s.cond, "resolved": s.resolved, "n": s.n,
            "rate": asdict(s.interval), "median_minutes": s.median_minutes,
            "broke_other_code": s.broke_other_code,
            "multi_file_resolved": s.multi_file_resolved, "multi_file_n": s.multi_file_n,
        } for s in arms],
        "replay": _replay(tasks, cells),
    }


def _grid() -> list[float]:
    step = (GRID_HI / GRID_LO) ** (1 / (GRID_POINTS - 1))
    log_spaced = [round(GRID_LO * step ** i, 2) for i in range(GRID_POINTS)]
    return sorted({*log_spaced, *map(float, GRID_ROUND)})


def _verdict_demo(bench) -> dict:
    ev = from_benches([bench], ARM, COND)
    a = Assumptions(tasks_per_month=TASKS_PER_MONTH)
    curve = []
    for cloud in _grid():
        v = decide(ev, a, Billing.api(round(cloud * TASKS_PER_MONTH, 2)))
        curve.append({
            "cloud_usd_per_task": cloud, "breakeven": breakeven_rate(ev, a, cloud),
            "call": v.call, "confident": v.confident, "reason": v.reason,
            "monthly_usd": (dict(zip(("lo", "point", "hi"), v.monthly_usd))
                            if v.monthly_usd else None),
        })
    return {"evidence": asdict(ev), "assumptions": {**asdict(a), "lines": a.lines()},
            "curve": curve}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout


def _version(root: Path) -> str:
    """The release number from the one file that holds it (pyproject.toml reads
    it dynamically), so the export can't name a version the package doesn't."""
    ns: dict = {}
    exec((root / "backend" / "orchestrator" / "__version__.py").read_text(), ns)
    return ns["__version__"]


def build_site(root: Path = ROOT, commit: str | None = None) -> dict:
    bench = demo.open_bench()
    return {
        "schema": SCHEMA,
        "source": {"repo": REPO, "commit": commit or _git(root, "rev-parse", "--short=7",
                                                          "HEAD").strip(),
                   "version": _version(root)},
        "claims": _claims(root / "experiments" / "claims.json", root),
        "probe": _probe(bench, demo.meta()),
        "verdict_demo": _verdict_demo(bench),
    }


def render(site: dict) -> str:
    return json.dumps(site, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _dirty(root: Path, output: Path) -> bool:
    """Tracked files changed since HEAD, other than the export itself."""
    changed = _git(root, "status", "--porcelain", "--untracked-files=no").splitlines()
    return any(root / line[3:] != output for line in changed)


def main(out: Path = OUT) -> int:
    try:
        text = render(build_site())
    except ExportError as e:
        print(f"export refused: {e}", file=sys.stderr)
        return 1
    if _dirty(ROOT, out):
        print("warning: uncommitted changes to tracked files, so source.commit does not "
              "describe these numbers", file=sys.stderr)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"{out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}: "
          f"{len(text.encode()) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
