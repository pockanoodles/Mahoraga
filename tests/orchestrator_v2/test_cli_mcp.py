"""Tests for `mahoraga mcp`, the stdio MCP bridge.

It speaks on stdio, so these read its help and patch the bridge out. None starts it.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from typer.testing import CliRunner

from backend.orchestrator.cli.main import app


def _text(output: str) -> str:
    """Help as a person reads it: no colour codes, no box borders, no wrapping."""
    plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
    return " ".join(re.sub(r"[│╭╮╰╯─]", " ", plain).split())


def _run(*args: str):
    return CliRunner(env={"COLUMNS": "200"}).invoke(app, list(args))


def test_mcp_is_a_command_of_the_product_cli():
    out = _run("--help")
    assert out.exit_code == 0 and "mcp" in _text(out.output)


def test_mcp_help_says_it_is_the_stdio_bridge():
    out = _run("mcp", "--help")
    assert out.exit_code == 0
    text = _text(out.output)
    assert "stdio" in text and "MCP" in text and "mahoraga serve" in text


def test_mcp_runs_the_bridges_main_and_nothing_else(monkeypatch):
    import backend.mcp.server as bridge

    started: list[str] = []

    async def fake_main() -> None:  # stands in for the stdio loop, which a test must not start
        started.append("main")

    monkeypatch.setattr(bridge, "main", fake_main)
    out = _run("mcp")
    assert out.exit_code == 0 and started == ["main"]
    assert out.output == ""  # stdout is the protocol's channel: the command adds nothing to it


def test_the_bridge_still_runs_as_a_module():
    import backend.mcp.server as bridge

    assert inspect.iscoroutinefunction(bridge.main)
    assert 'if __name__ == "__main__":' in Path(bridge.__file__).read_text()


def test_the_hints_for_a_stopped_engine_name_a_command_that_exists():
    import backend.mcp.server as bridge

    assert "mahoraga serve" in bridge._NOT_RUNNING
    assert "backend.main" not in bridge._NOT_RUNNING  # there is no such module
