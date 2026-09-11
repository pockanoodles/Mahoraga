"""resource_sampler.py — CPU/thermal telemetry while Mahoraga serves.

This is not a memory-pressure monitor. High RAM usage while local models are
warm is expected and not a problem signal for this workload. The thing worth
measuring is CPU load sustained high enough to spin fans and lag the machine,
which is the actual cost `orch metrics resource` exists to surface.

Log-only: nothing here feeds routing, escalation, or model-lifecycle
decisions. It samples on an interval for as long as `orch serve` is up — that
is exactly when local models are warm and could be the reason the machine is
straining — and appends to its own JSONL log, mirroring funnel_report.py's
separation from routing_decisions.db: this is a distinct population (hardware
samples) that stays out of the routing DB.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path

import httpx
import psutil

logger = logging.getLogger(__name__)

DEFAULT_LOG = Path.home() / ".mahoraga-v2" / "resource.jsonl"


def _thermal_level() -> str:
    """Best-effort macOS thermal-pressure read via `pmset -g therm`.

    At idle, macOS reports "No thermal warning level has been recorded" —
    that line's absence, not the CPU number, is the pressure signal, since a
    warning level replaces it only under real thermal load. No sudo needed.
    Non-macOS or a missing `pmset` reports "unknown", never raises.
    """
    try:
        proc = subprocess.run(
            ["pmset", "-g", "therm"],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:
        return "unknown"
    if proc.returncode != 0:
        return "unknown"
    if "No thermal warning level has been recorded" in proc.stdout:
        return "nominal"
    return "elevated"


async def _warm_models(base_url: str) -> list[str]:
    """Models Ollama currently has loaded, via /api/ps. Empty list, not an
    error, if Ollama is unreachable — sampling must never break serving."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{base_url}/api/ps")
            resp.raise_for_status()
            return [m.get("name", "?") for m in resp.json().get("models", [])]
    except Exception:
        return []


def _append(log_path: Path, row: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


async def sample_once(base_url: str, log_path: Path = DEFAULT_LOG) -> None:
    """Take one sample and append it. Never raises — a sampling failure must
    never take down `orch serve`."""
    try:
        cpu = psutil.cpu_percent(interval=None)
        thermal = _thermal_level()
        warm = await _warm_models(base_url)
        _append(log_path, {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "cpu_percent": cpu,
            "thermal_level": thermal,
            "warm_models": warm,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("resource_sampler: sample failed: %s", exc)


async def sampler_loop(
    base_url: str,
    interval_s: float,
    log_path: Path = DEFAULT_LOG,
) -> None:
    """Run forever, sampling every `interval_s` seconds, until cancelled.

    The first `cpu_percent(interval=None)` call has no prior call to compare
    against and returns a meaningless reading, so it's used here only to
    prime the internal counter — the first logged sample is one interval in.
    """
    psutil.cpu_percent(interval=None)
    while True:
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            return
        await sample_once(base_url, log_path)
