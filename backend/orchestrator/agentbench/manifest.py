"""A run's manifest: everything a published number depends on, in one file.

A resolve rate means nothing without the setup behind it: which model weights,
which agent version, which harness commit, which tasks and prompts, on what
machine. Each `run` writes runs/<run_id>.json under the bench before its first
attempt and stamps the end when it stops. Every attempt carries the run_id.

Each probe is best-effort. A missing binary or a stopped Ollama is recorded as
an error string, never raised, because the manifest must not be the reason a
night's run fails to start.
"""
from __future__ import annotations

import hashlib
import json
import platform
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from . import host, ollama
from .agents import Agent
from .bank import Bench, Task
from .runner import arm_name, prompt_for

HARNESS_ROOT = Path(__file__).resolve().parents[3]


def new_run_id(now: float | None = None) -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.localtime(now)) + "-" + secrets.token_hex(2)


def _out(*cmd: str, cwd: Path | None = None) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"error: {e}"
    return (r.stdout or r.stderr).strip() if r.returncode == 0 else f"error: {r.stderr.strip()[-200:]}"


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def machine() -> dict[str, Any]:
    p = host.power()
    info: dict[str, Any] = {
        "os": platform.platform(), "python": platform.python_version(),
        "power": {"on_ac": p.on_ac, "percent": p.percent, "watts": p.watts},
    }
    if platform.system() == "Darwin":
        info["model"] = _out("sysctl", "-n", "hw.model")
        info["cpu"] = _out("sysctl", "-n", "machdep.cpu.brand_string")
        mem = _out("sysctl", "-n", "hw.memsize")
        info["memory_gb"] = round(int(mem) / 2**30) if mem.isdigit() else mem
    return info


def harness() -> dict[str, Any]:
    head = _out("git", "rev-parse", "HEAD", cwd=HARNESS_ROOT)
    dirty = _out("git", "status", "--porcelain", "--untracked-files=no", cwd=HARNESS_ROOT)
    return {"commit": head, "dirty": bool(dirty) and not dirty.startswith("error")}


def agent_versions(agents: Iterable[Agent]) -> dict[str, str]:
    out = {}
    for name in sorted({a.name for a in agents}):
        binary = shutil.which(name)
        out[name] = _out(binary, "--version") if binary else "error: not on PATH"
    return out


def models(agents: Iterable[Agent]) -> dict[str, Any]:
    try:
        pulled = ollama.pulled_models()
    except Exception as e:  # noqa: BLE001 — Ollama down is recorded, not fatal
        return {"error": str(e)[:200]}
    try:
        import httpx
        version = httpx.get(f"{ollama.BASE_URL}/api/version", timeout=10).json().get("version")
    except Exception as e:  # noqa: BLE001
        version = f"error: {e}"[:200]
    return {"ollama": version,
            "digests": {a.model: pulled.get(ollama.tagged(a.model), "not pulled")
                        for a in agents}}


def tasks_fingerprint(bench: Bench, tasks: list[Task]) -> dict[str, Any]:
    """Hashes of the task file and of the exact prompts sent, so two runs can
    be shown to have measured the same thing."""
    raw = bench.tasks_path.read_text() if bench.tasks_path.exists() else ""
    return {
        "n": len(tasks),
        "tasks_sha256": _sha256(raw),
        "prompts_sha256": _sha256("\n\x00".join(prompt_for(t) for t in tasks)),
        "repo_head": _out("git", "rev-parse", "HEAD", cwd=bench.repo),
    }


def build(bench: Bench, agents: list[Agent], tasks: list[Task], *, run_id: str,
          settings: dict[str, Any], speed_refs: dict[str, float] | None = None
          ) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ended_at": None,
        "repo": str(bench.repo),
        "arms": [arm_name(a) for a in agents],
        "settings": settings,
        "suite": json.loads(bench.config_path.read_text()) if bench.config_path.exists() else None,
        "tasks": tasks_fingerprint(bench, tasks),
        "machine": machine(),
        "harness": harness(),
        "agents": agent_versions(agents),
        "models": models(agents),
        "speed_refs": speed_refs or {},
    }


def path(bench: Bench, run_id: str) -> Path:
    return bench.dir / "runs" / f"{run_id}.json"


def write(bench: Bench, manifest: dict[str, Any]) -> Path:
    p = path(bench, manifest["run_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=1))
    return p


def finish(bench: Bench, run_id: str, attempts: int, stopped: str) -> None:
    """Stamp the end. `stopped` says why: completed, deadline, interrupted."""
    p = path(bench, run_id)
    if not p.exists():
        return
    m = json.loads(p.read_text())
    m.update(ended_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), attempts=attempts, stopped=stopped)
    p.write_text(json.dumps(m, indent=1))


def load_all(bench: Bench) -> list[dict[str, Any]]:
    d = bench.dir / "runs"
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.exists() else []
