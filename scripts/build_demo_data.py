#!/usr/bin/env python3
"""Build the demo bench from the committed agent-edit probe.

    python scripts/build_demo_data.py

Reads experiments/agent_edit/ (tasks.jsonl, results/*.json, dropped.json and
the README) and writes backend/orchestrator/demo/data/: the bench `mahoraga
demo` serves, in the layout agentbench writes for a real repo, plus demo.json.
It writes through the engine's own classes (Task, SuiteConfig, Attempt), so the
files are the ones the engine would have written, and it reads them back
through the engine's loaders before it finishes. The build is deterministic:
the same probe gives the same bytes, which a test pins.

It converts and does not invent. The probe ran before run manifests existed, so
every attempt keeps run_id "" and the bench has no runs/ directory, which is
how the engine already reports an unrecorded machine. The test interpreter's
path was not kept either, so config.json names a plain "python3". The dates and
machine come from the README's "## Results (<dates>, <machine>)" heading, so
the demo can only say what the README says.

Task p2p lists are kept whole: they hold the true size of each task's
regression baseline (450 to 1,450 tests), and the API reports their length.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # the checkout this script sits in, not an installed copy

from backend.orchestrator import demo  # noqa: E402
from backend.orchestrator.agentbench.bank import SuiteConfig, Task  # noqa: E402
from backend.orchestrator.agentbench.runner import Attempt, _artifact, load_attempts  # noqa: E402

PROBE = ROOT / "experiments" / "agent_edit"

# The README's rerun command: --pytest-arg=-m --pytest-arg='not slow'.
PYTEST_ARGS = ["-m", "not slow"]

NOTE = (
    "This is a recorded run of 18 real commits from Mahoraga's own history, so it shows "
    "how the app reads a bench and says nothing about your code. Two local models "
    "attempted each commit through aider, once blind and once with test feedback. The "
    "probe ran before run manifests existed, which means no machine data was recorded "
    "per attempt, and the machine named above is the description in the probe's own "
    "README. Every number in the app comes from these attempts through the same code "
    "that reads a bench of your own."
)

_HEADING = re.compile(r"^## Results \((?P<dates>[\d/-]+), (?P<machine>[^)]+)\)$", re.M)


def _task(row: dict) -> Task:
    # The probe's rows also carry full_sha and gold_src_all (docs and config
    # changed alongside); the engine's Task has no place for either.
    return Task(**{name: row[name] for name in Task.__dataclass_fields__ if name in row})


def _attempt(row: dict) -> Attempt:
    attempt = Attempt(
        sha=row["sha"], arm=f"aider:{row['model']}", cond=row["cond"],
        resolved=row["resolved"], f2p_pass=row["f2p_pass"], f2p_total=row["f2p_total"],
        regressions=row["regressions"], regression_ids=row["regression_ids"],
        suite_crashed=row["suite_crashed"], edited_files=row["edited_files"],
        touched_tests=row["touched_tests"], hit_gold_file=row["hit_gold_file"],
        secs=row["secs"], timed_out=row["timed_out"], llm_calls=row["llm_calls"],
        tokens_sent=row["tokens_sent"], tokens_recv=row["tokens_recv"],
        # The probe counted rejected edits in one number; the engine now splits
        # that into search mismatches and format errors, which can't be undone.
        extra={"edit_errors": row["edit_errors"]},
    )
    if attempt.no_edit != row["no_edit"]:  # the engine derives what the probe stored
        raise ValueError(f"{row['model']}/{row['cond']}/{row['sha']}: the engine reads "
                         f"no_edit={attempt.no_edit}, the probe recorded {row['no_edit']}")
    return attempt


def _labels(readme: str) -> tuple[str, str]:
    found = _HEADING.search(readme)
    if not found:
        raise ValueError("the probe README has no '## Results (<dates>, <machine>)' heading")
    return found["dates"], found["machine"]


def convert(out: Path = demo.DATA_DIR) -> Path:
    """Write the demo bench for the committed probe under `out`; returns its directory."""
    dates, machine = _labels((PROBE / "README.md").read_text())
    tasks = [_task(json.loads(line))
             for line in (PROBE / "tasks.jsonl").read_text().splitlines() if line.strip()]
    attempts = [_attempt(json.loads(p.read_text()))
                for p in sorted((PROBE / "results").glob("*.json"))]
    dropped = {d["sha"]: d["drop"] for d in json.loads((PROBE / "dropped.json").read_text())}

    bench = demo.open_bench(out)
    if bench.dir.exists():
        shutil.rmtree(bench.dir)
    bench.save_config(SuiteConfig(python="python3", pytest_args=PYTEST_ARGS))
    bench.save_tasks(tasks)
    bench.dropped_path.write_text(json.dumps(dropped, indent=1))
    bench.attempts.mkdir(parents=True)
    for a in attempts:
        _artifact(bench, a.arm, a.cond, a.sha, a.rep, "json").write_text(
            json.dumps(asdict(a), indent=1))
    (Path(out) / demo.META).write_text(json.dumps({
        "source": PROBE.relative_to(ROOT).as_posix(),
        "dates": dates, "machine": machine, "note": NOTE,
    }, indent=1) + "\n")

    back = demo.open_bench(out)  # through the engine's loaders, as a user's copy will be read
    if (len(back.load_tasks()), len(load_attempts(back))) != (len(tasks), len(attempts)):
        raise ValueError("the demo bench does not read back as written (a duplicate attempt?)")
    return bench.dir


def main() -> None:
    bench_dir = convert()
    size = sum(f.stat().st_size for f in bench_dir.rglob("*") if f.is_file())
    back = demo.open_bench()
    print(f"{bench_dir.relative_to(ROOT)}: {len(back.load_tasks())} tasks, "
          f"{len(load_attempts(back))} attempts, {size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
