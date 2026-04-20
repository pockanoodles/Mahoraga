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


def bench_role(model: str, role: str) -> dict:
    prompts = PROMPT_SETS[role]
    tier_times: dict[str, list[float]] = {t: [] for t in TIERS}
    all_tps: list[float] = []

    for tier in TIERS:
        for prompt in prompts[tier]:
            result = run_prompt(model, prompt)
            if result is not None:
                tier_times[tier].append(result["duration_s"])
                all_tps.append(result["tps"])

    return {
        **{
            tier: round(sum(times) / len(times), 1) if times else None
            for tier, times in tier_times.items()
        },
        "tps": round(sum(all_tps) / len(all_tps), 1) if all_tps else None,
    }


def _fmt_tier(val: Optional[float]) -> str:
    return "—" if val is None else f"{val:.0f}s"


def _fmt_tps(val: Optional[float]) -> str:
    return "—" if val is None else f"{val:.0f} t/s"


def format_table(role: str, model_results: dict[str, dict]) -> str:
    lines = [
        f"### {role.capitalize()}",
        "| Model | Throughput | Easy | Medium | Hard |",
        "|-------|-----------|------|--------|------|",
    ]
    for model, r in model_results.items():
        lines.append(
            f"| {model} | {_fmt_tps(r['tps'])} | {_fmt_tier(r['easy'])} | {_fmt_tier(r['medium'])} | {_fmt_tier(r['hard'])} |"
        )
    return "\n".join(lines) + "\n"


def format_run_section(
    roles_data: dict[str, dict[str, dict]],
    run_time: datetime.datetime,
    roles: list[str],
) -> str:
    suite_label = "Full Suite" if set(roles) == set(ROLES) else f"Roles: {', '.join(roles)}"
    parts = [
        f"## {run_time.strftime('%Y-%m-%d %H:%M')} — {suite_label}",
        "**Hardware:** MacBook Pro M-series, 16 GB unified memory\n",
    ]
    for role in roles:
        parts.append(format_table(role, roles_data[role]))
    parts.append("---\n")
    return "\n".join(parts)
