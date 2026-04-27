"""Tests for score_quality_detailed and quality component persistence."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.orchestrator.routing.quality import score_quality, score_quality_detailed
from backend.orchestrator.routing.reward import TaskOutcome
from backend.orchestrator.routing.decision_log import DecisionLogger, _QUALITY_COMPONENT_COLUMNS


# ── Fixtures ─────────────────────────────────────────────────────────────────

PROSE_PROMPT = "Explain how mixture-of-experts routing works in large language models."
PROSE_OUTPUT = (
    "Mixture-of-experts (MoE) routing assigns each input token to a subset of "
    "expert sub-networks rather than passing it through every parameter. A learned "
    "gating network computes a probability distribution over experts; only the top-k "
    "experts (typically 2) receive the token and produce weighted outputs. This sparse "
    "activation reduces compute per forward pass while keeping total parameter count high. "
    "Load-balancing auxiliary losses discourage all tokens routing to the same expert."
)


# ── Test 1: composite parity ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detailed_composite_matches_score_quality():
    """score_quality_detailed composite == score_quality for the same inputs."""
    composite_via_wrapper = await score_quality(PROSE_PROMPT, PROSE_OUTPUT, "research")
    composite_via_detailed, _ = await score_quality_detailed(PROSE_PROMPT, PROSE_OUTPUT, "research")
    assert composite_via_wrapper == composite_via_detailed


@pytest.mark.asyncio
async def test_detailed_composite_matches_for_code_bucket():
    code_output = "def add(a, b):\n    return a + b\n"
    via_wrapper = await score_quality("add two numbers", code_output, "code")
    via_detailed, components = await score_quality_detailed("add two numbers", code_output, "code")
    assert via_wrapper == via_detailed
    assert components is None


# ── Test 2: components dict structure and value ranges ───────────────────────

@pytest.mark.asyncio
async def test_prose_components_keys_and_ranges():
    """Components dict has the five expected keys, all in [0, 1] or None for embed."""
    _, components = await score_quality_detailed(PROSE_PROMPT, PROSE_OUTPUT, "research")

    assert components is not None
    assert set(components.keys()) == {"structural", "novelty", "not_plan", "length", "embed"}

    for key in ("structural", "novelty", "not_plan", "length"):
        val = components[key]
        assert isinstance(val, float), f"{key} should be float, got {type(val)}"
        assert 0.0 <= val <= 1.0, f"{key}={val} out of [0,1]"

    # embed is either None (unavailable) or a float in [0, 1]
    embed = components["embed"]
    if embed is not None:
        assert isinstance(embed, float)
        assert 0.0 <= embed <= 1.0


@pytest.mark.asyncio
async def test_empty_output_returns_zero_components():
    composite, components = await score_quality_detailed(PROSE_PROMPT, "", "research")
    assert composite == 0.0
    assert components is not None
    assert components["embed"] is None


# ── Test 3: DB migration idempotency ─────────────────────────────────────────

def _old_schema_db(path: Path) -> None:
    """Create a DB using the old schema (without quality component columns)."""
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
        CREATE INDEX IF NOT EXISTS idx_strategy ON decisions(strategy);
        CREATE INDEX IF NOT EXISTS idx_agent ON decisions(selected_agent);
        CREATE INDEX IF NOT EXISTS idx_ts ON decisions(timestamp);
    """)
    conn.commit()
    conn.close()


def _get_columns(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(decisions)").fetchall()}
    conn.close()
    return cols


def test_migration_adds_columns_to_old_db(tmp_path):
    """DecisionLogger migrates a pre-existing DB that lacks the 5 component columns."""
    db_path = tmp_path / "old.db"
    _old_schema_db(db_path)

    # Verify columns are absent before migration
    pre_cols = _get_columns(db_path)
    for col in _QUALITY_COMPONENT_COLUMNS:
        assert col not in pre_cols, f"Expected {col} absent before migration"

    # Open with DecisionLogger — triggers migration
    logger = DecisionLogger(db_path)
    logger.close()

    post_cols = _get_columns(db_path)
    for col in _QUALITY_COMPONENT_COLUMNS:
        assert col in post_cols, f"Expected {col} present after migration"


def test_migration_idempotent_on_already_migrated_db(tmp_path):
    """Running DecisionLogger twice on an already-migrated DB doesn't error."""
    db_path = tmp_path / "new.db"

    logger1 = DecisionLogger(db_path)
    logger1.close()

    # Second open — columns already exist, migration must not raise
    logger2 = DecisionLogger(db_path)
    logger2.close()

    cols = _get_columns(db_path)
    for col in _QUALITY_COMPONENT_COLUMNS:
        assert col in cols


def test_migration_idempotent_on_fresh_db(tmp_path):
    """Fresh DB (no prior schema) also ends up with all 5 component columns."""
    db_path = tmp_path / "fresh.db"
    logger = DecisionLogger(db_path)
    logger.close()

    cols = _get_columns(db_path)
    for col in _QUALITY_COMPONENT_COLUMNS:
        assert col in cols


# ── Test 4: component persistence via log_outcome ────────────────────────────

def test_log_outcome_persists_prose_components(tmp_path):
    """When quality_components is populated, the 5 columns are written to the DB."""
    db_path = tmp_path / "test.db"
    logger = DecisionLogger(db_path)

    task = {"id": "t1", "goal": "Explain MoE routing"}
    logger.log_decision(task, None, "agent-a", ["agent-a"], "ucb")

    outcome = TaskOutcome(
        success=True,
        latency_s=1.0,
        cost_usd=0.0,
        quality_score=0.75,
        agent_name="agent-a",
        bucket="research",
        quality_components={
            "structural": 0.8,
            "novelty": 0.7,
            "not_plan": 1.0,
            "length": 0.6,
            "embed": None,
        },
    )
    logger.log_outcome(task, outcome, reward=0.65)

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT quality_structural, quality_novelty, quality_not_plan, quality_length, quality_embed "
        "FROM decisions WHERE task_id = 't1'"
    ).fetchone()
    conn.close()
    logger.close()

    assert row is not None
    assert row[0] == pytest.approx(0.8)
    assert row[1] == pytest.approx(0.7)
    assert row[2] == pytest.approx(1.0)
    assert row[3] == pytest.approx(0.6)
    assert row[4] is None   # embed was None — must not be stored as 0.0


def test_log_outcome_nulls_for_code_bucket(tmp_path):
    """Code-bucket outcomes (quality_components=None) leave the 5 columns NULL."""
    db_path = tmp_path / "code.db"
    logger = DecisionLogger(db_path)

    task = {"id": "c1", "goal": "Write a sort function"}
    logger.log_decision(task, None, "agent-b", ["agent-b"], "ucb")

    outcome = TaskOutcome(
        success=True,
        latency_s=0.5,
        cost_usd=0.0,
        quality_score=0.82,
        agent_name="agent-b",
        bucket="code",
        quality_components=None,
    )
    logger.log_outcome(task, outcome, reward=0.70)

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT quality_structural, quality_novelty, quality_not_plan, quality_length, quality_embed "
        "FROM decisions WHERE task_id = 'c1'"
    ).fetchone()
    conn.close()
    logger.close()

    assert row is not None
    assert all(v is None for v in row), f"Expected all NULLs for code bucket, got {row}"
