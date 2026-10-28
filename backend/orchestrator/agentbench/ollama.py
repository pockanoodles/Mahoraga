"""The bench's calls to the local Ollama server, in one place.

Local only: the bench refuses cloud-hosted tags (agents._require_local), so
nothing here leaves the machine.
"""
from __future__ import annotations

import os
import secrets

import httpx

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
# Tags this bench creates for itself (see `derive`), as opposed to models a person pulled.
DERIVED_PREFIX = "agentbench/"
_PROBE ="Write a Python function that parses an ISO 8601 date, with a docstring."


def pulled_models(base_url: str = BASE_URL, timeout: float = 10) -> dict[str, str]:
    """Local models, name -> digest. Raises httpx.HTTPError if Ollama is down."""
    r = httpx.get(f"{base_url}/api/tags", timeout=timeout)
    r.raise_for_status()
    return {m["name"]: m["digest"] for m in r.json().get("models", [])}


def loaded_models(base_url: str = BASE_URL, timeout: float = 10) -> list[str]:
    """Models Ollama holds in memory right now. Raises httpx.HTTPError if it is down."""
    r = httpx.get(f"{base_url}/api/ps", timeout=timeout)
    r.raise_for_status()
    return [m["name"] for m in r.json().get("models", [])]


def loaded_sizes(base_url: str = BASE_URL, timeout: float = 10) -> dict[str, int]:
    """Loaded models, name -> bytes in memory. Raises httpx.HTTPError if Ollama is down."""
    r = httpx.get(f"{base_url}/api/ps", timeout=timeout)
    r.raise_for_status()
    return {m["name"]: m.get("size", 0) for m in r.json().get("models", [])}


def unload(model: str, base_url: str = BASE_URL, timeout: float = 30) -> None:
    """Drop a model from memory now rather than at the end of its keep-alive."""
    r = httpx.post(f"{base_url}/api/generate", json={"model": model, "keep_alive": 0},
                   timeout=timeout)
    r.raise_for_status()


def version(base_url: str = BASE_URL, timeout: float = 10) -> str | None:
    """Ollama's own version. Raises httpx.HTTPError if it is down, and
    ValueError if something that isn't Ollama answers."""
    r = httpx.get(f"{base_url}/api/version", timeout=timeout)
    r.raise_for_status()
    return r.json().get("version")


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


def reading_speed(model: str, num_ctx: int, depth: int,
                  base_url: str = BASE_URL) -> tuple[float, float, int]:
    """(tokens read per second, tokens written per second, tokens read) for a
    prompt about `depth` tokens deep, the shape of a task that fills the window.
    The prompt starts with a fresh nonce, so no cached prefix shortens it."""
    lines, i = [f"# {secrets.token_hex(8)}"], 0
    while len(lines) * 20 < depth:  # ~20 tokens a line (6,773 for 341 lines on qwen3.5)
        lines.append(f"def step_{i}(x):\n    return x * {i % 97} + {i % 13}\n")
        i += 1
    r = httpx.post(f"{base_url}/api/generate", timeout=3 * 3600, json={
        "model": model, "raw": True, "stream": False,
        "prompt": "\n".join(lines) + "\n# The functions above, summarised:\n",
        "options": {"num_ctx": num_ctx, "num_predict": 256, "temperature": 0}})
    r.raise_for_status()
    body = r.json()
    read, wrote = body.get("prompt_eval_count", 0), body.get("eval_count", 0)
    read_s, wrote_s = body.get("prompt_eval_duration", 0) / 1e9, body.get("eval_duration", 0) / 1e9
    if not (read_s and wrote_s):
        raise ValueError(f"Ollama returned no timings for {model}")
    if wrote < 32:  # a rate over a handful of tokens is mostly startup noise
        raise ValueError(f"{model} stopped after {wrote} tokens, too few to time")
    return round(read / read_s, 1), round(wrote / wrote_s, 1), read


def tagged(model: str) -> str:
    return model if ":" in model else f"{model}:latest"


def is_derived(name: str) -> bool:
    """A tag `derive` made, not a model someone pulled."""
    return name.startswith(DERIVED_PREFIX)


def derive(model: str, params: dict, base_url: str = BASE_URL) -> str:
    """A tag of `model` with `params` baked in, created once and reused.

    For agents that reach Ollama through its OpenAI-compatible endpoint,
    which ignores per-request options such as num_ctx: without this, Ollama's
    default context silently truncates the agent's prompt. The derived tag
    shares the base model's weights, so it costs no disk.
    """
    base, _, tag = tagged(model).partition(":")
    suffix = "-".join(f"{k}{v}" for k, v in sorted(params.items()))
    name = f"{DERIVED_PREFIX}{base}:{tag}-{suffix}"
    if name not in pulled_models(base_url):
        r = httpx.post(f"{base_url}/api/create", timeout=300, json={
            "model": name, "from": tagged(model), "parameters": params, "stream": False})
        r.raise_for_status()
    return name
