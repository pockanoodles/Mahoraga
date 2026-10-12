"""The demo bench: a recorded run to look at before you have one of your own.

`mahoraga demo` serves the agent-edit probe (experiments/agent_edit: 18 real
Mahoraga commits, two local models driven by aider, with and without test
feedback), converted by scripts/build_demo_data.py into the same on-disk bench
the engine writes for a real repo. Every report, endpoint and verdict runs on
it through the code a real bench uses; nothing is mocked.

The probe predates run manifests, so the demo has none, and the engine says so
wherever it names hardware ("unrecorded machine"). `demo.json` beside the bench
says what the data is, so nothing presents it as the user's own measurement.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from backend.orchestrator.agentbench.bank import Bench

DATA_DIR = Path(__file__).with_name("data")
META = "demo.json"

# A bench directory is named <repo>-<hash of the repo's path>. The probe's
# checkout is not on anyone else's machine, so the packaged bench takes its
# name from this stand-in path instead (Mahoraga-c997b7c3).
DEMO_REPO = Path("/demo/Mahoraga")


def open_bench(root: Path = DATA_DIR) -> Bench:
    """The demo bench under `root`, as the engine's own Bench.

    Bench lets MAHORAGA_AGENTBENCH_ROOT override a root passed in, which would
    quietly point a script at whatever bench root the shell names (or have the
    build write into it). The root is set after construction instead.
    """
    bench = Bench(DEMO_REPO)
    bench.root = Path(root)
    return bench


def meta(root: Path = DATA_DIR) -> dict:
    """What the demo is: source, dates, machine, and a note on its limits."""
    return json.loads((Path(root) / META).read_text())


def install(dest: Path) -> Path:
    """Copy the packaged dataset to `dest`, replacing a previous demo there.

    `dest` becomes the bench root. A directory that holds something else is
    refused rather than emptied: only a copy this function made is replaced.
    """
    dest = Path(dest)
    if dest.exists():
        if any(dest.iterdir()) and not (dest / META).exists():
            raise FileExistsError(f"{dest} exists and is not a Mahoraga demo; move it aside")
        shutil.rmtree(dest)
    shutil.copytree(DATA_DIR, dest)
    return dest
