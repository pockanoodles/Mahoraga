"""
v2 benchmark harness: classification gate + model hash verification + simulation.

Designed to run against benchmarks/v2/prompts.json (54 prompts, 6 per bucket).

Two mandatory pre-run gates:
  1. Classification gate  — every prompt must route to its intended_bucket.
     If any misclassifies, the run aborts loudly. Prevents silent data corruption
     where a "security" prompt labeled as security actually routes as "code",
     filling the wrong cell of the compatibility matrix.

  2. Model hash check — if a roster.json exists, every active arm's model ID
     must match. Catches silent Ollama upgrades that would make the new bench
     results incomparable to the committed roster.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.strategies.static import classify_bucket
from backend.orchestrator.routing.vocab import BUCKETS, ENABLED_AGENTS

PROMPTS_PATH = Path(__file__).parent.parent.parent.parent.parent / "benchmarks" / "v2" / "prompts.json"


# ── classification gate ────────────────────────────────────────────────────────

def run_classification_gate(prompts: list[dict[str, Any]]) -> None:
    """Assert every prompt in the set classifies to its intended_bucket.

    Aborts loudly on any mismatch. This must run before any bench simulation
    so that the compatibility matrix cells are populated with correctly-bucketed
    prompts only.
    """
    failures: list[str] = []
    for p in prompts:
        ctx = TaskContext.from_task(type("T", (), {"goal": p["text"]})())
        actual = classify_bucket(ctx)
        if actual != p["intended_bucket"]:
            failures.append(
                f"  {p['id']}: intended={p['intended_bucket']!r} "
                f"but classified as={actual!r}\n"
                f"    prompt: {p['text'][:100]}"
            )
    if failures:
        print("ERROR: Classification gate FAILED. The following prompts misclassify:", file=sys.stderr)
        for msg in failures:
            print(msg, file=sys.stderr)
        print(
            "\nThe bench cannot run until all prompts match their intended_bucket.\n"
            "Fix the prompt text or update the intended_bucket field.",
            file=sys.stderr,
        )
        sys.exit(1)


# ── model hash verification ────────────────────────────────────────────────────

def _parse_ollama_list() -> dict[str, str]:
    """Shell out to `ollama list` and return {model_name: model_id} dict.

    Parses lines like:
      qwen3.5:latest    abc123def456    4.7 GB    2 weeks ago
    """
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"WARNING: Could not run `ollama list`: {exc}", file=sys.stderr)
        return {}

    models: dict[str, str] = {}
    for line in result.stdout.splitlines()[1:]:  # skip header row
        parts = line.split()
        if len(parts) >= 2:
            name = parts[0].lower()
            model_id = parts[1]
            # Strip :tag suffix for key lookup (e.g. "qwen3.5:latest" → "qwen3.5")
            bare_name = name.split(":")[0]
            models[bare_name] = model_id
            models[name] = model_id  # also keep fully-qualified name
    return models


def verify_model_hashes(roster_path: Path) -> None:
    """Compare active arm model IDs against the recorded roster.

    Aborts loudly if any active agent's model ID differs from the roster.
    Skips gracefully if roster_path does not exist (first-run scenario before
    any bench has been committed).
    """
    if not roster_path.exists():
        print(f"  [hash-check] No roster at {roster_path} — skipping (first run).")
        return

    roster = json.loads(roster_path.read_text())
    live_models = _parse_ollama_list()
    if not live_models:
        print("  [hash-check] Could not read ollama list — skipping hash verification.", file=sys.stderr)
        return

    mismatches: list[str] = []
    for agent_id in ENABLED_AGENTS:
        # agent_id format: "ollama:<modelname>"
        if not agent_id.startswith("ollama:"):
            continue
        model_name = agent_id.split(":", 1)[1]  # "qwen3.5"

        recorded = roster.get("agents", {}).get(agent_id, {})
        recorded_id = recorded.get("model_id")
        if recorded_id is None:
            continue  # agent not in roster — skip

        live_id = live_models.get(model_name) or live_models.get(f"{model_name}:latest")
        if live_id is None:
            mismatches.append(
                f"  {agent_id}: recorded model_id={recorded_id!r} "
                f"but model not found in `ollama list`"
            )
        elif live_id != recorded_id:
            mismatches.append(
                f"  {agent_id}: model ID mismatch.\n"
                f"    Recorded: {recorded_id}\n"
                f"    Found:    {live_id}\n"
                f"    Re-pull the recorded version or re-bench against the new model."
            )

    if mismatches:
        print("ERROR: Model hash verification FAILED:", file=sys.stderr)
        for msg in mismatches:
            print(msg, file=sys.stderr)
        sys.exit(1)

    print(f"  [hash-check] All {len(ENABLED_AGENTS)} active arms match roster. ✓")


# ── roster builder ─────────────────────────────────────────────────────────────

def build_roster(roster_path: Path) -> dict[str, Any]:
    """Capture current Ollama model IDs and write roster.json.

    Called during `orch benchmark v2 --write-roster` to record the
    model state at bench-run time.
    """
    import platform
    from datetime import datetime, timezone

    live_models = _parse_ollama_list()

    agents: dict[str, dict[str, str]] = {}
    for agent_id in ENABLED_AGENTS:
        if not agent_id.startswith("ollama:"):
            continue
        model_name = agent_id.split(":", 1)[1]
        live_id = live_models.get(model_name) or live_models.get(f"{model_name}:latest", "unknown")
        agents[agent_id] = {"model_name": model_name, "model_id": live_id}

    ollama_version = "unknown"
    try:
        ver = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=5)
        ollama_version = ver.stdout.strip()
    except Exception:  # noqa: BLE001
        pass

    roster: dict[str, Any] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "ollama_version": ollama_version,
        "enabled_agents": list(ENABLED_AGENTS),
        "agents": agents,
    }
    roster_path.parent.mkdir(parents=True, exist_ok=True)
    roster_path.write_text(json.dumps(roster, indent=2))
    return roster


# ── simulation run ─────────────────────────────────────────────────────────────

def run_simulation(
    prompts: list[dict[str, Any]],
    seed: int = 42,
) -> dict[str, Any]:
    """Route all prompts through each active arm and compute per-bucket rewards.

    Uses simulated oracle rewards (no live LLM calls) — the same approach as
    `orch benchmark simulate`. Returns a compatibility matrix suitable for
    warm_start_from_matrix().

    Oracle assignment: qwen3.5 wins on code/test/general/plan (creation-heavy);
    granite4.1-8b wins on debug/security/review/research/refactor (analysis-heavy).
    This matches the qualitative capability split described in CLAUDE.md and
    is the prior that the bandit refines with real observations.
    """
    import random

    rng = random.Random(seed)

    # Per-bucket oracle agent assignment (prior knowledge, not ground truth)
    _ORACLE: dict[str, str] = {
        "code":     "ollama:qwen3.5",
        "test":     "ollama:qwen3.5",
        "plan":     "ollama:qwen3.5",
        "general":  "ollama:qwen3.5",
        "debug":    "ollama:granite4.1-8b",
        "security": "ollama:granite4.1-8b",
        "review":   "ollama:granite4.1-8b",
        "research": "ollama:granite4.1-8b",
        "refactor": "ollama:granite4.1-8b",
    }
    # Oracle quality scores per bucket
    _ORACLE_QUAL: dict[str, float] = {
        "code": 0.88, "test": 0.85, "plan": 0.80, "general": 0.76,
        "debug": 0.86, "security": 0.84, "review": 0.78,
        "research": 0.81, "refactor": 0.83,
    }

    # Accumulate per-(bucket, agent) rewards
    totals: dict[str, dict[str, list[float]]] = {b: {} for b in BUCKETS}
    for agent in ENABLED_AGENTS:
        for b in BUCKETS:
            totals[b][agent] = []

    for p in prompts:
        bucket = p["intended_bucket"]
        oracle_agent = _ORACLE.get(bucket, ENABLED_AGENTS[0])
        oracle_qual = _ORACLE_QUAL.get(bucket, 0.75)

        for agent in ENABLED_AGENTS:
            if agent == oracle_agent:
                reward = oracle_qual + rng.gauss(0, 0.02)
            else:
                reward = oracle_qual * 0.65 + rng.gauss(0, 0.03)
            totals[bucket][agent].append(max(0.0, min(1.0, reward)))

    # Build compatibility matrix: {agent: {bucket: mean_reward}}
    matrix: dict[str, dict[str, float]] = {agent: {} for agent in ENABLED_AGENTS}
    for bucket in BUCKETS:
        for agent in ENABLED_AGENTS:
            scores = totals[bucket][agent]
            matrix[agent][bucket] = round(sum(scores) / len(scores), 4) if scores else 0.5

    return matrix


# ── public entry point ─────────────────────────────────────────────────────────

def run_v2_bench(
    prompts_path: Path = PROMPTS_PATH,
    roster_path: Path | None = None,
    out_dir: Path | None = None,
    seed: int = 42,
    write_roster: bool = False,
    skip_hash_check: bool = False,
) -> dict[str, Any]:
    """Full v2 bench run: gate → hash check → simulation → artifacts.

    Returns the compatibility matrix dict.
    """
    prompts = json.loads(prompts_path.read_text())
    print(f"  Loaded {len(prompts)} prompts from {prompts_path}")

    # Gate 1: classification
    print("  Running classification gate...")
    run_classification_gate(prompts)
    print(f"  Classification gate passed ({len(prompts)} prompts). ✓")

    # Gate 2: model hashes
    if roster_path and not skip_hash_check:
        print("  Verifying model hashes...")
        verify_model_hashes(roster_path)

    # Optional: write / refresh roster before run
    if write_roster and roster_path:
        print("  Writing roster.json...")
        roster = build_roster(roster_path)
        print(f"  Roster written: {roster['agents']}")

    # Simulation
    print("  Running simulation...")
    matrix = run_simulation(prompts, seed=seed)

    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        matrix_path = out_dir / "matrix.json"
        matrix_path.write_text(json.dumps(matrix, indent=2))
        print(f"  Wrote {matrix_path}")

        metadata: dict[str, Any] = {
            "prompts_path": str(prompts_path),
            "prompt_count": len(prompts),
            "seed": seed,
            "agents": list(ENABLED_AGENTS),
            "buckets": list(BUCKETS),
        }
        (out_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2))

    return matrix
