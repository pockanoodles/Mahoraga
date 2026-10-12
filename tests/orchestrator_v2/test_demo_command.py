"""Tests for `mahoraga demo`: it serves a fresh copy of the packaged bench, with
the engine's bench root pointed at the copy and never at the user's own."""
from __future__ import annotations

import os
from unittest import mock

import pytest
from typer.testing import CliRunner

from backend.orchestrator import demo
from backend.orchestrator.cli.main import app

ENV = ("MAHORAGA_AGENTBENCH_ROOT", "MAHORAGA_DEMO", "MAHORAGA_HEADLESS")


@pytest.fixture
def served(monkeypatch, tmp_path):
    """`mahoraga demo` with the server stubbed: what it would have served, and
    with which environment. HOME is a scratch directory, so the real
    ~/.mahoraga-v2 is never in play."""
    monkeypatch.setenv("HOME", str(tmp_path))
    calls: list[dict] = []

    def run(app_path, **kwargs):
        calls.append({"app": app_path, **kwargs, "env": {k: os.environ.get(k) for k in ENV}})

    monkeypatch.setattr("uvicorn.run", run)
    with mock.patch.dict(os.environ):  # whatever the command sets is undone afterwards
        for key in ENV:
            os.environ.pop(key, None)
        yield calls


def test_demo_serves_a_fresh_copy_and_never_the_real_bench_root(served, tmp_path):
    result = CliRunner().invoke(app, ["demo", "--port", "8123"])
    assert result.exit_code == 0, result.output
    (call,) = served
    root = tmp_path / ".mahoraga-v2" / "demo"
    assert (call["app"], call["host"], call["port"]) == (
        "backend.orchestrator.service.app:app", "127.0.0.1", 8123)
    assert call["env"] == {"MAHORAGA_AGENTBENCH_ROOT": str(root), "MAHORAGA_DEMO": "1",
                           "MAHORAGA_HEADLESS": None}
    assert len(demo.open_bench(root).load_tasks()) == 18
    assert not (tmp_path / ".mahoraga-v2" / "agentbench").exists()
    assert "http://127.0.0.1:8123" in result.output and "not your data" in result.output


def test_demo_headless_asks_for_no_ui(served):
    result = CliRunner().invoke(app, ["demo", "--headless"])
    assert result.exit_code == 0, result.output
    (call,) = served
    assert call["port"] == 8000 and call["env"]["MAHORAGA_HEADLESS"] == "1"
    assert "/api/engine" in result.output


def test_demo_does_not_serve_over_a_directory_that_is_not_its_own(served, tmp_path):
    other = tmp_path / ".mahoraga-v2" / "demo"
    other.mkdir(parents=True)
    (other / "notes.txt").write_text("keep")
    result = CliRunner().invoke(app, ["demo"])
    assert result.exit_code == 1 and "not a Mahoraga demo" in result.output
    assert served == [] and (other / "notes.txt").read_text() == "keep"
