"""Subprocess helpers: git, and pytest with per-test results via JUnit XML."""
from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

CRASH = "__crash__"  # result key for a run that produced no per-test results


def git(*args: str, cwd: Path, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[-500:]}")
    return r.stdout


def run_pytest(
    cwd: Path,
    python: str,
    home: Path,
    targets: list[str] | None = None,
    extra_args: list[str] | None = None,
    timeout: float = 900,
) -> dict[str, str]:
    """Run pytest; return {nodeid: 'pass'|'fail'|'skip'}.

    `--continue-on-collection-errors` matters: a test for a module the task
    hasn't written yet can't import, and must not hide the rest of the suite.
    A run with no per-test results returns {CRASH: <output tail>}.
    """
    home.mkdir(parents=True, exist_ok=True)
    xml = cwd / ".agentbench-junit.xml"
    xml.unlink(missing_ok=True)
    cmd = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           "--continue-on-collection-errors", f"--junitxml={xml}",
           *(extra_args or []), *(targets or [])]
    env = {**os.environ, "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {CRASH: "timeout"}
    try:
        if not xml.exists():
            return {CRASH: (r.stdout + r.stderr)[-1500:]}
        results = {}
        for tc in ET.parse(xml).getroot().iter("testcase"):
            tags = {child.tag for child in tc}
            results[f"{tc.get('classname')}::{tc.get('name')}"] = (
                "fail" if tags & {"failure", "error"}
                else "skip" if "skipped" in tags else "pass")
        return results or {CRASH: (r.stdout + r.stderr)[-1500:]}
    finally:
        xml.unlink(missing_ok=True)


def passing(results: dict[str, str]) -> set[str]:
    return {k for k, v in results.items() if v == "pass"}
