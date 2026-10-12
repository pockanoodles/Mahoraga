"""Tests for `mahoraga demo`: it serves a fresh copy of the packaged bench, with
the engine's bench root pointed at the copy and never at the user's own, from
a server whose home directory is a fresh scratch directory, so none of the
user's other state (the router's log, the bandit, a Claude login) is in play."""
from __future__ import annotations

import os
import sys

import pytest
from typer.testing import CliRunner

from backend.orchestrator import demo
from backend.orchestrator.cli.main import app


@pytest.fixture
def served(monkeypatch, tmp_path):
    """`mahoraga demo` with the exec stubbed: the server it would have become,
    and with which environment. HOME is a scratch directory, so the real
    ~/.mahoraga-v2 is never in play."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("MAHORAGA_AGENTBENCH_ROOT", "MAHORAGA_DEMO", "MAHORAGA_HEADLESS", "MAHORAGA_CASCADE"):
        monkeypatch.delenv(key, raising=False)
    calls: list[dict] = []

    def execve(path, argv, env):
        calls.append({"path": path, "argv": argv, "env": env})

    monkeypatch.setattr(os, "execve", execve)
    yield calls


def test_demo_serves_a_fresh_copy_and_never_the_real_bench_root(served, tmp_path):
    result = CliRunner().invoke(app, ["demo", "--port", "8123"])
    assert result.exit_code == 0, result.output
    (call,) = served
    state = tmp_path / ".mahoraga-v2"
    root = state / "demo"
    assert call["path"] == sys.executable
    assert call["argv"][1:] == ["-m", "uvicorn", "backend.orchestrator.service.app:app",
                                "--host", "127.0.0.1", "--port", "8123"]
    env = call["env"]
    assert env["MAHORAGA_AGENTBENCH_ROOT"] == str(root)
    assert env["MAHORAGA_DEMO"] == "1"
    assert "MAHORAGA_HEADLESS" not in env
    assert len(demo.open_bench(root).load_tasks()) == 18
    assert not (state / "agentbench").exists()
    assert "http://127.0.0.1:8123" in result.output and "not your data" in result.output


def test_demo_server_runs_in_a_scratch_home_with_the_cascade_off(served, tmp_path):
    result = CliRunner().invoke(app, ["demo"])
    assert result.exit_code == 0, result.output
    env = served[0]["env"]
    home = tmp_path / ".mahoraga-v2" / "demo-home"
    assert env["HOME"] == str(home)
    assert env["MAHORAGA_CASCADE"] == "off"
    assert sorted(p.name for p in home.iterdir()) == [demo.HOME_MARK]


def test_each_demo_starts_from_an_empty_home(served, tmp_path):
    CliRunner().invoke(app, ["demo"])
    home = tmp_path / ".mahoraga-v2" / "demo-home"
    (home / ".mahoraga-v2").mkdir()
    (home / ".mahoraga-v2" / "routing_decisions.db").write_text("from the last demo")
    result = CliRunner().invoke(app, ["demo"])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in home.iterdir()) == [demo.HOME_MARK]


def test_demo_headless_asks_for_no_ui(served):
    result = CliRunner().invoke(app, ["demo", "--headless"])
    assert result.exit_code == 0, result.output
    (call,) = served
    assert call["argv"][-1] == "8000" and call["env"]["MAHORAGA_HEADLESS"] == "1"
    assert "/api/engine" in result.output


def test_demo_does_not_serve_over_a_directory_that_is_not_its_own(served, tmp_path):
    other = tmp_path / ".mahoraga-v2" / "demo"
    other.mkdir(parents=True)
    (other / "notes.txt").write_text("keep")
    result = CliRunner().invoke(app, ["demo"])
    assert result.exit_code == 1 and "not a Mahoraga demo" in result.output
    assert served == [] and (other / "notes.txt").read_text() == "keep"


def test_demo_does_not_empty_a_home_it_did_not_make(served, tmp_path):
    other = tmp_path / ".mahoraga-v2" / "demo-home"
    other.mkdir(parents=True)
    (other / "notes.txt").write_text("keep")
    result = CliRunner().invoke(app, ["demo"])
    assert result.exit_code == 1 and "not a Mahoraga demo home" in result.output
    assert served == [] and (other / "notes.txt").read_text() == "keep"
