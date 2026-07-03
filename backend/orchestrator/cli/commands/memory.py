"""
`orch memory` — inspect, clear, and backfill episodic memory.

Subcommands
-----------

  orch memory inspect              — print index metadata + episode counts
  orch memory clear [--yes]        — delete index, metadata, embedding cache
  orch memory backfill [...]       — rebuild semantic index from decision log

The backfill command implements locked design decision #11: deduplicate at
(task_hash, agent_id) granularity and average rewards across duplicate runs,
so a 50-retry task does not flood the HNSW index with 50 near-identical
points. Raw per-decision data stays in the decision log for analysis; only
the *aggregated* episodes are fed to retrieval.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import typer

from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.decision_log import _DEFAULT_DB_PATH
from backend.orchestrator.routing.embeddings import (
    DEFAULT_CACHE_PATH as EMB_CACHE_PATH,
    EmbeddingService,
)
from backend.orchestrator.routing.episodic_memory import (
    DIM_HANDCRAFT,
    DIM_SEMANTIC,
    INDEX_VERSION,
    SEMANTIC_MODEL_ID,
    EpisodicMemory,
)


app = typer.Typer(
    name="memory",
    help="Inspect, clear, and rebuild episodic memory.",
    no_args_is_help=True,
)


_DEFAULT_STATE_DIR = Path.home() / ".mahoraga-v2"
_HC_INDEX_FILE = "episodic_memory.bin"
_SEM_INDEX_FILE = "episodic_memory_v2.bin"
_META_FILE = "episodic_memory.meta.json"


def _hash_goal(text: str) -> Optional[str]:
    if not text or not text.strip():
        return None
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def _file_size(path: Path) -> str:
    if not path.exists():
        return "—"
    size = path.stat().st_size
    if size < 1024:
        return f"{size} B"
    if size < 1024**2:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024**2:.1f} MB"


# ── inspect ───────────────────────────────────────────────────────────────────


@app.command("inspect")
def inspect(
    state_dir: Path = typer.Option(
        _DEFAULT_STATE_DIR,
        "--state-dir",
        help="Directory holding episodic_memory.* files.",
    ),
) -> None:
    """Print episodic memory metadata + file sizes."""
    state_dir = state_dir.expanduser()
    meta_path = state_dir / _META_FILE
    hc_path = state_dir / _HC_INDEX_FILE
    sem_path = state_dir / _SEM_INDEX_FILE

    typer.echo(f"State directory: {state_dir}")

    if not meta_path.exists():
        typer.echo("No metadata file found — episodic memory has never been "
                   "persisted at this location.")
        raise typer.Exit(0)

    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(f"  ERROR reading metadata: {exc}", err=True)
        raise typer.Exit(1) from exc

    version = meta.get("version", 1)
    size = meta.get("size", 0)
    has_emb = meta.get("has_embeddings") or [False] * size
    semantic_size = sum(1 for h in has_emb if h)
    timestamps = [t for t in (meta.get("timestamps") or []) if t]
    agents = meta.get("agents") or []

    typer.echo("")
    typer.echo("─── Index ─────────────────────────────────────")
    typer.echo(f"  Schema version : {version}")
    typer.echo(f"  Episodes       : {size}")
    typer.echo(f"  Semantic       : {semantic_size} / {size}")
    typer.echo(f"  Handcraft dim  : {meta.get('dim_handcraft', meta.get('dim'))}")
    typer.echo(f"  Semantic dim   : {meta.get('dim_semantic', '—')}")
    typer.echo(f"  Model id       : {meta.get('model_id', '—')}")

    typer.echo("")
    typer.echo("─── Files ─────────────────────────────────────")
    typer.echo(f"  {_HC_INDEX_FILE:30s} {_file_size(hc_path)}")
    typer.echo(f"  {_SEM_INDEX_FILE:30s} {_file_size(sem_path)}")
    typer.echo(f"  {_META_FILE:30s} {_file_size(meta_path)}")

    if timestamps:
        oldest = datetime.fromtimestamp(min(timestamps), tz=timezone.utc)
        newest = datetime.fromtimestamp(max(timestamps), tz=timezone.utc)
        typer.echo("")
        typer.echo("─── Timeline ──────────────────────────────────")
        typer.echo(f"  Oldest episode : {oldest.isoformat()}")
        typer.echo(f"  Newest episode : {newest.isoformat()}")

    if agents:
        typer.echo("")
        typer.echo("─── Per-agent counts ──────────────────────────")
        counts: dict[str, int] = defaultdict(int)
        for a in agents:
            counts[a] += 1
        for a, c in sorted(counts.items(), key=lambda kv: -kv[1]):
            typer.echo(f"  {a:25s} {c}")


# ── clear ─────────────────────────────────────────────────────────────────────


@app.command("clear")
def clear(
    state_dir: Path = typer.Option(
        _DEFAULT_STATE_DIR,
        "--state-dir",
        help="Directory holding episodic_memory.* files.",
    ),
    embedding_cache: bool = typer.Option(
        False,
        "--embedding-cache",
        help="Also delete the persistent embedding cache (default: keep).",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
) -> None:
    """Delete the episodic memory index files. By default keeps the embedding
    cache, since re-encoding ~10K episodes takes ~1 minute."""
    state_dir = state_dir.expanduser()
    targets = [
        state_dir / _HC_INDEX_FILE,
        state_dir / _SEM_INDEX_FILE,
        state_dir / _META_FILE,
    ]
    if embedding_cache:
        targets.append(EMB_CACHE_PATH)

    existing = [p for p in targets if p.exists()]
    if not existing:
        typer.echo("Nothing to clear.")
        raise typer.Exit(0)

    typer.echo("Will delete:")
    for p in existing:
        typer.echo(f"  - {p}  ({_file_size(p)})")

    if not yes and not typer.confirm("Proceed?", default=False):
        typer.echo("Aborted.")
        raise typer.Exit(0)

    for p in existing:
        try:
            p.unlink()
            typer.echo(f"deleted {p.name}")
        except OSError as exc:
            typer.echo(f"  failed to delete {p}: {exc}", err=True)


# ── backfill ──────────────────────────────────────────────────────────────────


@app.command("backfill")
def backfill(
    decision_log: Path = typer.Option(
        _DEFAULT_DB_PATH,
        "--decision-log",
        help="Path to routing_decisions.db.",
    ),
    state_dir: Path = typer.Option(
        _DEFAULT_STATE_DIR,
        "--state-dir",
        help="Where to write the rebuilt episodic memory.",
    ),
    cache_path: Path = typer.Option(
        EMB_CACHE_PATH,
        "--cache-path",
        help="Embedding cache (avoids re-encoding on subsequent runs).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Count what would be backfilled without touching disk.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-encode even if the SQLite cache already has the embedding.",
    ),
    min_reward_episodes: int = typer.Option(
        0,
        "--min-reward-episodes",
        help="Minimum non-null reward count for a (task, agent) pair to be "
        "included. 0 means include all.",
    ),
) -> None:
    """Rebuild episodic memory from the decision log, with semantic embeddings.

    Aggregates duplicate (task_hash, agent_id) pairs by averaging their
    rewards (locked design decision #11) — keeps the index dense in *unique*
    routing decisions rather than retry counts.
    """
    decision_log = decision_log.expanduser()
    state_dir = state_dir.expanduser()
    cache_path = cache_path.expanduser()

    if not decision_log.exists():
        typer.echo(f"Decision log not found: {decision_log}", err=True)
        typer.echo("Nothing to backfill.")
        raise typer.Exit(0)

    typer.echo(f"Decision log : {decision_log}")
    typer.echo(f"State dir    : {state_dir}")
    typer.echo(f"Cache path   : {cache_path}")
    typer.echo("")

    # 1. Read decisions with non-null rewards.
    started = time.time()
    typer.echo("Reading decision log…")
    conn = sqlite3.connect(str(decision_log))
    try:
        cur = conn.execute(
            "SELECT task_goal, selected_agent, reward, context_vector, "
            "timestamp FROM decisions WHERE reward IS NOT NULL "
            "AND task_goal IS NOT NULL AND task_goal != ''"
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        typer.echo("No completed decisions in the log. Nothing to backfill.")
        raise typer.Exit(0)

    typer.echo(f"  total rows: {len(rows)}")

    # 2. Aggregate at (task_hash, agent) granularity.
    Aggregate = dict  # alias for clarity; using a dict per pair
    pairs: dict[tuple[str, str], dict] = {}
    skipped_no_hash = 0
    for goal, agent, reward, ctx_json, ts in rows:
        task_hash = _hash_goal(goal)
        if task_hash is None:
            skipped_no_hash += 1
            continue
        key = (task_hash, agent)
        agg = pairs.get(key)
        if agg is None:
            agg = {
                "goal": goal,
                "agent": agent,
                "task_hash": task_hash,
                "rewards": [],
                "context_vectors": [],
                "timestamps": [],
            }
            pairs[key] = agg
        agg["rewards"].append(float(reward))
        if ctx_json:
            try:
                agg["context_vectors"].append(json.loads(ctx_json))
            except (json.JSONDecodeError, TypeError):
                pass
        if ts:
            try:
                agg["timestamps"].append(
                    datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                )
            except (ValueError, AttributeError):
                pass

    if min_reward_episodes > 0:
        before = len(pairs)
        pairs = {
            k: v for k, v in pairs.items()
            if len(v["rewards"]) >= min_reward_episodes
        }
        typer.echo(
            f"  filter --min-reward-episodes={min_reward_episodes}: "
            f"{before} → {len(pairs)} pairs"
        )

    typer.echo(f"  unique (task, agent) pairs: {len(pairs)}")
    typer.echo(f"  skipped (empty/null goal):  {skipped_no_hash}")

    if dry_run:
        unique_goals = {agg["goal"] for agg in pairs.values()}
        typer.echo("")
        typer.echo("Dry run — would encode "
                   f"{len(unique_goals)} unique task descriptions.")
        elapsed = time.time() - started
        typer.echo(f"Read phase: {elapsed:.2f}s")
        raise typer.Exit(0)

    # 3. Encode unique task descriptions in batch.
    typer.echo("")
    typer.echo("Loading embedding service…")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    svc = EmbeddingService(cache_path=cache_path)
    if not svc.available:
        typer.echo(
            "ERROR: embedding service unavailable. "
            "Install with `pip install -r requirements-semantic.txt`.",
            err=True,
        )
        raise typer.Exit(1)

    if force:
        typer.echo("  --force: clearing cache for re-encode")
        if cache_path.exists():
            cache_path.unlink()
        svc.close()
        svc = EmbeddingService(cache_path=cache_path)

    unique_goals = list({agg["goal"] for agg in pairs.values()})
    typer.echo(f"Encoding {len(unique_goals)} unique tasks…")
    enc_start = time.time()
    embeddings = svc.encode_batch(unique_goals)
    enc_elapsed = time.time() - enc_start
    typer.echo(f"  encode time: {enc_elapsed:.2f}s "
               f"({enc_elapsed * 1000 / max(len(unique_goals), 1):.1f} ms/item avg)")
    goal_to_emb: dict[str, Optional[np.ndarray]] = dict(
        zip(unique_goals, embeddings)
    )
    n_with_emb = sum(1 for e in embeddings if e is not None)
    if n_with_emb < len(unique_goals):
        typer.echo(
            f"  WARNING: {len(unique_goals) - n_with_emb} tasks failed to encode"
        )

    # 4. Wipe existing memory files to write a fresh index.
    state_dir.mkdir(parents=True, exist_ok=True)
    for fname in (_HC_INDEX_FILE, _SEM_INDEX_FILE, _META_FILE):
        p = state_dir / fname
        if p.exists():
            p.unlink()

    # 5. Reconstruct EpisodicMemory by replaying aggregated pairs.
    mem = EpisodicMemory(state_dir=state_dir)
    n_added = 0
    n_skipped = 0
    for agg in pairs.values():
        avg_reward = sum(agg["rewards"]) / len(agg["rewards"])
        # Prefer the persisted context vector; fall back to recomputing from
        # the goal text. Both should match because TaskContext.from_task is
        # deterministic for a given goal.
        if agg["context_vectors"]:
            hc_vec = np.asarray(agg["context_vectors"][0], dtype=np.float32)
            if hc_vec.shape != (DIM_HANDCRAFT,):
                hc_vec = TaskContext.from_task(
                    type("T", (), {"goal": agg["goal"]})()
                ).to_vector().astype(np.float32)
        else:
            hc_vec = TaskContext.from_task(
                type("T", (), {"goal": agg["goal"]})()
            ).to_vector().astype(np.float32)

        latest_ts = max(agg["timestamps"]) if agg["timestamps"] else None
        embedding = goal_to_emb.get(agg["goal"])

        try:
            mem.add_episode(
                handcraft_vector=hc_vec,
                agent=agg["agent"],
                reward=avg_reward,
                embedding=embedding,
                task_hash=agg["task_hash"],
                timestamp=latest_ts,
            )
            n_added += 1
        except Exception as exc:  # noqa: BLE001 — defensive
            n_skipped += 1
            typer.echo(f"  failed to add (task_hash={agg['task_hash'][:8]}…, "
                       f"agent={agg['agent']}): {exc}",
                       err=True)

    elapsed = time.time() - started
    typer.echo("")
    typer.echo("─── Summary ───────────────────────────────────")
    typer.echo(f"  Aggregated pairs : {len(pairs)}")
    typer.echo(f"  Episodes written : {n_added}")
    typer.echo(f"  Episodes failed  : {n_skipped}")
    typer.echo(f"  Semantic size    : {mem.semantic_size}")
    typer.echo(f"  Total time       : {elapsed:.2f}s")
