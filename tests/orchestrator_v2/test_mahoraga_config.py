# tests/orchestrator_v2/test_mahoraga_config.py
import json
import pytest
from pathlib import Path
from backend.orchestrator.config import MahoragaConfig


def test_defaults_when_no_file(tmp_path):
    cfg = MahoragaConfig(path=tmp_path / "config.json")
    assert cfg.get("active_backend") == "ollama"
    assert cfg.get("ollama_base_url") == "http://localhost:11434"


def test_set_persists_to_disk(tmp_path):
    path = tmp_path / "config.json"
    cfg = MahoragaConfig(path=path)
    cfg.set("active_backend", "ollama")
    assert json.loads(path.read_text())["active_backend"] == "ollama"


def test_get_reads_persisted_value(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"active_backend": "ollama", "ollama_base_url": "http://localhost:11434"}))
    cfg = MahoragaConfig(path=path)
    assert cfg.get("active_backend") == "ollama"


def test_all_returns_full_dict(tmp_path):
    cfg = MahoragaConfig(path=tmp_path / "config.json")
    result = cfg.all()
    assert "active_backend" in result
    assert "ollama_base_url" in result


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("not valid json {{{")
    cfg = MahoragaConfig(path=path)
    assert cfg.get("active_backend") == "ollama"


def test_set_creates_nested_dirs(tmp_path):
    path = tmp_path / "nested" / "deep" / "config.json"
    cfg = MahoragaConfig(path=path)
    cfg.set("active_backend", "ollama")
    assert path.exists()
