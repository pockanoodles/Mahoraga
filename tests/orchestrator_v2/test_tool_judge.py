"""Unit tests for the compute-augmented judge (tool_judge), v3.

v3 (findings Era 18) removes the LLM from BOTH sides of the compare after two
live iterations broke the invariant on the same row:
  - SOLVER side: the number must survive `solve_consensus` (≥2 agreeing runs);
  - CANDIDATE side: numbers are parsed deterministically and the executed answer
    is checked against the candidate's LAST `LAST_K` numbers (its conclusion),
    so an intermediate like "3/12" can't mask a wrong final answer.

The fake worker only ever serves the solver and the base verdict now — the
candidate comparison touches no model. The solver's Python runs for real in the
subprocess sandbox.

Invariant under test: the compute-check is RECALL-ONLY — accept -> reject is the
only override; it never turns a reject into an accept, and abstains whenever
there's no solver consensus or no candidate number, preserving ref-accept = 1.0.
"""
from __future__ import annotations

import asyncio

from backend.orchestrator.workers.base import WorkerEvent
from backend.orchestrator.routing.tool_judge import (
    SOLVER_PROMPT,
    parse_number,
    parse_solver_answer,
    extract_ordered_numbers,
    run_solver_code,
    _consensus,
    solve_consensus,
    compute_check,
    tool_augmented_judge,
)


class _ScriptedWorker:
    """Routes by goal content: solver call vs base verdict. `solver` may be a str
    (same each call) or a list (cycled) to simulate (dis)agreement."""

    id = "fake:tooljudge"

    def __init__(self, *, base: str = "", solver=""):
        self._base = base
        self._solver = solver
        self._i = 0
        self.calls: list[str] = []

    def _next_solver(self) -> str:
        if isinstance(self._solver, (list, tuple)):
            v = self._solver[self._i % len(self._solver)]
            self._i += 1
            return v
        return self._solver

    async def execute(self, attempt, task, feedback=None):
        if SOLVER_PROMPT[:40] in task.goal:
            kind, reply = "solver", self._next_solver()
        else:
            kind, reply = "base", self._base
        self.calls.append(kind)
        yield WorkerEvent(type="metrics", payload={"cost_usd": 0.0})
        yield WorkerEvent(type="attempt.completed", payload={"summary": reply})

    def clear_history(self, task_id: str) -> None:
        pass


# ----- pure parsing helpers -----

def test_parse_number_forms():
    assert parse_number("5/14") == 5 / 14
    assert parse_number("about 0.357") == 0.357
    assert parse_number("$66.00") == 66.0
    assert parse_number("no number") is None


def test_extract_ordered_numbers_folds_fractions_without_doubling():
    # "3/12" must yield 0.25 only — NOT a stray 3 and 12 — else an intermediate
    # would spuriously contain a final answer of 3.
    got = extract_ordered_numbers("1/4 + 1/6 = 3/12 = 0.25 tank, so 2.4 hours or 2 hours 24 min")
    assert 3.0 not in got and 12.0 not in got
    assert got[-3:] == [2.4, 2.0, 24.0]        # the conclusion's numbers
    assert extract_ordered_numbers("no numbers") == []


def test_parse_solver_answer_last_wins():
    assert parse_solver_answer("noise\nANSWER: 0.357\n") == "0.357"
    assert parse_solver_answer("ANSWER: 1\nANSWER: 3") == "3"
    assert parse_solver_answer("nothing") is None


def test_consensus_rules():
    assert _consensus([0.357, 0.3571, 0.356], 0.02) is not None
    assert _consensus([0.357], 0.02) is None            # single -> abstain
    assert _consensus([], 0.02) is None
    assert _consensus([3.0, 0.6, 3.0], 0.02) is not None  # majority holds
    assert _consensus([3.0, 0.6], 0.02) is None           # tie -> abstain


# ----- sandbox -----

def test_run_solver_code_success_and_failures():
    out = asyncio.run(run_solver_code("print('ANSWER:', 5/14)"))
    assert out is not None and parse_number(parse_solver_answer(out)) == 5 / 14
    assert asyncio.run(run_solver_code("")) is None
    assert asyncio.run(run_solver_code("def (:")) is None
    assert asyncio.run(run_solver_code("raise SystemExit(1)")) is None


# ----- solve_consensus -----

def test_solve_consensus_agrees():
    w = _ScriptedWorker(solver="print('ANSWER:', 5/14)")
    val, _ = asyncio.run(solve_consensus(w, "prob task", k=5))
    assert val is not None and abs(val - 5 / 14) < 1e-6


def test_solve_consensus_abstains_on_contradiction():
    w = _ScriptedWorker(solver=["print('ANSWER:', 3.0)", "print('ANSWER:', 0.6)"])
    assert asyncio.run(solve_consensus(w, "prob task", k=2))[0] is None


def test_solve_consensus_abstains_when_not_computable():
    w = _ScriptedWorker(solver="NOT_COMPUTABLE")
    assert asyncio.run(solve_consensus(w, "explain sky", k=5))[0] is None


# ----- compute_check (deterministic candidate side) -----

def test_compute_check_catches_wrong_conclusion():
    w = _ScriptedWorker(solver="print('ANSWER:', 5/14)")  # 0.357
    verdict, _c, detail = asyncio.run(compute_check(w, "prob task", "20/64 = 5/16, about 0.31"))
    assert verdict is False and "disagrees" in detail


def test_compute_check_not_fooled_by_matching_intermediate():
    # candidate's conclusion is 2.4 (wrong) though it contains "3/12"; computed = 3.
    w = _ScriptedWorker(solver="print('ANSWER:', 3)")
    verdict, _c, _d = asyncio.run(compute_check(w, "rate task", "3/12 = 0.25 ... Time = 2.4 hours"))
    assert verdict is False  # last-K sees 2.4, not the stray 3


def test_compute_check_abstains_on_agreement_never_true():
    w = _ScriptedWorker(solver="print('ANSWER:', 5/14)")
    verdict, _c, _d = asyncio.run(compute_check(w, "prob task", "20/56 = 5/14, about 0.357"))
    assert verdict is None


def test_compute_check_abstains_without_candidate_number():
    w = _ScriptedWorker(solver="print('ANSWER:', 5/14)")
    verdict, _c, detail = asyncio.run(compute_check(w, "prob task", "roughly a third, I think"))
    assert verdict is None and "no candidate number" in detail


def test_compute_check_abstains_without_consensus():
    w = _ScriptedWorker(solver=["print('ANSWER:', 3.0)", "print('ANSWER:', 0.6)"])
    verdict, _c, detail = asyncio.run(compute_check(w, "prob task", "2.4 hours", k=2))
    assert verdict is None and "no solver consensus" in detail


# ----- tool_augmented_judge: recall-only invariant -----

def test_override_accept_to_reject_when_tool_catches():
    w = _ScriptedWorker(base='{"correct": true}', solver="print('ANSWER:', 5/14)")
    verdict, _c, detail = asyncio.run(tool_augmented_judge(w, "prob task", "5/16, about 0.31"))
    assert verdict is False and "tool override" in detail


def test_no_override_when_tool_abstains():
    w = _ScriptedWorker(base='{"correct": true}', solver="NOT_COMPUTABLE")
    verdict, _c, _d = asyncio.run(tool_augmented_judge(w, "explain sky", "correct answer"))
    assert verdict is True


def test_tool_never_softens_a_base_reject():
    w = _ScriptedWorker(base='{"correct": false}', solver="print('ANSWER:', 5/14)")
    verdict, _c, _d = asyncio.run(tool_augmented_judge(w, "prob task", "5/14, about 0.357"))
    assert verdict is False
