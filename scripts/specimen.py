#!/usr/bin/env python3
"""Keep one audit attempt whole, so the landing page can show what a task is.

    python scripts/specimen.py ~/.mahoraga-v2/agentbench-131k/more-itertools-440e2efd \\
        --arm aider:qwen3.5:latest@ctx=131072

The ledgers in experiments/agentbench/ keep one line per attempt, which is all
a rate needs. A reader needs one task in full: the commit, the fix the bench
took out, what the agent wrote instead, and what the commit's own tests said.
This writes that for one attempt to experiments/agentbench/specimen-2026-10.json,
read through the engine's own loaders from the bench (its tasks, the attempt's
record and patch, and its run's manifest) and the upstream commit's diff, so
nothing in it is typed.

The attempt is picked by position, never by outcome: the first task in the
bench's own order (as mined) that the arm has a counted attempt on, and that
task's first rep if it has more than one. The agent's
log stays behind, since it holds the machine's paths and the prompts.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # the checkout this script sits in, not an installed copy

from backend.orchestrator.agentbench import manifest  # noqa: E402
from backend.orchestrator.agentbench.bank import Bench  # noqa: E402
from backend.orchestrator.agentbench.runner import attempt_patch, load_attempts  # noqa: E402

OUT = ROOT / "experiments" / "agentbench" / "specimen-2026-10.json"

# What the page shows of the attempt's record. The rest (resource conditions,
# guard notes) describes the machine's evening, not the task.
ATTEMPT_FIELDS = ("arm", "cond", "rep", "resolved", "f2p_pass", "f2p_total", "regressions",
                  "suite_crashed", "edited_files", "hit_gold_file", "secs", "timed_out",
                  "llm_calls", "tokens_sent", "tokens_recv", "request_errors", "tier", "run_id")


class SpecimenError(Exception):
    """The bench can't back the specimen."""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout


def build(path: Path, arm: str, cond: str) -> dict:
    bench = Bench.from_dir(path)
    counted = {}
    for a in sorted(load_attempts(bench), key=lambda a: a.rep):  # a task's first rep, if it has more
        if a.arm == arm and a.cond == cond and not a.degraded:
            counted.setdefault(a.sha, a)
    task = next((t for t in bench.load_tasks() if t.sha in counted), None)
    if task is None:
        raise SpecimenError(f"{arm} / {cond} has no counted attempt in {path}")
    attempt = counted[task.sha]
    run = next((m for m in manifest.load_all(bench) if m["run_id"] == attempt.run_id), None)
    if run is None:
        raise SpecimenError(f"{task.sha}'s run {attempt.run_id} has no manifest")
    repo = Path(run["repo"])
    upstream = _git(repo, "remote", "get-url", "origin").strip().removesuffix(".git")
    if not upstream.startswith("https://github.com/"):
        raise SpecimenError(f"{repo}'s origin is {upstream}, not a public GitHub repository")
    record = asdict(attempt)
    hw = run["machine"]
    return {
        "picked": "The first task in the bench's own order that this arm has a counted "
                  "attempt on, chosen by position and not by outcome.",
        "repo": repo.name,
        "upstream": upstream,
        "sha": task.sha,
        "subject": task.subject,
        "committed": _git(repo, "show", "-s", "--format=%cI", task.sha).strip(),
        "gold_src": list(task.gold_src),
        "gold_lines": task.gold_lines,
        "gold_diff": _git(repo, "show", "--format=", task.sha, "--", *task.gold_src),
        "f2p": list(task.f2p),
        "p2p": len(task.p2p),
        "attempt": {k: record[k] for k in ATTEMPT_FIELDS},
        "patch": attempt_patch(bench, attempt).read_text(),
        "run": {
            "started_at": run["started_at"],
            "machine": f"{hw.get('cpu', '?')}, {hw.get('memory_gb', '?')} GB ({hw.get('model', '?')})",
            "harness": run["harness"]["commit"][:9],
            "agent": ", ".join(run.get("agents", {}).values()),
            "ollama": (run.get("models") or {}).get("ollama"),
            "limit_s": run["settings"]["timeout"],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bench", type=Path)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--cond", default="blind")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    try:
        specimen = build(args.bench.expanduser(), args.arm, args.cond)
    except SpecimenError as e:
        print(f"specimen refused: {e}", file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(specimen, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"{args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}: "
          f"{specimen['repo']} {specimen['sha']}, "
          f"{'resolved' if specimen['attempt']['resolved'] else 'not resolved'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
