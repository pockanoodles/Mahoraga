"""Packaging stays consistent with the code.

pyproject.toml is what gets published; requirements*.txt is what CI and
setup.sh install. Both list the dependencies, so a test keeps the copies from
drifting, and the console scripts must point at something that exists.
"""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def _requirements(name: str) -> set[str]:
    lines = (line.split("#", 1)[0].strip() for line in (ROOT / name).read_text().splitlines())
    return {line.replace(" ", "") for line in lines if line}


def test_requirements_txt_is_runtime_dev_and_router_dependencies():
    extras = PROJECT["optional-dependencies"]
    declared = set(PROJECT["dependencies"]) | set(extras["dev"]) | set(extras["router"])
    assert _requirements("requirements.txt") == declared


def test_requirements_semantic_txt_is_the_semantic_extra():
    assert _requirements("requirements-semantic.txt") == set(PROJECT["optional-dependencies"]["semantic"])


def test_the_semantic_extra_brings_the_index_it_searches():
    # Without hnswlib, semantic memory has embeddings and nothing to search them in.
    extras = PROJECT["optional-dependencies"]
    assert set(extras["router"]) <= set(extras["semantic"])


def test_version_is_written_once_and_read_by_pyproject():
    from backend.orchestrator.__version__ import __version__

    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    assert PROJECT["dynamic"] == ["version"]


def test_console_scripts_resolve_to_callables():
    assert {"mahoraga", "orch"} <= PROJECT["scripts"].keys()
    for target in PROJECT["scripts"].values():
        module, func = target.split(":")
        assert callable(getattr(importlib.import_module(module), func))


def test_the_cli_runs_without_hnswlib(tmp_path):
    """hnswlib is the router extra, and a base install has nothing to compile it
    with, so only the router's memory may need it, and only when it is there."""
    code = "\n".join([
        "import sys",
        "sys.modules['hnswlib'] = None  # makes `import hnswlib` raise ImportError",
        "from backend.orchestrator.routing import episodic_memory",
        "assert not episodic_memory._HNSWLIB_AVAILABLE",
        "from backend.orchestrator.cli.main import main",
        "sys.argv = ['mahoraga', 'audit', '--help']",
        "main()",
    ])
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "HOME": str(tmp_path)},
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert "verdict" in out.stdout
