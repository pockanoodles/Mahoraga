"""Tests for `orch bench repro` — preflight logic and wiring into live-route.

Fast lane: Ollama's /api/tags probe is a mocked httpx.get, the `claude` binary
check is a mocked shutil.which, and the underlying live_route_cmd is replaced
with a kwargs recorder. No network, no inference, no spend.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from backend.orchestrator.cli.commands import bench as bench_mod
from backend.orchestrator.cli.main import app

runner = CliRunner()

AGENTS_YAML = (
    'ollama:\n'
    '  base_url: "http://localhost:11434"\n'
    '  models:\n'
    '    - id: granite4.1-8b\n'
    '      model: granite4.1:8b\n'
    '      max_ctx: 131072\n'
    'claude-cli:\n'
    '  enabled: false\n'
    '  model: claude-sonnet-4-6\n'
    '  worker_id: claude-cli:sonnet\n'
)


class _TagsResponse:
    """Stands in for httpx.get(<ollama>/api/tags)."""

    def __init__(self, models: list[str]):
        self._models = models
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"models": [{"name": m} for m in self._models]}


def _setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    present_models: list[str] | None = None,
    ollama_up: bool = True,
    claude_on_path: bool = True,
) -> tuple[Path, Path]:
    """Write a minimal agents.yaml + bank and mock the environment probes."""
    cfg = tmp_path / "agents.yaml"
    cfg.write_text(AGENTS_YAML)
    bank = tmp_path / "bank.jsonl"
    bank.write_text('{"prompt": "def f(): ...", "tests": "assert True"}\n')

    if present_models is None:
        present_models = ["granite4.1:8b", "qwen3.5:latest"]

    def fake_get(url, timeout=None):
        if not ollama_up:
            raise ConnectionError("connection refused")
        return _TagsResponse(present_models)

    monkeypatch.setattr(bench_mod.httpx, "get", fake_get)
    monkeypatch.setattr(
        bench_mod.shutil,
        "which",
        lambda binary: "/usr/local/bin/claude" if claude_on_path else None,
    )
    return bank, cfg


def _record_live_route(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace live_route_cmd with a kwargs recorder; returns the record."""
    calls: dict = {}

    def fake_live_route(**kwargs):
        calls.update(kwargs)

    monkeypatch.setattr(bench_mod, "live_route_cmd", fake_live_route)
    return calls


# ── Preflight failures: exit 1 before any inference, with the fix inline ──────


def test_missing_bank_fails_with_regen_hint(tmp_path, monkeypatch):
    _, cfg = _setup(tmp_path, monkeypatch)
    calls = _record_live_route(monkeypatch)
    result = runner.invoke(
        app,
        ["bench", "repro", "--bank", str(tmp_path / "nope.jsonl"), "--config", str(cfg)],
    )
    assert result.exit_code == 1
    assert "bank not found" in result.output
    assert "build_humaneval_bank.py" in result.output
    assert not calls  # never reached live-route


def test_ollama_down_fails_with_daemon_hint(tmp_path, monkeypatch):
    bank, cfg = _setup(tmp_path, monkeypatch, ollama_up=False)
    calls = _record_live_route(monkeypatch)
    result = runner.invoke(
        app, ["bench", "repro", "--bank", str(bank), "--config", str(cfg)]
    )
    assert result.exit_code == 1
    assert "Ollama unreachable" in result.output
    assert "ollama serve" in result.output
    assert not calls


def test_missing_model_fails_with_pull_command(tmp_path, monkeypatch):
    bank, cfg = _setup(tmp_path, monkeypatch, present_models=["qwen3.5:latest"])
    calls = _record_live_route(monkeypatch)
    result = runner.invoke(
        app, ["bench", "repro", "--bank", str(bank), "--config", str(cfg)]
    )
    assert result.exit_code == 1
    assert "ollama pull granite4.1:8b" in result.output
    assert not calls


def test_missing_claude_cli_fails_with_install_hint(tmp_path, monkeypatch):
    bank, cfg = _setup(tmp_path, monkeypatch, claude_on_path=False)
    calls = _record_live_route(monkeypatch)
    result = runner.invoke(
        app, ["bench", "repro", "--bank", str(bank), "--config", str(cfg)]
    )
    assert result.exit_code == 1
    assert "npm install -g @anthropic-ai/claude-code" in result.output
    assert "authenticate" in result.output
    assert not calls


def test_all_problems_reported_together(tmp_path, monkeypatch):
    # One run surfaces every problem, not just the first.
    _, cfg = _setup(
        tmp_path, monkeypatch, present_models=["qwen3.5:latest"], claude_on_path=False
    )
    result = runner.invoke(
        app,
        ["bench", "repro", "--bank", str(tmp_path / "nope.jsonl"), "--config", str(cfg)],
    )
    assert result.exit_code == 1
    assert "bank not found" in result.output
    assert "ollama pull granite4.1:8b" in result.output
    assert "@anthropic-ai/claude-code" in result.output


# ── Preflight passes: the wrapper proceeds and wires live-route correctly ─────


def test_preflight_only_checks_and_exits_clean(tmp_path, monkeypatch):
    bank, cfg = _setup(tmp_path, monkeypatch)
    calls = _record_live_route(monkeypatch)
    result = runner.invoke(
        app,
        ["bench", "repro", "--preflight-only", "--bank", str(bank), "--config", str(cfg)],
    )
    assert result.exit_code == 0
    assert "Preflight OK" in result.output
    assert not calls  # no inference on --preflight-only


def test_smoke_wires_limit_5_through_to_live_route(tmp_path, monkeypatch):
    bank, cfg = _setup(tmp_path, monkeypatch)
    calls = _record_live_route(monkeypatch)
    result = runner.invoke(
        app, ["bench", "repro", "--smoke", "--bank", str(bank), "--config", str(cfg)]
    )
    assert result.exit_code == 0
    assert calls["limit"] == 5
    assert calls["bank"] == bank
    assert calls["escalate_only"] is False
    # published configuration is pinned
    assert calls["local_arm"] == "granite4.1-8b"
    assert calls["judge_model"] == "qwen3.5:latest"
    assert calls["cloud_arm"] == "claude-cli"
    # default output lands in experiments/ and is marked as a smoke run
    assert calls["output"].parent.name == "experiments"
    assert calls["output"].name.startswith("repro_")
    assert calls["output"].name.endswith("_smoke.jsonl")


def test_full_run_defaults(tmp_path, monkeypatch):
    bank, cfg = _setup(tmp_path, monkeypatch)
    calls = _record_live_route(monkeypatch)
    result = runner.invoke(
        app, ["bench", "repro", "--bank", str(bank), "--config", str(cfg)]
    )
    assert result.exit_code == 0
    assert calls["limit"] is None
    assert calls["escalate_only"] is False
    assert calls["json_out"] is False
    assert calls["output"].name.startswith("repro_")
    assert not calls["output"].name.endswith("_smoke.jsonl")


def test_local_only_maps_to_escalate_only(tmp_path, monkeypatch):
    bank, cfg = _setup(tmp_path, monkeypatch)
    calls = _record_live_route(monkeypatch)
    result = runner.invoke(
        app,
        ["bench", "repro", "--local-only", "--bank", str(bank), "--config", str(cfg)],
    )
    assert result.exit_code == 0
    assert calls["escalate_only"] is True


def test_explicit_output_and_json_pass_through(tmp_path, monkeypatch):
    bank, cfg = _setup(tmp_path, monkeypatch)
    calls = _record_live_route(monkeypatch)
    out = tmp_path / "my_run.jsonl"
    result = runner.invoke(
        app,
        ["bench", "repro", "--json", "-o", str(out), "--bank", str(bank), "--config", str(cfg)],
    )
    assert result.exit_code == 0
    assert calls["output"] == out
    assert calls["json_out"] is True
