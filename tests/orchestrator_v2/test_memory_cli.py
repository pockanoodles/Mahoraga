"""Tests for `orch memory` CLI subcommands.

The backfill command is the heart of Phase 3. These tests seed a temp
decision log, run backfill against an injected fake encoder, and verify
the resulting EpisodicMemory has the expected aggregation, dedup, and
metadata structure.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import pytest
from typer.testing import CliRunner

from backend.orchestrator.cli.commands import memory as memory_cmd
from backend.orchestrator.cli.commands.memory import app as memory_app
from backend.orchestrator.routing.embeddings import (
    DIM as EMB_DIM,
    EmbeddingService,
    DEFAULT_CACHE_PATH,
)
from backend.orchestrator.routing.episodic_memory import (
    DIM_HANDCRAFT,
    EpisodicMemory,
    INDEX_VERSION,
    SEMANTIC_MODEL_ID,
)


# ── Test doubles ──────────────────────────────────────────────────────────────


class FakeEncoder:
    def __init__(self) -> None:
        self.call_count = 0

    def encode(
        self,
        texts: Sequence[str],
        normalize_embeddings: bool = True,
        batch_size: int = 32,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        self.call_count += 1
        out = np.zeros((len(texts), EMB_DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            seed = int.from_bytes(
                hashlib.sha256(t.encode("utf-8")).digest()[:8], "big"
            )
            rng = np.random.default_rng(seed)
            v = rng.standard_normal(EMB_DIM).astype(np.float32)
            if normalize_embeddings:
                v = v / np.linalg.norm(v)
            out[i] = v
        return out


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def fake_encoder() -> FakeEncoder:
    return FakeEncoder()


@pytest.fixture
def patched_emb_service(
    monkeypatch: pytest.MonkeyPatch, fake_encoder: FakeEncoder
):
    """Patch EmbeddingService construction inside the memory CLI module so
    the fake encoder is injected wherever the CLI builds a service."""
    real_init = EmbeddingService.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["model"] = fake_encoder
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(EmbeddingService, "__init__", patched_init)
    return fake_encoder


def _seed_decision_log(db_path: Path, rows: list[dict]) -> None:
    """Create a routing_decisions.db and insert the given rows.

    Each row dict can contain: task_goal, selected_agent, reward,
    context_vector (list of 9 floats or None), timestamp (ISO string).
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            task_id TEXT,
            task_goal TEXT,
            strategy TEXT NOT NULL,
            selected_agent TEXT NOT NULL,
            available_agents TEXT,
            context_vector TEXT,
            scores TEXT,
            success INTEGER,
            latency_s REAL,
            cost_usd REAL,
            quality_score REAL,
            reward REAL,
            error_message TEXT
        );
    """)
    for r in rows:
        ctx = json.dumps(r.get("context_vector")) if r.get("context_vector") else None
        ts = r.get("timestamp") or datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO decisions "
            "(timestamp, task_goal, strategy, selected_agent, "
            " context_vector, reward, success) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, r["task_goal"], "linucb", r["selected_agent"],
             ctx, r["reward"], 1),
        )
    conn.commit()
    conn.close()


def _hand_vec(seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    return rng.random(DIM_HANDCRAFT).astype(np.float32).tolist()


# ── inspect ───────────────────────────────────────────────────────────────────


class TestInspect:
    def test_no_state_dir_prints_friendly_message(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            memory_app, ["inspect", "--state-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "No metadata file found" in result.output

    def test_inspect_after_episodes_added(
        self,
        runner: CliRunner,
        tmp_path: Path,
        fake_encoder: FakeEncoder,
    ) -> None:
        # Seed an EpisodicMemory at this state_dir.
        mem = EpisodicMemory(state_dir=tmp_path)
        for i in range(5):
            emb = fake_encoder.encode([f"task {i}"])[0]
            mem.add_episode(
                handcraft_vector=np.array(_hand_vec(i), dtype=np.float32),
                agent="aider" if i % 2 == 0 else "ollama",
                reward=0.5 + i * 0.1,
                embedding=emb,
                task_hash=f"hash-{i}",
                timestamp=time.time() - 100 + i,
            )

        result = runner.invoke(
            memory_app, ["inspect", "--state-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert f"Schema version : {INDEX_VERSION}" in result.output
        assert "Episodes       : 5" in result.output
        assert "Semantic       : 5 / 5" in result.output
        assert SEMANTIC_MODEL_ID in result.output
        assert "aider" in result.output
        assert "ollama" in result.output


# ── clear ─────────────────────────────────────────────────────────────────────


class TestClear:
    def test_nothing_to_clear(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            memory_app, ["clear", "--state-dir", str(tmp_path), "--yes"]
        )
        assert result.exit_code == 0
        assert "Nothing to clear" in result.output

    def test_clear_removes_files(
        self,
        runner: CliRunner,
        tmp_path: Path,
        fake_encoder: FakeEncoder,
    ) -> None:
        # Seed memory.
        mem = EpisodicMemory(state_dir=tmp_path)
        for i in range(3):
            emb = fake_encoder.encode([f"t{i}"])[0]
            mem.add_episode(
                handcraft_vector=np.array(_hand_vec(i), dtype=np.float32),
                agent="aider", reward=0.7,
                embedding=emb, task_hash=f"h{i}",
            )

        assert (tmp_path / "episodic_memory.bin").exists()
        assert (tmp_path / "episodic_memory_v2.bin").exists()
        assert (tmp_path / "episodic_memory.meta.json").exists()

        result = runner.invoke(
            memory_app, ["clear", "--state-dir", str(tmp_path), "--yes"]
        )
        assert result.exit_code == 0

        assert not (tmp_path / "episodic_memory.bin").exists()
        assert not (tmp_path / "episodic_memory_v2.bin").exists()
        assert not (tmp_path / "episodic_memory.meta.json").exists()

    def test_clear_keeps_embedding_cache_by_default(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cache = tmp_path / "emb.sqlite"
        cache.write_bytes(b"cache-content")
        monkeypatch.setattr(memory_cmd, "EMB_CACHE_PATH", cache)
        # Also seed the index file so clear has something to do.
        (tmp_path / "episodic_memory.bin").write_bytes(b"x")

        result = runner.invoke(
            memory_app, ["clear", "--state-dir", str(tmp_path), "--yes"]
        )
        assert result.exit_code == 0
        assert cache.exists(), "embedding cache should survive default clear"

    def test_clear_with_embedding_cache_flag_removes_cache(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cache = tmp_path / "emb.sqlite"
        cache.write_bytes(b"cache-content")
        monkeypatch.setattr(memory_cmd, "EMB_CACHE_PATH", cache)
        (tmp_path / "episodic_memory.bin").write_bytes(b"x")

        result = runner.invoke(
            memory_app,
            ["clear", "--state-dir", str(tmp_path),
             "--embedding-cache", "--yes"],
        )
        assert result.exit_code == 0
        assert not cache.exists()


# ── backfill ──────────────────────────────────────────────────────────────────


class TestBackfill:
    def test_no_decision_log_exits_cleanly(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            memory_app,
            [
                "backfill",
                "--decision-log", str(tmp_path / "missing.db"),
                "--state-dir", str(tmp_path / "state"),
                "--cache-path", str(tmp_path / "cache.sqlite"),
            ],
        )
        assert result.exit_code == 0
        assert "Nothing to backfill" in result.output

    def test_backfill_aggregates_at_task_agent_pair(
        self,
        runner: CliRunner,
        tmp_path: Path,
        patched_emb_service: FakeEncoder,
    ) -> None:
        """Locked decision #11: same (task_hash, agent) pair across multiple
        decisions collapses into ONE episode with averaged reward."""
        db_path = tmp_path / "decisions.db"
        # 3 decisions for ("task A", aider) with rewards 0.6, 0.8, 1.0 → avg=0.8
        # 2 decisions for ("task A", ollama) with rewards 0.4, 0.4 → avg=0.4
        # 1 decision for ("task B", aider) with reward 0.9
        rows = [
            {"task_goal": "task A", "selected_agent": "aider",
             "reward": 0.6, "context_vector": _hand_vec(1)},
            {"task_goal": "task A", "selected_agent": "aider",
             "reward": 0.8, "context_vector": _hand_vec(1)},
            {"task_goal": "task A", "selected_agent": "aider",
             "reward": 1.0, "context_vector": _hand_vec(1)},
            {"task_goal": "task A", "selected_agent": "ollama",
             "reward": 0.4, "context_vector": _hand_vec(1)},
            {"task_goal": "task A", "selected_agent": "ollama",
             "reward": 0.4, "context_vector": _hand_vec(1)},
            {"task_goal": "task B", "selected_agent": "aider",
             "reward": 0.9, "context_vector": _hand_vec(2)},
        ]
        _seed_decision_log(db_path, rows)

        state_dir = tmp_path / "state"
        cache_path = tmp_path / "cache.sqlite"

        result = runner.invoke(
            memory_app,
            [
                "backfill",
                "--decision-log", str(db_path),
                "--state-dir", str(state_dir),
                "--cache-path", str(cache_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "unique (task, agent) pairs: 3" in result.output
        assert "Episodes written : 3" in result.output

        # Verify the actual stored content.
        mem = EpisodicMemory(state_dir=state_dir)
        assert mem.size == 3
        assert mem.semantic_size == 3

        # Find the (task A, aider) pair and verify averaged reward.
        normalized_a = "task a"
        a_hash = hashlib.sha256(normalized_a.encode()).hexdigest()
        b_hash = hashlib.sha256(b"task b").hexdigest()

        # Map task_hash + agent → reward
        recovered = {
            (h, a): r
            for h, a, r in zip(mem._task_hashes, mem._agents, mem._rewards)
        }
        assert recovered[(a_hash, "aider")] == pytest.approx(0.8, abs=1e-6)
        assert recovered[(a_hash, "ollama")] == pytest.approx(0.4, abs=1e-6)
        assert recovered[(b_hash, "aider")] == pytest.approx(0.9, abs=1e-6)

    def test_backfill_skips_rows_without_reward(
        self,
        runner: CliRunner,
        tmp_path: Path,
        patched_emb_service: FakeEncoder,
    ) -> None:
        db_path = tmp_path / "decisions.db"
        # Manually construct so some rows have reward=None
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                task_id TEXT, task_goal TEXT,
                strategy TEXT NOT NULL, selected_agent TEXT NOT NULL,
                available_agents TEXT, context_vector TEXT, scores TEXT,
                success INTEGER, latency_s REAL, cost_usd REAL,
                quality_score REAL, reward REAL, error_message TEXT
            );
        """)
        ts = datetime.now(timezone.utc).isoformat()
        # Two rows with reward, one without.
        for goal, reward in [("a", 0.5), ("b", None), ("c", 0.7)]:
            conn.execute(
                "INSERT INTO decisions (timestamp, task_goal, strategy, "
                "selected_agent, reward) VALUES (?, ?, ?, ?, ?)",
                (ts, goal, "linucb", "aider", reward),
            )
        conn.commit()
        conn.close()

        result = runner.invoke(
            memory_app,
            [
                "backfill",
                "--decision-log", str(db_path),
                "--state-dir", str(tmp_path / "state"),
                "--cache-path", str(tmp_path / "cache.sqlite"),
            ],
        )
        assert result.exit_code == 0
        assert "Episodes written : 2" in result.output  # excludes the null

    def test_backfill_dry_run_does_not_write(
        self,
        runner: CliRunner,
        tmp_path: Path,
        patched_emb_service: FakeEncoder,
    ) -> None:
        db_path = tmp_path / "decisions.db"
        _seed_decision_log(db_path, [
            {"task_goal": "x", "selected_agent": "aider",
             "reward": 0.5, "context_vector": _hand_vec(0)},
        ])
        state_dir = tmp_path / "state"

        result = runner.invoke(
            memory_app,
            [
                "backfill",
                "--decision-log", str(db_path),
                "--state-dir", str(state_dir),
                "--cache-path", str(tmp_path / "cache.sqlite"),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "Dry run" in result.output
        # No state files written.
        assert not (state_dir / "episodic_memory.bin").exists()
        assert not (state_dir / "episodic_memory.meta.json").exists()

    def test_backfill_filters_pairs_by_min_reward_episodes(
        self,
        runner: CliRunner,
        tmp_path: Path,
        patched_emb_service: FakeEncoder,
    ) -> None:
        db_path = tmp_path / "decisions.db"
        # ("a", aider) has 3 decisions; ("a", ollama) has 1.
        rows = [
            *[{"task_goal": "a", "selected_agent": "aider",
               "reward": 0.5, "context_vector": _hand_vec(0)}
              for _ in range(3)],
            {"task_goal": "a", "selected_agent": "ollama",
             "reward": 0.4, "context_vector": _hand_vec(0)},
        ]
        _seed_decision_log(db_path, rows)

        result = runner.invoke(
            memory_app,
            [
                "backfill",
                "--decision-log", str(db_path),
                "--state-dir", str(tmp_path / "state"),
                "--cache-path", str(tmp_path / "cache.sqlite"),
                "--min-reward-episodes", "2",
            ],
        )
        assert result.exit_code == 0
        assert "Episodes written : 1" in result.output  # only aider survives

    def test_backfill_recomputes_handcraft_when_context_missing(
        self,
        runner: CliRunner,
        tmp_path: Path,
        patched_emb_service: FakeEncoder,
    ) -> None:
        """If a decision row has no stored context_vector, backfill should
        recompute it from task_goal via TaskContext.from_task."""
        db_path = tmp_path / "decisions.db"
        _seed_decision_log(db_path, [
            {"task_goal": "no-context-task", "selected_agent": "aider",
             "reward": 0.6, "context_vector": None},
            {"task_goal": "no-context-task", "selected_agent": "aider",
             "reward": 0.8, "context_vector": None},
        ])
        state_dir = tmp_path / "state"

        result = runner.invoke(
            memory_app,
            [
                "backfill",
                "--decision-log", str(db_path),
                "--state-dir", str(state_dir),
                "--cache-path", str(tmp_path / "cache.sqlite"),
            ],
        )
        assert result.exit_code == 0
        assert "Episodes written : 1" in result.output

        mem = EpisodicMemory(state_dir=state_dir)
        # Handcraft vector should be a valid 9-dim vector (recomputed).
        assert mem._handcraft_vectors[0].shape == (DIM_HANDCRAFT,)

    def test_backfill_repeated_call_uses_cache(
        self,
        runner: CliRunner,
        tmp_path: Path,
        patched_emb_service: FakeEncoder,
    ) -> None:
        """A second backfill against the same cache should not re-encode."""
        db_path = tmp_path / "decisions.db"
        _seed_decision_log(db_path, [
            {"task_goal": "task X", "selected_agent": "aider",
             "reward": 0.7, "context_vector": _hand_vec(0)},
        ])
        cache_path = tmp_path / "cache.sqlite"

        # First run — encoder gets called.
        runner.invoke(
            memory_app,
            [
                "backfill",
                "--decision-log", str(db_path),
                "--state-dir", str(tmp_path / "state1"),
                "--cache-path", str(cache_path),
            ],
        )
        first_calls = patched_emb_service.call_count
        assert first_calls >= 1

        # Second run — encoder should not be called (cache hit).
        runner.invoke(
            memory_app,
            [
                "backfill",
                "--decision-log", str(db_path),
                "--state-dir", str(tmp_path / "state2"),
                "--cache-path", str(cache_path),
            ],
        )
        second_calls = patched_emb_service.call_count
        # encode_batch only goes to the model on cache miss; so call_count
        # should NOT increase between runs.
        assert second_calls == first_calls
