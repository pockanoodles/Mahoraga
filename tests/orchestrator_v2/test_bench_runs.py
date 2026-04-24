"""Tests for bench_runs table, migration, and _capture_run_context."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.orchestrator.routing.decision_log import DecisionLogger


# ── Helpers ───────────────────────────────────────────────────────────────────

def _old_schema_db(path: Path) -> None:
    """Create a DB using the pre-bench_runs schema (no bench_run_id on decisions)."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS decisions (
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
    conn.commit()
    conn.close()


# ── Test 1: bench_runs table created on fresh DB ──────────────────────────────

def test_bench_runs_table_created_on_fresh_db(tmp_path):
    db_path = tmp_path / "test.db"
    logger = DecisionLogger(db_path=db_path)
    tables = {
        row[0]
        for row in logger._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "bench_runs" in tables
    cols = {row[1] for row in logger._conn.execute("PRAGMA table_info(bench_runs)").fetchall()}
    assert "id" in cols
    assert "started_at" in cols
    assert "git_sha" in cols
    assert "ollama_version" in cols
    assert "hostname" in cols
    assert "on_charger" in cols
    assert "bandit_seed" in cols
    assert "prompt_seed" in cols
    logger.close()


# ── Test 2: Migration adds bench_run_id to old decisions table ────────────────

def test_migration_adds_bench_run_id_column(tmp_path):
    db_path = tmp_path / "old.db"
    _old_schema_db(db_path)

    # Confirm bench_run_id is absent before migration
    conn = sqlite3.connect(str(db_path))
    cols_before = {row[1] for row in conn.execute("PRAGMA table_info(decisions)").fetchall()}
    assert "bench_run_id" not in cols_before
    conn.close()

    # Opening DecisionLogger triggers migration
    logger = DecisionLogger(db_path=db_path)
    cols_after = {row[1] for row in logger._conn.execute("PRAGMA table_info(decisions)").fetchall()}
    assert "bench_run_id" in cols_after

    # Also check the index was created
    indexes = {
        row[1]
        for row in logger._conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "idx_decisions_bench_run" in indexes
    logger.close()


# ── Test 2b: Migration is idempotent (re-open existing migrated DB) ───────────

def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "fresh.db"
    logger1 = DecisionLogger(db_path=db_path)
    logger1.close()
    # Opening a second time should not raise
    logger2 = DecisionLogger(db_path=db_path)
    cols = {row[1] for row in logger2._conn.execute("PRAGMA table_info(decisions)").fetchall()}
    assert "bench_run_id" in cols
    logger2.close()


# ── Test 3: create_bench_run inserts a row and returns int id ─────────────────

def test_create_bench_run_returns_int_id(tmp_path):
    db_path = tmp_path / "br.db"
    logger = DecisionLogger(db_path=db_path)
    run_id = logger.create_bench_run(
        mode="force-explore",
        git_sha="abc123",
        git_dirty=0,
        hostname="testhost",
        prompts_file="/tmp/prompts.jsonl",
        agents='["ollama:qwen3-4b"]',
        repeats=1,
        task_count_planned=5,
    )
    assert isinstance(run_id, int)
    assert run_id >= 1

    row = logger._conn.execute(
        "SELECT mode, git_sha, hostname FROM bench_runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row is not None
    assert row[0] == "force-explore"
    assert row[1] == "abc123"
    assert row[2] == "testhost"
    logger.close()


# ── Test 3b: finalize_bench_run sets ended_at and task_count_completed ────────

def test_finalize_bench_run(tmp_path):
    db_path = tmp_path / "br2.db"
    logger = DecisionLogger(db_path=db_path)
    run_id = logger.create_bench_run(mode="bandit", hostname="h1")
    logger.finalize_bench_run(run_id, task_count_completed=7)

    row = logger._conn.execute(
        "SELECT ended_at, task_count_completed FROM bench_runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row[0] is not None  # ended_at set
    assert row[1] == 7
    logger.close()


# ── Test 3c: log_decision stores bench_run_id ────────────────────────────────

def test_log_decision_stores_bench_run_id(tmp_path):
    db_path = tmp_path / "dec.db"
    logger = DecisionLogger(db_path=db_path)
    run_id = logger.create_bench_run(mode="force-explore", hostname="h")

    class _T:
        id = "t1"
        goal = "test goal"
        title = "test"

    row_id = logger.log_decision(
        task=_T(),
        context=None,
        selected_agent="ollama:qwen3-4b",
        available_agents=["ollama:qwen3-4b"],
        strategy="linucb",
        bench_run_id=run_id,
    )

    row = logger._conn.execute(
        "SELECT bench_run_id FROM decisions WHERE id = ?", (row_id,)
    ).fetchone()
    assert row[0] == run_id
    logger.close()


# ── Test 4: _capture_run_context handles missing tools gracefully ─────────────

@pytest.mark.asyncio
async def test_capture_run_context_all_failures(monkeypatch):
    """git unavailable + ollama down + psutil absent → no exception, sensible defaults."""
    import backend.orchestrator.cli.commands.bench as bench_mod

    # Simulate git not available
    def _bad_run(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr("subprocess.run", _bad_run)

    # Simulate psutil not installed
    monkeypatch.setattr(bench_mod, "_psutil", None)

    # Simulate Ollama unreachable — patch httpx.AsyncClient
    class _FailClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def get(self, url):
            raise Exception("connection refused")

    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FailClient())

    ctx = await bench_mod._capture_run_context("http://localhost:8000")

    assert ctx["git_sha"] is None
    assert ctx["git_dirty"] is None
    assert ctx["ollama_version"] is None
    assert ctx["on_charger"] is None
    assert isinstance(ctx["hostname"], str)  # socket.gethostname() always works
    assert len(ctx["hostname"]) > 0


@pytest.mark.asyncio
async def test_capture_run_context_git_works(monkeypatch):
    """When git is available, git_sha and git_dirty are populated."""
    import backend.orchestrator.cli.commands.bench as bench_mod

    class _FakeResult:
        returncode = 0
        stdout = "deadbeef1234567890\n"

    class _FakeDirtyResult:
        returncode = 0
        stdout = ""  # clean working tree

    call_count = [0]

    def _fake_run(args, **kwargs):
        call_count[0] += 1
        if "rev-parse" in args:
            return _FakeResult()
        return _FakeDirtyResult()

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr(bench_mod, "_psutil", None)

    class _FailClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def get(self, url):
            raise Exception("ollama down")

    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FailClient())

    ctx = await bench_mod._capture_run_context("http://localhost:8000")

    assert ctx["git_sha"] == "deadbeef1234567890"
    assert ctx["git_dirty"] == 0  # clean
    assert ctx["ollama_version"] is None


@pytest.mark.asyncio
async def test_capture_run_context_ollama_version(monkeypatch):
    """When Ollama is up, ollama_version is captured."""
    import backend.orchestrator.cli.commands.bench as bench_mod

    def _bad_run(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr("subprocess.run", _bad_run)
    monkeypatch.setattr(bench_mod, "_psutil", None)

    class _FakeResponse:
        status_code = 200
        def json(self):
            return {"version": "0.6.1"}

    class _FakeOllamaClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def get(self, url):
            return _FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeOllamaClient())

    ctx = await bench_mod._capture_run_context("http://localhost:8000")

    assert ctx["ollama_version"] == "0.6.1"
