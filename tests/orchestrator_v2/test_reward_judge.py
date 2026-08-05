"""Unit tests for the reward-fidelity judge (reward_judge) — fake workers only.

Era 20 diagnosed the composite reward's success term as saturated (~1.0 on every
non-crash), leaving latency as the only gradient. reward_judge turns a free
local judge verdict into `TaskOutcome.correctness`, the coefficient on the
success term. Invariants under test:

  - `judge_correctness` NEVER raises: any worker error, crash, or unparseable
    reply degrades to correctness=None, which the reward treats as legacy 1.0;
  - mode `code` layers the recall-only `differential_check` on a base accept
    only, and its exceptions degrade to an abstain (keep the accept);
  - the judge surface stays pinned to the exec gate's buckets.
"""
from __future__ import annotations

import asyncio
import sqlite3

from backend.orchestrator.routing import reward_judge
from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.execution_gate import EXEC_GATE_BUCKETS
from backend.orchestrator.routing.reward import TaskOutcome
from backend.orchestrator.routing.reward_judge import (
    REWARD_JUDGE_BUCKETS,
    judge_correctness,
    reward_judge_mode,
)
from backend.orchestrator.workers.base import WorkerEvent


class _FakeJudgeWorker:
    """Serves a scripted judge reply (or a scripted failure) for every call."""

    id = "fake:reward-judge"

    def __init__(self, reply: str = "", *, fail: str | None = None, boom: bool = False):
        self._reply = reply
        self._fail = fail
        self._boom = boom
        self.calls = 0

    async def execute(self, attempt, task, feedback=None):
        self.calls += 1
        if self._boom:
            raise RuntimeError("worker exploded")
        yield WorkerEvent(type="metrics", payload={"cost_usd": 0.001})
        if self._fail is not None:
            yield WorkerEvent(type="attempt.failed", payload={"error": self._fail})
        else:
            yield WorkerEvent(type="attempt.completed", payload={"summary": self._reply})

    def clear_history(self, task_id: str) -> None:
        pass


def _patch_worker(monkeypatch, worker) -> None:
    monkeypatch.setattr(reward_judge, "_get_judge_worker", lambda: worker)


# ----- mode parsing -----

def test_mode_defaults_on(monkeypatch):
    monkeypatch.delenv("MAHORAGA_REWARD_JUDGE", raising=False)
    assert reward_judge_mode() == "on"


def test_mode_off_and_code_case_insensitive(monkeypatch):
    for raw, want in [
        ("off", "off"), ("OFF", "off"), ("0", "off"), ("false", "off"), ("No", "off"),
        ("code", "code"), ("CODE", "code"),
        ("on", "on"), ("ON", "on"),
    ]:
        monkeypatch.setenv("MAHORAGA_REWARD_JUDGE", raw)
        assert reward_judge_mode() == want, raw


def test_mode_unknown_falls_back_to_on(monkeypatch):
    monkeypatch.setenv("MAHORAGA_REWARD_JUDGE", "banana")
    assert reward_judge_mode() == "on"


def test_buckets_pinned_to_exec_gate():
    assert REWARD_JUDGE_BUCKETS == EXEC_GATE_BUCKETS


# ----- verdict mapping -----

def test_accept_maps_to_one(monkeypatch):
    monkeypatch.delenv("MAHORAGA_REWARD_JUDGE", raising=False)
    _patch_worker(monkeypatch, _FakeJudgeWorker('{"correct": true, "reason": "ok"}'))
    correctness, cost, _detail = asyncio.run(judge_correctness("task", "output"))
    assert correctness == 1.0
    assert cost == 0.001


def test_reject_maps_to_zero(monkeypatch):
    monkeypatch.delenv("MAHORAGA_REWARD_JUDGE", raising=False)
    _patch_worker(monkeypatch, _FakeJudgeWorker('{"correct": false, "reason": "bug"}'))
    correctness, _cost, _detail = asyncio.run(judge_correctness("task", "output"))
    assert correctness == 0.0


def test_unparseable_reply_abstains(monkeypatch):
    monkeypatch.delenv("MAHORAGA_REWARD_JUDGE", raising=False)
    _patch_worker(monkeypatch, _FakeJudgeWorker("hmm, hard to say"))
    correctness, _cost, detail = asyncio.run(judge_correctness("task", "output"))
    assert correctness is None
    assert "unparseable" in detail


def test_worker_error_abstains(monkeypatch):
    monkeypatch.delenv("MAHORAGA_REWARD_JUDGE", raising=False)
    _patch_worker(monkeypatch, _FakeJudgeWorker(fail="connection refused"))
    correctness, _cost, detail = asyncio.run(judge_correctness("task", "output"))
    assert correctness is None
    assert "judge unavailable" in detail


def test_worker_crash_never_raises(monkeypatch):
    monkeypatch.delenv("MAHORAGA_REWARD_JUDGE", raising=False)
    _patch_worker(monkeypatch, _FakeJudgeWorker(boom=True))
    correctness, cost, detail = asyncio.run(judge_correctness("task", "output"))
    assert correctness is None
    assert cost == 0.0
    assert "judge unavailable" in detail


def test_worker_construction_failure_never_raises(monkeypatch):
    def _explode():
        raise RuntimeError("no ollama")

    monkeypatch.delenv("MAHORAGA_REWARD_JUDGE", raising=False)
    monkeypatch.setattr(reward_judge, "_get_judge_worker", _explode)
    correctness, _cost, detail = asyncio.run(judge_correctness("task", "output"))
    assert correctness is None
    assert "judge unavailable" in detail


# ----- mode "code": recall-only differential layer -----

def _spy_differential(result):
    calls = []

    async def _fake(worker, task_prompt, candidate, **kwargs):
        calls.append((task_prompt, candidate))
        if isinstance(result, Exception):
            raise result
        return result

    return _fake, calls


def test_code_mode_invokes_tool_only_on_base_accept(monkeypatch):
    monkeypatch.setenv("MAHORAGA_REWARD_JUDGE", "code")
    fake, calls = _spy_differential((None, 0.002, "no shared entrypoint"))
    monkeypatch.setattr(reward_judge, "differential_check", fake)

    _patch_worker(monkeypatch, _FakeJudgeWorker('{"correct": false}'))
    correctness, _cost, _detail = asyncio.run(judge_correctness("task", "output"))
    assert correctness == 0.0
    assert calls == []  # base reject → tool never runs

    _patch_worker(monkeypatch, _FakeJudgeWorker('{"correct": true}'))
    correctness, cost, detail = asyncio.run(judge_correctness("task", "output"))
    assert correctness == 1.0  # abstain keeps the base accept
    assert len(calls) == 1
    assert cost == 0.001 + 0.002  # tool cost charged on top of the base verdict
    assert "abstain" in detail


def test_code_mode_tool_override_flips_accept(monkeypatch):
    monkeypatch.setenv("MAHORAGA_REWARD_JUDGE", "code")
    fake, _calls = _spy_differential((False, 0.002, "disagrees on 3 inputs"))
    monkeypatch.setattr(reward_judge, "differential_check", fake)
    _patch_worker(monkeypatch, _FakeJudgeWorker('{"correct": true}'))
    correctness, _cost, detail = asyncio.run(judge_correctness("task", "output"))
    assert correctness == 0.0
    assert "code-judge override" in detail


def test_code_mode_tool_crash_degrades_to_abstain(monkeypatch):
    monkeypatch.setenv("MAHORAGA_REWARD_JUDGE", "code")
    fake, _calls = _spy_differential(RuntimeError("sandbox died"))
    monkeypatch.setattr(reward_judge, "differential_check", fake)
    _patch_worker(monkeypatch, _FakeJudgeWorker('{"correct": true}'))
    correctness, _cost, detail = asyncio.run(judge_correctness("task", "output"))
    assert correctness == 1.0  # base accept stands
    assert "tool crashed" in detail


def test_on_mode_never_invokes_tool(monkeypatch):
    monkeypatch.setenv("MAHORAGA_REWARD_JUDGE", "on")
    fake, calls = _spy_differential((False, 0.002, "would have caught"))
    monkeypatch.setattr(reward_judge, "differential_check", fake)
    _patch_worker(monkeypatch, _FakeJudgeWorker('{"correct": true}'))
    correctness, _cost, _detail = asyncio.run(judge_correctness("task", "output"))
    assert correctness == 1.0
    assert calls == []


# ----- decision_log migration + persistence -----

_OLD_SCHEMA = """
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    task_id TEXT,
    task_goal TEXT,
    strategy TEXT,
    selected_agent TEXT,
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
"""


def test_migration_adds_judge_columns_to_old_db(tmp_path):
    db = tmp_path / "decisions.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_SCHEMA)
    conn.commit()
    pre = {row[1] for row in conn.execute("PRAGMA table_info(decisions)").fetchall()}
    conn.close()
    for col in ("correctness", "judge_cost", "judge_detail"):
        assert col not in pre

    logger = DecisionLogger(db_path=db)  # opening triggers _migrate()
    cols = {row[1] for row in logger._conn.execute("PRAGMA table_info(decisions)").fetchall()}
    assert {"correctness", "judge_cost", "judge_detail"} <= cols


def test_log_outcome_persists_judge_columns(tmp_path):
    logger = DecisionLogger(db_path=tmp_path / "decisions.db")
    task = {"id": "t-judge-1", "goal": "write a function"}
    logger.log_decision(task, None, "ollama", ["ollama"], "linucb")
    outcome = TaskOutcome(
        success=True, latency_s=1.0, cost_usd=0.0, quality_score=0.8,
        agent_name="ollama", bucket="code",
        correctness=0.0, judge_cost=0.003, judge_detail="code-judge override: x",
    )
    logger.log_outcome(task, outcome, reward=0.25)
    row = logger._conn.execute(
        "SELECT correctness, judge_cost, judge_detail FROM decisions WHERE task_id = ?",
        ("t-judge-1",),
    ).fetchone()
    assert row == (0.0, 0.003, "code-judge override: x")


def test_log_outcome_persists_null_correctness(tmp_path):
    """No judge run → NULL correctness on the row, distinguishable from 1.0."""
    logger = DecisionLogger(db_path=tmp_path / "decisions.db")
    task = {"id": "t-judge-2", "goal": "plan a refactor"}
    logger.log_decision(task, None, "ollama", ["ollama"], "linucb")
    outcome = TaskOutcome(
        success=True, latency_s=1.0, cost_usd=0.0, quality_score=0.8,
        agent_name="ollama", bucket="plan",
    )
    logger.log_outcome(task, outcome, reward=0.8)
    row = logger._conn.execute(
        "SELECT correctness, judge_cost, judge_detail FROM decisions WHERE task_id = ?",
        ("t-judge-2",),
    ).fetchone()
    assert row == (None, 0.0, "")
