"""MAHORAGA_AGENTS_YAML lets a bench run serve its own roster."""
from __future__ import annotations

from backend.orchestrator.adapters.loader import load_agent_pool
from backend.orchestrator.adapters.ollama_adapter import OllamaAdapter


def ollama_ids(adapters) -> list[str]:
    # CLI adapters are always built; the roster decides the Ollama arms.
    return [a.name for a in adapters if isinstance(a, OllamaAdapter)]

ROSTER = """
ollama:
  base_url: http://127.0.0.1:11434
  models:
    - id: only-arm
      model: qwen3.5:latest
      capabilities: {code: 0.9}
      enabled: true
"""


def test_env_var_selects_the_roster(tmp_path, monkeypatch):
    f = tmp_path / "bench-agents.yaml"
    f.write_text(ROSTER)
    monkeypatch.setenv("MAHORAGA_AGENTS_YAML", str(f))
    workers, adapters = load_agent_pool()
    assert ollama_ids(adapters) == ["ollama:only-arm"]


def test_an_explicit_path_beats_the_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("MAHORAGA_AGENTS_YAML", str(tmp_path / "missing.yaml"))
    f = tmp_path / "explicit.yaml"
    f.write_text(ROSTER)
    _, adapters = load_agent_pool(config_path=f)
    assert ollama_ids(adapters) == ["ollama:only-arm"]
