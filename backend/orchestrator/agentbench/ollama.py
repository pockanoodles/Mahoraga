"""The bench's calls to the local Ollama server, in one place.

Local only: the bench refuses cloud-hosted tags (agents._require_local), so
nothing here leaves the machine.
"""
from __future__ import annotations

import os

import httpx

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
_PROBE = "Write a Python function that parses an ISO 8601 date, with a docstring."


def pulled_models(base_url: str = BASE_URL) -> dict[str, str]:
    """Local models, name -> digest. Raises httpx.HTTPError if Ollama is down."""
    r = httpx.get(f"{base_url}/api/tags", timeout=10)
    r.raise_for_status()
    return {m["name"]: m["digest"] for m in r.json().get("models", [])}


def generation_speed(model: str, num_ctx: int | None, base_url: str = BASE_URL) -> float:
    """tok/s over a fixed 128-token generation, by Ollama's own eval counters."""
    options: dict = {"num_predict": 128, "temperature": 0}
    if num_ctx:
        options["num_ctx"] = num_ctx
    r = httpx.post(f"{base_url}/api/generate", timeout=300, json={
        "model": model, "prompt": _PROBE, "stream": False, "options": options})
    r.raise_for_status()
    body = r.json()
    secs = body.get("eval_duration", 0) / 1e9
    return round(body.get("eval_count", 0) / secs, 1) if secs else 0.0


def tagged(model: str) -> str:
    return model if ":" in model else f"{model}:latest"


def derive(model: str, params: dict, base_url: str = BASE_URL) -> str:
    """A tag of `model` with `params` baked in, created once and reused.

    For agents that reach Ollama through its OpenAI-compatible endpoint,
    which ignores per-request options such as num_ctx: without this, Ollama's
    default context silently truncates the agent's prompt. The derived tag
    shares the base model's weights, so it costs no disk.
    """
    base, _, tag = tagged(model).partition(":")
    suffix = "-".join(f"{k}{v}" for k, v in sorted(params.items()))
    name = f"agentbench/{base}:{tag}-{suffix}"
    if name not in pulled_models(base_url):
        r = httpx.post(f"{base_url}/api/create", timeout=300, json={
            "model": name, "from": tagged(model), "parameters": params, "stream": False})
        r.raise_for_status()
    return name
