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
    verdict_demo  the slice of the claim HEADLINE names, read by audit.claims
                  .select and .evidence (what `orch bench verify` reads for that
                  claim), then audit.verdict.decide, .breakeven_rate and
                  .materiality at each cloud price on a grid, once for each
                  triage time the calculator offers
    settings      the claims SETTINGS names, task by task through the same
                  select, and audit.claims.paired between neighbouring settings
    specimen      experiments/agentbench/specimen-2026-10.json, one attempt kept
                  whole by scripts/specimen.py, checked for agreeing with itself
                  and carried only once a claim's ledger holds the same attempt

The output is deterministic: sorted keys, no timestamps, so running it twice
gives the same bytes. Nothing in it changes unless the engine or its data does,
apart from source.commit. The private ops repo's figures are never exported.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # the checkout this script sits in, not an installed copy

from backend.orchestrator import demo  # noqa: E402
from backend.orchestrator.agentbench.report import outcome, summarise  # noqa: E402
from backend.orchestrator.agentbench.runner import Attempt, load_attempts  # noqa: E402
from backend.orchestrator.agentbench.stats import task_rate  # noqa: E402
from backend.orchestrator.audit import claims as audit_claims  # noqa: E402
from backend.orchestrator.audit.verdict import (  # noqa: E402
    Assumptions, Billing, attempt_cost, breakeven_rate, decide, materiality, triage_cost,
)
from backend.orchestrator.routing.benchmark.verify import load_rows, verify_claims  # noqa: E402

SCHEMA = 1
REPO = "pockanoodles/Mahoraga"
OUT = ROOT / "docs" / "site" / "site.json"
CLAIM_FIELDS = ("id", "description", "artifact", "config", "metrics")

# The arm the replay follows through the probe.
ARM, COND = "aider:qwen3.5:latest", "blind"
REPLAY_ORDER = ("The order of experiments/agent_edit/tasks.jsonl, as listed there, with no "
                "sorting by outcome, size or commit date.")

# The claim the verdict demo follows: the newest pooled measurement the page
# leads with, so the calculator reads the evidence the hero chart draws. When a
# newer measurement takes the lead, this is the line that moves.
HEADLINE = "report_2026_10_studio_qwen35_131k_outside"

# Cloud cost per task: log-spaced values, plus the round figures a reader would try.
GRID_LO, GRID_HI, GRID_POINTS = 0.10, 30.0, 24
GRID_ROUND = (0.25, 0.5, 0.75, 1, 2, 5, 10, 20)
# The calculator's second control: minutes to see a local attempt failed and
# hand the task over. The claim's own figure is one of them.
TRIAGE_MINUTES = (1.0, 2.0, 3.0, 5.0, 10.0)

# The settings exhibit: the laptop's model at Ollama's 32k default and at a
# 131k context, and the 35B model a 64 GB machine holds, on the same commits.
SETTINGS = (("32k", "report_2026_10_studio_qwen35_outside"),
            ("131k", "report_2026_10_studio_qwen35_131k_outside"),
            ("35b", "report_2026_10_studio_qwen36_outside"))
SETTINGS_ORDER = ("By repository, then by commit date, oldest first, with no sorting by "
                  "outcome.")
# Where each outside repository's commits can be read. A repository missing
# here is refused, so a private one can't reach the page by accident.
UPSTREAM = {"attrs": "https://github.com/python-attrs/attrs",
            "click": "https://github.com/pallets/click",
            "more-itertools": "https://github.com/more-itertools/more-itertools"}
SPECIMEN = ROOT / "experiments" / "agentbench" / "specimen-2026-10.json"
PLAIN_RATE = {"arm", "cond", "repos"}  # the select keys of a rate the specimen can sit beside


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


def _headline(path: Path, root: Path) -> tuple[dict, list[dict]]:
    """The claim the verdict demo follows, and the rows of its slice."""
    c = next((c for c in json.loads(path.read_text())["claims"] if c["id"] == HEADLINE), None)
    if c is None or not c.get("verdict"):
        raise ExportError(f"claims.json has no {HEADLINE} with verdict assumptions, and the "
                          "verdict demo follows it")
    return c, audit_claims.select(load_rows(root / c["artifact"]), c["select"])


def _curve(ev, a: Assumptions) -> list[dict]:
    curve = []
    for cloud in _grid():
        billing = Billing.api(round(cloud * a.tasks_per_month, 2))
        v = decide(ev, a, billing)
        curve.append({
            "cloud_usd_per_task": cloud, "breakeven": breakeven_rate(ev, a, cloud),
            "bar": materiality(billing),
            "call": v.call, "confident": v.confident, "reason": v.reason,
            "monthly_usd": (dict(zip(("lo", "point", "hi"), v.monthly_usd))
                            if v.monthly_usd else None),
        })
    return curve


def _verdict_demo(claim: dict, rows: list[dict]) -> dict:
    ev = audit_claims.evidence(rows)
    # The claim's own assumptions, so the curve at the claim's price is its verdict.
    a = Assumptions(**{k: v for k, v in claim["verdict"].items() if k != "api_spend"})
    # The same curve under each other triage time the calculator offers. Only
    # triage changes, so a difference between two curves is that minute's doing.
    others = sorted(set(TRIAGE_MINUTES) - {a.triage_minutes})
    by_triage = []
    for minutes in others:
        at = replace(a, triage_minutes=minutes)
        by_triage.append({"triage_minutes": minutes, "triage_usd": triage_cost(at),
                          "curve": _curve(ev, at)})
    return {"claim": claim["id"], "select": claim["select"], "evidence": asdict(ev),
            "assumptions": {**asdict(a), "lines": a.lines()}, "curve": _curve(ev, a),
            # What one local attempt costs in electricity, and a failed one in
            # the developer's time, at the claim's assumptions.
            "costs": {"attempt_usd": attempt_cost(ev, a), "triage_usd": triage_cost(a)},
            "by_triage": by_triage}


def _settings(manifest: Path, root: Path) -> dict:
    """Each outside commit's outcome under each setting, read through each
    claim's own select, so the lit squares count to the claims' resolved
    figures, and the paired test between neighbouring settings. `only_this`
    is the commits only the first of a pair solved, `only_versus` the second."""
    by_id = {c["id"]: c for c in json.loads(manifest.read_text())["claims"]}
    slices: dict[str, dict[tuple[str, str], dict]] = {}
    arms = []
    for key, cid in SETTINGS:
        c = by_id.get(cid)
        if c is None:
            raise ExportError(f"claims.json has no {cid}, and the settings exhibit reads it")
        missing = [k for k in ("context", "machine") if k not in c.get("config", {})]
        if missing:
            raise ExportError(f"{cid}'s config has no {' or '.join(missing)}, and the settings "
                              "exhibit names both")
        rows = audit_claims.select(load_rows(root / c["artifact"]), c["select"])
        cells = {(r["repo"], r["sha"]): r for r in rows}
        if len(cells) != len(rows):
            raise ExportError(f"{cid} has more than one attempt on a commit, and a square "
                              "shows one")
        ev = audit_claims.evidence(rows)
        slices[key] = cells
        arms.append({"key": key, "claim": cid, "arm": c["select"]["arm"],
                     "context": c["config"]["context"], "machine": c["config"]["machine"],
                     "resolved": sum(r["resolved"] for r in rows), "rate": asdict(ev.rate)})
    keys = [k for k, _ in SETTINGS]
    first = slices[keys[0]]
    if any(set(slices[k]) != set(first) for k in keys):
        raise ExportError("the settings' slices cover different commits, so one square would "
                          "stand for different tasks")
    private = {repo for repo, _ in first} - set(UPSTREAM)
    if private:
        raise ExportError(f"no public upstream for {', '.join(sorted(private))}")
    order = sorted(first, key=lambda t: (t[0], first[t]["committed"], t[1]))
    return {
        "order": SETTINGS_ORDER,
        "arms": arms,
        "tasks": [{"repo": repo, "sha": sha, "committed": first[repo, sha]["committed"][:10],
                   "url": f"{UPSTREAM[repo]}/commit/{sha}",
                   "resolved": {k: slices[k][repo, sha]["resolved"] for k in keys}}
                  for repo, sha in order],
        "pairs": [{"from": a, "to": b, **audit_claims.paired(list(slices[a].values()),
                                                             list(slices[b].values()))}
                  for a, b in zip(keys, keys[1:])],
    }


def _specimen(path: Path, manifest: Path, root: Path) -> dict | None:
    """One attempt kept whole by scripts/specimen.py, carried only beside its
    run's published rate: once a claim's own select holds the same attempt.
    Until then the export leaves it out, whatever its outcome, so a single
    attempt never stands on the page without the rate it belongs to. It is
    refused if that claim's row disagrees with it, or if it disagrees with
    itself: the grader counts an attempt resolved only when every fail-to-pass
    test passes, nothing else breaks and the suite ran."""
    s = json.loads(path.read_text())
    a = s["attempt"]
    graded = (a["f2p_pass"] == a["f2p_total"] == len(s["f2p"]) and a["regressions"] == 0
              and not a["suite_crashed"])
    if a["resolved"] != graded:
        raise ExportError(f"the specimen {s['repo']}/{s['sha']} says resolved={a['resolved']}, "
                          "which its own test counts contradict")
    if UPSTREAM.get(s["repo"]) != s["upstream"]:
        raise ExportError(f"the specimen's repository {s['repo']} has no public upstream here")
    if not s["gold_diff"].strip():
        raise ExportError("the specimen is missing the commit's own diff")
    # An empty patch is the agent changing nothing, an outcome position can pick,
    # and one that can't have resolved a task whose tests fail without the fix.
    if not isinstance(s.get("patch"), str):
        raise ExportError("the specimen has no patch, not even an empty one")
    if not s["patch"].strip() and a["resolved"]:
        raise ExportError(f"the specimen {s['repo']}/{s['sha']} changed nothing and says it resolved")
    same = (s["repo"], s["sha"], a["rep"], a["run_id"])
    for c in json.loads(manifest.read_text())["claims"]:
        sel = c.get("select", {})
        # The rate the card sits beside is its arm's plain rate on its repositories,
        # never a slice of one (a time limit, a date split, another arm's tasks).
        if (sel.get("arm"), sel.get("cond")) != (a["arm"], a["cond"]) or set(sel) - PLAIN_RATE:
            continue
        for r in audit_claims.select(load_rows(root / c["artifact"]), c["select"]):
            if (r["repo"], r["sha"], r.get("rep", 0), r.get("run_id")) == same:
                if bool(r["resolved"]) != bool(a["resolved"]):
                    raise ExportError(f"the specimen {s['repo']}/{s['sha']} says "
                                      f"resolved={a['resolved']}, and {c['id']}'s ledger says "
                                      f"{r['resolved']}")
                return s
    print(f"the specimen {s['repo']}/{s['sha']} waits: no claim's plain rate holds rep "
          f"{a['rep']} of run {a['run_id']} yet", file=sys.stderr)
    return None


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
    manifest = root / "experiments" / "claims.json"
    claims = _claims(manifest, root)  # first, so a drifted headline is refused by name
    return {
        "schema": SCHEMA,
        "source": {"repo": REPO, "commit": commit or _git(root, "rev-parse", "--short=7",
                                                          "HEAD").strip(),
                   "version": _version(root)},
        "claims": claims,
        "probe": _probe(bench, demo.meta()),
        "verdict_demo": _verdict_demo(*_headline(manifest, root)),
        "settings": _settings(manifest, root),
    } | _optional("specimen", _specimen(root / SPECIMEN.relative_to(ROOT), manifest, root))


def _optional(key: str, value: dict | None) -> dict:
    """A block the export leaves out, rather than writing null, until it can stand."""
    return {} if value is None else {key: value}


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
