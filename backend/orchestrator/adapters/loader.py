"""Load agent workers and adapters from agents.yaml.

Reads the project-root agents.yaml (or a path override) and returns fully
constructed (WorkerAdapter, AgentAdapter) pairs ready to register. Replaces
the ~150-line hardcoded registration block that used to live in app.py lifespan.

Adding a new agent: edit agents.yaml, restart orch serve. No Python required.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from .base import AgentAdapter, AgentCapability
from .ollama_adapter import OllamaAdapter
from .codex_adapter import CodexAdapter
from .aider_adapter import AiderAdapter
from .opencode_adapter import OpenCodeAdapter
from .gemini_adapter import GeminiCLIAdapter
from .goose_adapter import GooseAdapter
from ..workers.base import WorkerAdapter
from ..workers.ollama import OllamaWorker

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path(__file__).parents[3] / "agents.yaml"


def _caps(mapping: dict[str, float]) -> list[AgentCapability]:
    return [AgentCapability(name, confidence=conf) for name, conf in mapping.items()]


def load_agent_pool(
    config_path: Path | str | None = None,
    workdir: str | None = None,
    ollama_url_override: str | None = None,
) -> tuple[list[WorkerAdapter], list[AgentAdapter]]:
    """Parse agents.yaml and return (workers, adapters) ready to register.

    Args:
        config_path: Path to agents.yaml. Defaults to project root.
        workdir: CWD passed to file-writing CLI workers (codex, aider).
        ollama_url_override: Overrides the base_url in the yaml (e.g. from
            MahoragaConfig or OLLAMA_BASE_URL env var).
    """
    path = Path(config_path) if config_path else _DEFAULT_CONFIG
    if not path.exists():
        logger.warning("agents.yaml not found at %s — no agents loaded from config", path)
        return [], []

    with path.open() as f:
        cfg: dict[str, Any] = yaml.safe_load(f) or {}

    workers: list[WorkerAdapter] = []
    adapters: list[AgentAdapter] = []
    warm_workers: list[OllamaWorker] = []

    # ── Ollama ────────────────────────────────────────────────────────────────
    ollama_cfg = cfg.get("ollama", {})
    if ollama_cfg:
        base_url = (
            ollama_url_override
            or os.getenv("OLLAMA_BASE_URL")
            or ollama_cfg.get("base_url", "http://localhost:11434")
        )
        roles: list[str] = ollama_cfg.get("roles", ["planner", "fast", "coder", "general"])

        for spec in ollama_cfg.get("models", []):
            if not spec.get("enabled", True):
                continue
            model_id = spec["id"]
            model_tag = spec["model"]
            name = f"ollama:{model_id}"
            max_ctx: int | None = spec.get("max_ctx")
            options: dict | None = spec.get("options")
            extra_payload: dict = spec.get("extra_payload", {})
            warm: bool = spec.get("warm", False)

            for role in roles:
                w = OllamaWorker(
                    model=model_tag,
                    worker_id=f"{name}:{role}",
                    base_url=base_url,
                    options=options,
                    extra_payload=extra_payload,
                    max_ctx=max_ctx,
                )
                workers.append(w)
                if warm and role == "general":
                    warm_workers.append(w)

            cap_map: dict[str, float] = spec.get("capabilities", {})
            adapters.append(OllamaAdapter(
                model=model_tag,
                ollama_base_url=base_url,
                name=name,
                worker_id=f"{name}:general",
                capabilities=_caps(cap_map) if cap_map else None,
            ))

        # Pre-warm flagged models (fire-and-forget, same as before)
        if warm_workers:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    for w in warm_workers:
                        asyncio.ensure_future(w.warm())
            except RuntimeError:
                pass  # no event loop — warmup deferred until first request

    # ── Claude ────────────────────────────────────────────────────────────────
    claude_cfg = cfg.get("claude", {})
    if claude_cfg.get("enabled", True):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            from ..workers.claude import ClaudeWorker
            from .claude_adapter import ClaudeAdapter
            model = claude_cfg.get("model", "claude-sonnet-4-6")
            worker_id = claude_cfg.get("worker_id", "claude:sonnet")
            workers.append(ClaudeWorker(api_key=api_key, model=model, worker_id=worker_id))
            cap_map = claude_cfg.get("capabilities", {})
            adapters.append(ClaudeAdapter(
                api_key=api_key,
                model=model,
                worker_id=worker_id,
                capabilities=_caps(cap_map) if cap_map else None,
            ))
        else:
            logger.info("claude: ANTHROPIC_API_KEY not set — skipping")

    # ── Codex ─────────────────────────────────────────────────────────────────
    codex_cfg = cfg.get("codex", {})
    if codex_cfg.get("enabled", True):
        from ..workers.codex import CodexWorker
        cap_map = codex_cfg.get("capabilities", {})
        workers.append(CodexWorker(cwd=workdir))
        adapters.append(CodexAdapter(capabilities=_caps(cap_map) if cap_map else None))

    # ── Aider ─────────────────────────────────────────────────────────────────
    aider_cfg = cfg.get("aider", {})
    if aider_cfg.get("enabled", True):
        from ..workers.aider import AiderWorker
        model_env = aider_cfg.get("model_env", "AIDER_MODEL")
        model = os.getenv(model_env) or aider_cfg.get("model_default", "ollama_chat/qwen3:4b")
        cap_map = aider_cfg.get("capabilities", {})
        workers.append(AiderWorker(model=model, cwd=workdir))
        adapters.append(AiderAdapter(
            model=model,
            capabilities=_caps(cap_map) if cap_map else None,
        ))

    # ── OpenCode ──────────────────────────────────────────────────────────────
    opencode_cfg = cfg.get("opencode", {})
    if opencode_cfg.get("enabled", True):
        from ..workers.opencode import OpenCodeWorker
        cap_map = opencode_cfg.get("capabilities", {})
        workers.append(OpenCodeWorker())
        adapters.append(OpenCodeAdapter(capabilities=_caps(cap_map) if cap_map else None))

    # ── Gemini CLI ────────────────────────────────────────────────────────────
    gemini_cfg = cfg.get("gemini", {})
    if gemini_cfg.get("enabled", True):
        from ..workers.gemini import GeminiWorker
        cap_map = gemini_cfg.get("capabilities", {})
        workers.append(GeminiWorker())
        adapters.append(GeminiCLIAdapter(capabilities=_caps(cap_map) if cap_map else None))

    # ── Goose ─────────────────────────────────────────────────────────────────
    goose_cfg = cfg.get("goose", {})
    if goose_cfg.get("enabled", True):
        from ..workers.goose import GooseWorker
        cap_map = goose_cfg.get("capabilities", {})
        workers.append(GooseWorker())
        adapters.append(GooseAdapter(capabilities=_caps(cap_map) if cap_map else None))

    logger.info(
        "agents.yaml loaded: %d workers, %d adapters from %s",
        len(workers), len(adapters), path,
    )
    return workers, adapters
