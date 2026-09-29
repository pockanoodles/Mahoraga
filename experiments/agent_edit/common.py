"""Shared helpers for the agent-edit probe.

Every task lives in its own throwaway clone under WORK; the real repo is only
ever read (git clone --shared). pytest runs with HOME pointed at a scratch dir
so no test at any historical commit can touch real ~/.mahoraga* state — older
commits' tests write into the repo brain and ~/.mahoraga, and did so in the
clones during the first run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
PY = sys.executable
_STATE = Path(os.environ.get("AGENT_EDIT_STATE",
                             Path.home() / ".mahoraga-v2" / "agent_edit"))
WORK = _STATE / "work"
FAKE_HOME = _STATE / "home"
TASKS = ROOT / "tasks.jsonl"


def sh(cmd, cwd=None, timeout=None, env=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd} failed: {r.stderr[-2000:]}")
    return r


def git(*args, cwd, check=True):
    return sh(["git", *args], cwd=cwd, check=check).stdout


def test_env():
    FAKE_HOME.mkdir(parents=True, exist_ok=True)
    (FAKE_HOME / ".mahoraga-v2").mkdir(exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(FAKE_HOME)
    env.pop("MAHORAGA_RESOURCE_LOG", None)
    return env


def run_pytest(cwd: Path, targets: list[str] | None, timeout=600):
    """Run pytest, return {nodeid: 'pass'|'fail'|'skip'} (+ '__crash__' on
    collection failure / timeout)."""
    xml = cwd / ".probe-junit.xml"
    xml.unlink(missing_ok=True)
    # --continue-on-collection-errors: one unimportable file (e.g. a test for a
    # module the task hasn't written yet) must not hide the rest of the suite.
    cmd = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "not slow",
           "--continue-on-collection-errors", f"--junitxml={xml}",
           *(targets or ["tests"])]
    try:
        r = sh(cmd, cwd=cwd, timeout=timeout, env=test_env(), check=False)
    except subprocess.TimeoutExpired:
        return {"__crash__": "timeout"}
    if not xml.exists():
        return {"__crash__": r.stdout[-1500:] + r.stderr[-1500:]}
    out = {}
    for tc in ET.parse(xml).getroot().iter("testcase"):
        nid = f"{tc.get('classname')}::{tc.get('name')}"
        tags = {c.tag for c in tc}
        out[nid] = ("fail" if tags & {"failure", "error"}
                    else "skip" if "skipped" in tags else "pass")
    if r.returncode not in (0, 1) and not out:
        out["__crash__"] = r.stdout[-1500:]
    xml.unlink(missing_ok=True)
    return out


def failing(results: dict) -> set[str]:
    return {k for k, v in results.items() if v == "fail" or k == "__crash__"}


def load_tasks():
    return [json.loads(l) for l in TASKS.read_text().splitlines() if l.strip()]
