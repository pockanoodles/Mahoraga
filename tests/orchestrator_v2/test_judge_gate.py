"""Unit tests for the LLM-judge escalation gate (judge_gate).

The judge itself is worker-agnostic — `judge_one` drives any WorkerAdapter — so
these tests use a fake worker (no network, no spend) to cover verdict parsing,
prompt assembly, cost capture, and the failure path.
"""
from __future__ import annotations

import asyncio

from backend.orchestrator.workers.base import WorkerEvent
from backend.orchestrator.routing.judge_gate import (
    build_judge_goal,
    parse_verdict,
    judge_one,
    JUDGE_RUBRIC,
)


class _FakeWorker:
    id = "fake:judge"

    def __init__(self, summary: str = "", cost: float = 0.0, fail: str | None = None):
        self._summary = summary
        self._cost = cost
        self._fail = fail
        self.cleared: list[str] = []

    async def execute(self, attempt, task, feedback=None):
        if self._fail:
            yield WorkerEvent(type="attempt.failed", payload={"error": self._fail})
            return
        yield WorkerEvent(type="metrics", payload={"cost_usd": self._cost})
        yield WorkerEvent(type="attempt.completed", payload={"summary": self._summary})

    def clear_history(self, task_id: str) -> None:
        self.cleared.append(task_id)


def test_parse_verdict_json():
    assert parse_verdict('{"correct": true, "reason": "ok"}') is True
    assert parse_verdict('{"correct": false, "reason": "off-by-one"}') is False
    assert parse_verdict('  {"correct":TRUE}  ') is True  # case-insensitive


def test_parse_verdict_bare_fallback():
    assert parse_verdict("true") is True
    assert parse_verdict("the answer is false") is False


def test_parse_verdict_unparseable():
    assert parse_verdict("") is None
    assert parse_verdict("maybe? true or false") is None  # ambiguous -> None
    assert parse_verdict("no verdict here") is None


def test_build_judge_goal_contains_parts():
    goal = build_judge_goal("Write reverse(s)", "def reverse(s): return s[::-1]")
    assert JUDGE_RUBRIC.split("\n")[0] in goal
    assert "Write reverse(s)" in goal
    assert "s[::-1]" in goal
    assert "JSON only" in goal


def test_judge_one_correct_verdict_and_cost():
    w = _FakeWorker(summary='{"correct": true, "reason": "solves it"}', cost=0.0031)
    verdict, cost, raw, err = asyncio.run(judge_one(w, "task", "output"))
    assert verdict is True
    assert cost == 0.0031
    assert err is None
    assert "correct" in raw
    assert w.cleared  # history cleaned up


def test_judge_one_incorrect_verdict():
    w = _FakeWorker(summary='{"correct": false, "reason": "wrong sign"}', cost=0.002)
    verdict, cost, raw, err = asyncio.run(judge_one(w, "task", "output"))
    assert verdict is False and err is None


def test_judge_one_failure_path():
    w = _FakeWorker(fail="claude timed out")
    verdict, cost, raw, err = asyncio.run(judge_one(w, "task", "output"))
    assert verdict is None
    assert err == "claude timed out"
    assert cost == 0.0
