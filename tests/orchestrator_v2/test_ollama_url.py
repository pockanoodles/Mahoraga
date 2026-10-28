"""One place decides the Ollama URL: OLLAMA_BASE_URL, read when a call is made.

The conftest clears the variable for every test, so each test here sets it
itself and checks that the call goes where it points.
"""
from __future__ import annotations

import importlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from backend.orchestrator.adapters.loader import load_agent_pool
from backend.orchestrator.adapters.ollama_adapter import OllamaAdapter
from backend.orchestrator.config import DEFAULT_OLLAMA_URL, MahoragaConfig, ollama_base_url
from backend.orchestrator.domain.models import Mission
from backend.orchestrator.planning.planner import generate_tasks
from backend.orchestrator.routing import quality
from backend.orchestrator.routing.live_route import load_arms
from backend.orchestrator.workers.aider import AiderWorker
from backend.orchestrator.workers.ollama import OllamaWorker

STUDIO = "http://studio:11434"


def _response(payload: dict, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=payload)
    return resp


def _client(*, get: MagicMock | None = None, post: MagicMock | None = None) -> AsyncMock:
    """An httpx.AsyncClient stand-in. Its calls record the URL they were given."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if get is not None:
        client.get = AsyncMock(return_value=get)
    if post is not None:
        client.post = AsyncMock(return_value=post)
    return client


# ── The function ────────────────────────────────────────────────────────────


def test_the_default_is_the_local_server():
    assert ollama_base_url() == DEFAULT_OLLAMA_URL == "http://localhost:11434"


def test_the_variable_wins_and_loses_its_trailing_slash(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO + "/")
    assert ollama_base_url() == STUDIO
    assert ollama_base_url("http://roster:11434") == STUDIO


def test_a_blank_variable_is_not_a_url(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "  ")
    assert ollama_base_url() == DEFAULT_OLLAMA_URL
    assert ollama_base_url("http://roster:11434/") == "http://roster:11434"


def test_it_is_read_on_every_call(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://one:11434")
    assert ollama_base_url() == "http://one:11434"
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://two:11434")
    assert ollama_base_url() == "http://two:11434"


# ── It reaches the legacy paths ─────────────────────────────────────────────


async def test_the_planner_posts_to_it(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO)
    monkeypatch.setattr("backend.orchestrator.planning.planner.ENABLED_BACKENDS", ["ollama"])
    raw = [{"title": "Do it", "goal": "All of it", "dependencies": [], "done_criteria": "Done"}]
    client = _client(post=_response({"message": {"content": json.dumps(raw)}}))
    mission = Mission.new(title="t", goal="g", success_condition="s")
    with patch("backend.orchestrator.planning.planner.httpx.AsyncClient", return_value=client):
        await generate_tasks(mission, run_id="r1")
    assert client.post.call_args.args[0] == f"{STUDIO}/api/chat"


async def test_the_quality_scorer_embeds_through_it(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO)
    client = _client(post=_response({"embeddings": [[0.5, 0.5]]}))
    with patch("backend.orchestrator.routing.quality.httpx.AsyncClient", return_value=client):
        assert await quality._embed("some text") == [0.5, 0.5]
    assert client.post.call_args.args[0] == f"{STUDIO}/api/embed"


async def test_the_aider_health_check_asks_it(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO)
    monkeypatch.setattr("backend.orchestrator.workers.aider.shutil.which", lambda _: "/bin/aider")
    client = _client(get=_response({"models": [{"name": "qwen3:4b"}]}))
    with patch("httpx.AsyncClient", return_value=client):
        health = await AiderWorker().health()
    assert health.healthy
    assert client.get.call_args.args[0] == f"{STUDIO}/api/tags"


async def test_aider_runs_against_the_server_its_health_check_asked(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO)
    seen = {}

    async def spawn(*cmd, **kw):
        seen.update(kw)
        raise FileNotFoundError

    monkeypatch.setattr("backend.orchestrator.workers.aider.asyncio.create_subprocess_exec", spawn)
    task = SimpleNamespace(title="t", goal="g", done_criteria="")
    events = [e async for e in AiderWorker().execute(None, task)]
    assert events[0].payload["error_code"] == "binary_not_found"
    assert seen["env"]["OLLAMA_API_BASE"] == STUDIO


async def test_the_warm_check_asks_it(monkeypatch):
    from backend.orchestrator.service.app import _is_ollama_warm

    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO)
    client = _client(get=_response({"models": [{"name": "qwen3.5"}]}))
    with patch("httpx.AsyncClient", return_value=client):
        assert await _is_ollama_warm() is True
    assert client.get.call_args.args[0] == f"{STUDIO}/api/ps"


def test_a_worker_or_adapter_built_without_a_url_gets_it(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO + "/")
    assert OllamaWorker(model="m", worker_id="ollama:judge")._base_url == STUDIO
    assert OllamaAdapter()._base_url == STUDIO


def test_a_worker_or_adapter_given_a_url_keeps_it(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO)
    assert OllamaWorker(model="m", worker_id="w", base_url="http://other:1/")._base_url == "http://other:1"
    assert OllamaAdapter(ollama_base_url="http://other:1")._base_url == "http://other:1"


def test_the_model_bench_reads_it(monkeypatch):
    from benchmark import model_bench

    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO)
    with patch("httpx.get", return_value=_response({"models": [{"name": "m"}]})) as get:
        model_bench.discover_models()
    get.assert_called_once_with(f"{STUDIO}/api/tags", timeout=10)


# ── The roster, the saved config and the audit agree with it ────────────────

ROSTER = """
ollama:
  base_url: http://roster:11434
  models:
    - id: only-arm
      model: qwen3.5:latest
      capabilities: {code: 0.9}
claude-cli:
  enabled: false
  model: claude-sonnet-4-6
  worker_id: claude-cli:sonnet
"""


def _urls(workers, adapters) -> set[str]:
    return ({w._base_url for w in workers if isinstance(w, OllamaWorker)}
            | {a._base_url for a in adapters if isinstance(a, OllamaAdapter)})


def test_the_pool_takes_the_variable_over_the_roster(tmp_path, monkeypatch):
    roster = tmp_path / "agents.yaml"
    roster.write_text(ROSTER)
    assert _urls(*load_agent_pool(config_path=roster)) == {"http://roster:11434"}
    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO)
    assert _urls(*load_agent_pool(config_path=roster)) == {STUDIO}


def test_an_explicit_override_still_beats_the_variable(tmp_path, monkeypatch):
    roster = tmp_path / "agents.yaml"
    roster.write_text(ROSTER)
    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO)
    pool = load_agent_pool(config_path=roster, ollama_url_override="http://saved:11434")
    assert _urls(*pool) == {"http://saved:11434"}


def test_the_bench_arms_take_the_variable_over_the_roster(tmp_path, monkeypatch):
    roster = tmp_path / "agents.yaml"
    roster.write_text(ROSTER)
    local, judge, _ = load_arms(roster, "only-arm", "qwen3.5:latest")
    assert local._base_url == judge._base_url == "http://roster:11434"
    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO)
    local, judge, _ = load_arms(roster, "only-arm", "qwen3.5:latest")
    assert local._base_url == judge._base_url == STUDIO


def test_the_variable_beats_a_saved_url_and_a_saved_url_beats_the_default(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    cfg = MahoragaConfig(path=path)
    assert cfg.get("ollama_base_url") == DEFAULT_OLLAMA_URL
    path.write_text(json.dumps({"ollama_base_url": "http://saved:11434/"}))
    assert cfg.get("ollama_base_url") == "http://saved:11434"
    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO)
    assert cfg.get("ollama_base_url") == STUDIO
    assert cfg.all()["ollama_base_url"] == STUDIO


def test_saving_a_setting_does_not_write_the_variable_to_disk(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    cfg = MahoragaConfig(path=path)
    monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO)
    cfg.set("routing_mode", "local_first")
    assert json.loads(path.read_text())["ollama_base_url"] == DEFAULT_OLLAMA_URL
    monkeypatch.delenv("OLLAMA_BASE_URL")
    assert cfg.get("ollama_base_url") == DEFAULT_OLLAMA_URL


def test_the_audit_keeps_its_loopback_default_and_reads_the_variable(monkeypatch):
    from backend.orchestrator.agentbench import ollama

    try:
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        importlib.reload(ollama)
        assert ollama.BASE_URL == "http://127.0.0.1:11434"
        monkeypatch.setenv("OLLAMA_BASE_URL", STUDIO + "/")
        importlib.reload(ollama)
        assert ollama.BASE_URL == STUDIO
    finally:
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        importlib.reload(ollama)
