"""The state directory: $MAHORAGA_HOME, else ~/.mahoraga-v2 (backend/orchestrator/paths.py)."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from backend.orchestrator import paths

ROOT = Path(__file__).resolve().parents[2]


def test_home_defaults_under_the_users_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.home() == tmp_path / ".mahoraga-v2"


def test_mahoraga_home_moves_it(monkeypatch, tmp_path):
    monkeypatch.setenv("MAHORAGA_HOME", str(tmp_path / "state"))
    assert paths.home() == tmp_path / "state"


def test_an_empty_mahoraga_home_is_unset_and_a_tilde_expands(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAHORAGA_HOME", "")
    assert paths.home() == tmp_path / ".mahoraga-v2"
    monkeypatch.setenv("MAHORAGA_HOME", "~/elsewhere")
    assert paths.home() == tmp_path / "elsewhere"


@pytest.mark.parametrize("given", ["{tmp}/state", "state"])
def test_a_path_resolved_at_import_follows_the_variable(tmp_path, given):
    """A relative value lands in the same absolute directory, since the service
    hands it to a daemon that runs from a working directory of its own."""
    tmp = tmp_path.resolve()
    code = ("from backend.orchestrator.store.base import DEFAULT_DB_PATH; "
            "from backend.orchestrator.agentbench.bank import bench_root; "
            "from backend.mcp.funnel_liveness import LOG_PATH; "
            "print(DEFAULT_DB_PATH); print(bench_root()); print(LOG_PATH)")
    env = {**os.environ, "MAHORAGA_HOME": given.format(tmp=tmp), "PYTHONPATH": str(ROOT)}
    env.pop("MAHORAGA_AGENTBENCH_ROOT", None)
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    state = tmp / "state"
    assert out.stdout.split() == [str(state / "mahoraga.db"), str(state / "agentbench"), str(state / "funnel.jsonl")]


# funnel_liveness stays stdlib-only on the MCP tool-discovery path, so it writes
# the rule out, and the import-time test above checks it agrees.
WRITES_THE_RULE_OUT = {"paths.py", "funnel_liveness.py"}


def test_only_paths_names_the_state_directory():
    """Every module reaches the state directory through paths.home(), so one
    variable moves all of it. A new hard-coded path would stay behind."""
    literal = re.compile(r"""["']\.mahoraga-v2["']""")
    offenders = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "backend").rglob("*.py")
                       if p.name not in WRITES_THE_RULE_OUT and "tests" not in p.parts
                       and literal.search(p.read_text()))
    assert offenders == []
