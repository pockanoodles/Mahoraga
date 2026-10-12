"""Tests for the CLI's product names: `mahoraga`, `audit` beside `agentbench`,
`version` and `serve --headless`."""
from __future__ import annotations

import importlib.metadata
import os
import re
import tomllib
from pathlib import Path

import pytest
import typer.main
from typer.testing import CliRunner

from backend.orchestrator.cli.commands.agentbench import app as agentbench_app
from backend.orchestrator.cli.main import app

WIDE = {"COLUMNS": "200"}  # so help isn't wrapped mid-sentence
PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _text(output: str) -> str:
    """Help as a person reads it: no colour codes, no box borders, no wrapping."""
    plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
    return " ".join(re.sub(r"[│╭╮╰╯─]", " ", plain).split())


def _run(*args: str, **kw):
    return CliRunner(env=WIDE).invoke(app, list(args), **kw)


def test_the_cli_says_what_the_product_is_for():
    out = _run("--help")
    assert out.exit_code == 0
    assert "Is local coding AI worth it for your code? Measure it, then route it." in _text(out.output)
    commands = typer.main.get_command(app).commands
    for name in ("serve", "version", "audit", "agentbench"):
        assert name in commands


def test_audit_is_the_agentbench_commands_under_the_product_name():
    out = _run("audit", "--help")
    assert out.exit_code == 0
    assert "Measure local agents on your own repo" in _text(out.output)
    commands = typer.main.get_command(app).commands
    audit, legacy = commands["audit"], commands["agentbench"]
    expected = set(typer.main.get_command(agentbench_app).commands)
    assert expected >= {"mine", "preflight", "doctor", "run", "report", "verdict"}
    assert set(audit.commands) == set(legacy.commands) == expected
    for name in expected:  # the same command, not a copy that drifts
        assert audit.commands[name].help == legacy.commands[name].help
        assert [p.name for p in audit.commands[name].params] == [p.name for p in legacy.commands[name].params]


def test_the_old_name_is_kept_and_marked_as_the_old_name():
    out = _run("agentbench", "--help")
    assert out.exit_code == 0 and "mine" in _text(out.output)
    assert "Same as `audit`" in _text(_run("--help").output)


def test_both_names_run_the_same_command(tmp_path, monkeypatch):
    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(tmp_path / "state"))
    repo = tmp_path / "some-repo"
    via_audit = _run("audit", "report", str(repo))
    via_old_name = _run("agentbench", "report", str(repo))
    assert via_audit.exit_code == via_old_name.exit_code == 0
    assert via_audit.output == via_old_name.output and "no attempts yet" in via_audit.output
    assert not (tmp_path / "state").exists()  # reading a bench that isn't there makes nothing


def test_version_prints_the_installed_version_and_nothing_else():
    out = _run("version")
    assert out.exit_code == 0
    assert out.output == importlib.metadata.version("mahoraga") + "\n"


def test_both_console_scripts_start_the_same_app():
    scripts = tomllib.loads(PYPROJECT.read_text())["project"]["scripts"]
    assert scripts["mahoraga"] == scripts["orch"] == "backend.orchestrator.cli.main:main"


# ── serve --headless ──────────────────────────────────────────────────────────


@pytest.fixture
def uvicorn_calls(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda target, **kw: calls.append({"target": target, **kw}))
    # setenv first, so the variable the command sets is removed again afterwards
    monkeypatch.setenv("MAHORAGA_HEADLESS", "")
    return calls


def test_serve_headless_tells_the_app_to_mount_no_ui(uvicorn_calls):
    out = _run("serve", "--headless", "--port", "8123")
    assert out.exit_code == 0, out.output
    assert os.environ["MAHORAGA_HEADLESS"] == "1"
    assert uvicorn_calls == [{"target": "backend.orchestrator.service.app:app",
                              "host": "127.0.0.1", "port": 8123, "reload": False}]


def test_serve_without_the_flag_leaves_the_ui_on(uvicorn_calls):
    assert _run("serve").exit_code == 0
    assert os.environ["MAHORAGA_HEADLESS"] == ""
    assert uvicorn_calls[0]["port"] == 8000


def test_serve_documents_the_flag():
    assert "--headless" in _text(_run("serve", "--help").output)
