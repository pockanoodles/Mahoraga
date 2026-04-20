import argparse
import datetime
import sys
from pathlib import Path
from typing import Optional

import httpx

from benchmark.prompts import PROMPT_SETS, ROLES, TIERS

OLLAMA_BASE = "http://localhost:11434"
LOG_PATH = Path(__file__).parent.parent / "brain" / "benchmarks" / "hardware_log.md"
PROMPT_TIMEOUT = 120.0


def discover_models() -> list[str]:
    resp = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=10)
    resp.raise_for_status()
    return [m["name"] for m in resp.json()["models"]]


def run_prompt(model: str, prompt: str, timeout: float = PROMPT_TIMEOUT) -> Optional[dict]:
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        eval_count = data.get("eval_count", 0)
        eval_duration_ns = data.get("eval_duration", 0)
        total_duration_ns = data.get("total_duration", eval_duration_ns)
        tps = eval_count / (eval_duration_ns / 1e9) if eval_duration_ns > 0 else 0.0
        return {"tps": tps, "duration_s": total_duration_ns / 1e9}
    except (httpx.TimeoutException, httpx.HTTPError):
        return None
