"""Unit tests for the live local→judge→cloud cascade (live_route).

The cascade is worker-agnostic (`route_one` drives any WorkerAdapter), so these
tests use fake workers — no network, no Ollama, no spend — and real hidden-test
grading via verify_replay.run_case. They cover the escalation decision, the
cost accounting (judge charged always, cloud only on escalation), the
escalate-only cloud skip, and the fold into the simulator's matrix shape.
"""
from __future__ import annotations

import asyncio

from backend.orchestrator.workers.base import WorkerEvent
from backend.orchestrator.routing.live_route import (
    RoutedCase,
    route_one,
    to_matrix,
    load_arms,
)

PASS_CODE = "```python\ndef f():\n    return 42\n```"
FAIL_CODE = "```python\ndef f():\n    return 0\n```"
TESTS = "assert f() == 42"


class _FakeWorker:
    """Yields a fixed (summary, cost) like a real worker's terminal events."""

    def __init__(self, worker_id: str, summary: str = "", cost: float = 0.0, fail: str | None = None):
        self.id = worker_id
        self._summary = summary
        self._cost = cost
        self._fail = fail
        self.calls = 0
        self.cleared: list[str] = []

    async def execute(self, attempt, task, feedback=None):
        self.calls += 1
        if self._fail:
            yield WorkerEvent(type="attempt.failed", payload={"error": self._fail})
            return
        yield WorkerEvent(type="metrics", payload={"cost_usd": self._cost})
        yield WorkerEvent(type="attempt.completed", payload={"summary": self._summary})

    def clear_history(self, task_id: str) -> None:
        self.cleared.append(task_id)


def _run(local_sum, judge_verdict_json, cloud_sum, *, judge_cost=0.0, cloud_cost=0.05,
         run_cloud_always=True):
    local = _FakeWorker("ollama:local:coder", summary=local_sum)
    judge = _FakeWorker("ollama:judge", summary=judge_verdict_json, cost=judge_cost)
    cloud = _FakeWorker("claude-cli:sonnet", summary=cloud_sum, cost=cloud_cost)
    case = asyncio.run(route_one(local, judge, cloud, "solve it", TESTS,
                                 run_cloud_always=run_cloud_always))
    return case, local, judge, cloud


def test_keeps_local_when_judge_says_correct():
    case, local, judge, cloud = _run(PASS_CODE, '{"correct": true}', FAIL_CODE)
    assert case.escalated is False
    assert case.local_passed is True
    assert case.final_passed is True          # served the (correct) local answer
    assert case.total_cost == 0.0             # judge free, cloud not charged
    assert cloud.calls == 1                    # cloud ran for the baseline...
    assert case.cloud_passed is False          # ...and is graded, but not served


def test_escalates_when_judge_says_incorrect():
    case, local, judge, cloud = _run(FAIL_CODE, '{"correct": false}', PASS_CODE,
                                      judge_cost=0.001, cloud_cost=0.05)
    assert case.escalated is True
    assert case.local_passed is False
    assert case.final_passed is True          # cloud recovered it
    assert abs(case.total_cost - (0.001 + 0.05)) < 1e-9


def test_unparseable_verdict_escalates():
    case, *_ = _run(PASS_CODE, "no json here at all", PASS_CODE)
    assert case.judge_verdict is None
    assert case.escalated is True             # None → safe default = escalate


def test_escalate_only_skips_cloud_when_kept():
    case, local, judge, cloud = _run(PASS_CODE, '{"correct": true}', PASS_CODE,
                                      run_cloud_always=False)
    assert case.escalated is False
    assert cloud.calls == 0                    # no baseline spend
    assert case.cloud_passed is None
    assert case.cloud_cost == 0.0
    assert case.final_passed is True


def test_wrong_accept_costs_quality():
    # Judge wrongly accepts a failing local answer → served wrong, no escalation.
    case, *_ = _run(FAIL_CODE, '{"correct": true}', PASS_CODE)
    assert case.escalated is False
    assert case.local_passed is False
    assert case.final_passed is False          # quality tax, not a money tax
    assert case.total_cost == 0.0


def test_to_matrix_folds_cases_for_simulator():
    c1, *_ = _run(PASS_CODE, '{"correct": true}', FAIL_CODE, judge_cost=0.002)
    c2, *_ = _run(FAIL_CODE, '{"correct": false}', PASS_CODE, judge_cost=0.004, cloud_cost=0.06)
    # distinct prompts so both survive the dict fold
    c2 = RoutedCase(**{**c2.__dict__, "prompt": "second"})
    matrix, prompts, cloud_costs, verdicts, mean_judge = to_matrix([c1, c2])
    assert set(prompts) == {"solve it", "second"}
    assert matrix["solve it"]["ollama:local:coder"] is True
    assert matrix["solve it"]["claude-cli:sonnet"] is False
    assert cloud_costs["second"] == 0.06
    assert verdicts["second"] is False
    assert abs(mean_judge - 0.003) < 1e-9      # (0.002 + 0.004) / 2


def test_local_worker_failure_is_graceful():
    local = _FakeWorker("ollama:local:coder", fail="ollama 500")
    judge = _FakeWorker("ollama:judge", summary='{"correct": false}')
    cloud = _FakeWorker("claude-cli:sonnet", summary=PASS_CODE, cost=0.05)
    case = asyncio.run(route_one(local, judge, cloud, "p", TESTS))
    assert case.local_passed is False          # empty output fails the tests
    assert case.escalated is True
    assert case.final_passed is True
    assert "ollama 500" in case.error


def test_load_arms_from_yaml(tmp_path):
    cfg = tmp_path / "agents.yaml"
    cfg.write_text(
        "ollama:\n"
        "  base_url: \"http://localhost:11434\"\n"
        "  models:\n"
        "    - id: granite4.1-8b\n"
        "      model: granite4.1:8b\n"
        "      max_ctx: 131072\n"
        "claude-cli:\n"
        "  enabled: false\n"
        "  model: claude-sonnet-4-6\n"
        "  worker_id: claude-cli:sonnet\n"
    )
    local, judge, cloud = load_arms(cfg, "granite4.1-8b", "qwen3.5:latest")
    assert local._model == "granite4.1:8b"
    assert local.id == "ollama:granite4.1-8b:coder"
    assert judge.id == "ollama:judge"
    assert cloud.id == "claude-cli:sonnet"
    assert cloud._model == "claude-sonnet-4-6"


def test_load_arms_unknown_local_raises(tmp_path):
    cfg = tmp_path / "agents.yaml"
    cfg.write_text("ollama:\n  models:\n    - id: granite4.1-8b\n      model: granite4.1:8b\n")
    try:
        load_arms(cfg, "does-not-exist", "qwen3.5:latest")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "does-not-exist" in str(exc)
