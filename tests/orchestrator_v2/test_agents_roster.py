"""Where the engine finds its roster, and what an install ships with.

An installed package has no checkout around it, so before the packaged
default existed a pip or Homebrew install found no agents.yaml and the router
started with zero arms."""
from __future__ import annotations

import yaml

from backend.orchestrator.adapters import loader


def test_env_wins(monkeypatch, tmp_path):
    roster = tmp_path / "bench.yaml"
    monkeypatch.setenv("MAHORAGA_AGENTS_YAML", str(roster))
    assert loader.agents_yaml() == roster


def test_a_users_own_roster_beats_the_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv("MAHORAGA_AGENTS_YAML", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    mine = tmp_path / ".mahoraga-v2" / "agents.yaml"
    mine.parent.mkdir()
    mine.write_text("ollama: {}\n")
    assert loader.agents_yaml() == mine


def test_the_checkout_roster_is_used_in_a_source_tree(monkeypatch, tmp_path):
    monkeypatch.delenv("MAHORAGA_AGENTS_YAML", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert loader.agents_yaml() == loader._CHECKOUT_CONFIG


def test_an_install_falls_back_to_the_packaged_roster(monkeypatch, tmp_path):
    monkeypatch.delenv("MAHORAGA_AGENTS_YAML", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(loader, "_CHECKOUT_CONFIG", tmp_path / "no-checkout" / "agents.yaml")
    assert loader.agents_yaml() == loader._PACKAGED_CONFIG
    assert loader._PACKAGED_CONFIG.exists()


def test_the_packaged_roster_runs_local_arms_only():
    cfg = yaml.safe_load(loader._PACKAGED_CONFIG.read_text())
    assert "ollama" in cfg
    hosted = {name: block for name, block in cfg.items() if name != "ollama" and isinstance(block, dict)}
    assert hosted, "the roster should still name the hosted arms, switched off"
    assert all(block.get("enabled") is False for block in hosted.values())
