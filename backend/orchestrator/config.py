# backend/orchestrator/config.py
from __future__ import annotations
import json
import os
from pathlib import Path
from backend.orchestrator import paths

# Controls which backends are registered at startup.
# personal branch: ["ollama"]
# main branch:     ["ollama", "claude"]
ENABLED_BACKENDS: list[str] = ["ollama"]

DEFAULT_OLLAMA_URL = "http://localhost:11434"


def ollama_base_url(default: str | None = None) -> str:
    """Where Ollama is: OLLAMA_BASE_URL if set, else `default`, else the local server.

    The one place that variable is read, so every Ollama call agrees. It is read
    on each call, because a constant built at import would freeze it. The result
    has no trailing slash, so callers can append a path.
    """
    url = os.environ.get("OLLAMA_BASE_URL", "").strip() or default or DEFAULT_OLLAMA_URL
    return url.rstrip("/")


_DEFAULTS: dict = {
    "active_backend": "ollama",
    "ollama_base_url": DEFAULT_OLLAMA_URL,
    "workdir": None,  # None = inherit uvicorn CWD
    "routing_mode": "balanced",  # "local_first" | "balanced" | "quality_first"
}


class MahoragaConfig:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (paths.home() / "config.json")

    def _stored(self) -> dict:
        """The defaults with the file laid over them, which is what `set` writes back."""
        if not self._path.exists():
            return dict(_DEFAULTS)
        try:
            return {**_DEFAULTS, **json.loads(self._path.read_text())}
        except (json.JSONDecodeError, OSError):
            return dict(_DEFAULTS)

    def _load(self) -> dict:
        """What the app reads. OLLAMA_BASE_URL beats a saved URL, the way
        MAHORAGA_WORKDIR beats a saved workdir, and `set` never writes it to disk."""
        data = self._stored()
        data["ollama_base_url"] = ollama_base_url(data["ollama_base_url"])
        return data

    def get(self, key: str):
        return self._load()[key]

    def set(self, key: str, value) -> None:
        data = self._stored()
        data[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))

    def all(self) -> dict:
        return self._load()


# Module-level singleton used by app.py and gateway.py
config = MahoragaConfig()


def get_workdir() -> str:
    """Return the active working directory for subprocess file operations.

    Priority: MAHORAGA_WORKDIR env var → config.json workdir → uvicorn CWD.
    """
    env_wd = os.environ.get("MAHORAGA_WORKDIR")
    if env_wd:
        return os.path.expanduser(env_wd)
    config_wd = config.get("workdir")
    if config_wd:
        return os.path.expanduser(config_wd)
    return os.getcwd()
