# backend/orchestrator/config.py
from __future__ import annotations
import json
from pathlib import Path

# Controls which backends are registered at startup.
# personal branch: ["ollama"]
# main branch:     ["ollama", "claude"]
ENABLED_BACKENDS: list[str] = ["ollama"]

_DEFAULTS: dict = {
    "active_backend": "claude",
    "ollama_base_url": "http://localhost:11434",
}


class MahoragaConfig:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (Path.home() / ".mahoraga" / "config.json")

    def _load(self) -> dict:
        if not self._path.exists():
            return dict(_DEFAULTS)
        try:
            return {**_DEFAULTS, **json.loads(self._path.read_text())}
        except (json.JSONDecodeError, OSError):
            return dict(_DEFAULTS)

    def get(self, key: str):
        return self._load()[key]

    def set(self, key: str, value) -> None:
        data = self._load()
        data[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))

    def all(self) -> dict:
        return self._load()


# Module-level singleton used by app.py and gateway.py
config = MahoragaConfig()
