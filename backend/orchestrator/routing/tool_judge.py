"""
tool_judge.py — a compute-augmented correctness judge for NON-VERIFIABLE tasks.

Why this exists (findings.md Era 16/17): a free local judge (qwen3.5) never
falsely rejects a correct answer but is blind to a specific residual — wrong
*numbers* it cannot recompute, and dropped requirements. A second independent
model family (granite) does NOT close it: the Era-17 overlap join caught 0 of 4
quantity mutants with *either* family, so no local-judge ensemble is the answer.
The only lever left is to give the judge a TOOL.

This module adds the first tool: a COMPUTE-CHECK for tasks whose answer is a
computable quantity (probability, arithmetic, rate problems). Rather than the
fragile "extract the candidate's number and diff it" (a mutant ending
"...2.4 hours, or 2 hours 24 minutes" defeats naive regex), it works in two
steps:

  1. TOOL — ask the judge model to emit a self-contained Python program that
     solves the task and prints `ANSWER: <value>`, then run it in the same
     subprocess sandbox as execution_gate.py. This manufactures the hidden test
     that non-verifiable tasks lack.
  2. GROUND — feed that executed answer back into a focused verdict call: "an
     independent computation gives X; does the candidate's final answer agree?"
     The model does the easy semantic match; the *number* comes from run code,
     not the model's belief.

INVARIANT (preserves Era 16's ref-accept = 1.0): the compute-check is
RECALL-ONLY. It may flip a base verdict accept -> reject (catch a wrong number
the base judge missed) but NEVER reject -> accept, and it ABSTAINS unless the
solver produced a clean number to compare against. A buggy or empty solver
therefore degrades to "no opinion", never a false rejection. That property is
measured against the bank's references, not assumed.

SECURITY: runs model-generated Python in a subprocess with a short timeout —
the same posture as execution_gate.py / tools/code_exec.py. Local single-user
context is the intended use.
"""
from __future__ import annotations

import asyncio
import ast
import re
from typing import Optional

from ..workers.postprocess import extract_code
from .judge_gate import GENERAL_RUBRIC, judge_one, run_text

_SOLVER_TIMEOUT_SECONDS = 8

SOLVER_PROMPT = (
    "You are given a task whose answer is a single computable quantity. Write a "
    "SHORT, self-contained Python 3 program that solves the task FROM SCRATCH by "
    "computing (do NOT hard-code the answer) and, as its last line of output, "
    "prints exactly:\n"
    "    ANSWER: <value>\n"
    "where <value> is the final numeric result (a plain number; include units "
    "only if the task's answer is inherently a unit like hours). Use only the "
    "Python standard library. If the task is NOT a self-contained computation "
    "(e.g. it needs an external fact you would have to look up, or it is not "
    "quantitative), output the single line:\n"
    "    NOT_COMPUTABLE\n"
    "Output ONLY the program or that single line — no prose, no explanation."
)


# Hardening (findings Era 18). Two live iterations showed the invariant
# (ref-accept = 1.0) breaks whenever an LLM sits between the executed answer and
# the candidate's prose: v1's "do they agree?" call was pedantic ("approximately
# correct but lacks precision" on an exact match); v2's "extract the number" call
# misread 0.357 as 0.3. Both failed on the same row. So v3 removes the LLM from
# BOTH sides of the compare:
#   - SOLVER side: sample K times, trust the number only if runs AGREE (a single
#     shot was ~1/3 reliable);
#   - CANDIDATE side: parse the candidate's numbers deterministically and check
#     the executed answer against its LAST few (the conclusion), NOT all of them
#     — an intermediate like "3/12" spuriously contains the final answer "3".
SOLVER_SAMPLES = 5
NUMERIC_RTOL = 0.02   # tight: distinguishes $66 from $68 (3%), tolerates rounding
LAST_K = 3            # compare against the candidate's last K numbers (its answer)

_ANSWER_RE = re.compile(r"ANSWER:\s*(.+)", re.IGNORECASE)
_FRACTION_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
# fraction OR bare number, scanned left-to-right so a fraction is consumed whole
# (its components are not re-emitted as separate numbers).
_TOKEN_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)|(-?\d+(?:\.\d+)?)")


def parse_number(text: Optional[str]) -> Optional[float]:
    """First numeric value in `text` as a float — fraction `a/b`, else a plain
    number. Strips currency/percent/commas. None if nothing parses."""
    if not text:
        return None
    cleaned = text.replace(",", "").replace("$", "").replace("%", " ")
    fm = _FRACTION_RE.search(cleaned)
    if fm:
        num, den = float(fm.group(1)), float(fm.group(2))
        return num / den if den else None
    nm = _NUMBER_RE.search(cleaned)
    return float(nm.group(0)) if nm else None


def extract_ordered_numbers(text: Optional[str]) -> list[float]:
    """All numeric values in `text`, in order, fractions folded to decimals and
    their components NOT double-counted (so "3/12" yields 0.25, not 3 and 12)."""
    if not text:
        return []
    cleaned = text.replace(",", "").replace("$", "")
    out: list[float] = []
    for m in _TOKEN_RE.finditer(cleaned):
        if m.group(1) is not None:  # fraction a/b
            den = float(m.group(2))
            if den:
                out.append(float(m.group(1)) / den)
        else:  # bare number
            out.append(float(m.group(3)))
    return out


def _rel_diff(a: float, b: float) -> float:
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom


def _consensus(values: list[float], rtol: float) -> Optional[float]:
    """Trust a number only if ≥2 successful solver runs AGREE within `rtol` and
    no surviving run contradicts them. Ignores failed runs (None already dropped).
    A solver that flips between answers yields no consensus -> None (abstain)."""
    if len(values) < 2:
        return None
    # largest rtol-cluster; require it to hold a strict majority of successes so
    # a contradicting run blocks consensus (invariant over catch).
    best: list[float] = []
    for anchor in values:
        cluster = [v for v in values if _rel_diff(v, anchor) <= rtol]
        if len(cluster) > len(best):
            best = cluster
    if len(best) < 2 or len(best) <= len(values) - len(best):
        return None
    return sum(best) / len(best)


def parse_solver_answer(stdout: str) -> Optional[str]:
    """Pull the `ANSWER: <value>` line from solver stdout; None if absent/empty.

    Takes the LAST match so debug prints before the final answer don't win.
    """
    matches = _ANSWER_RE.findall(stdout or "")
    if not matches:
        return None
    val = matches[-1].strip()
    return val or None


async def run_solver_code(code: str, timeout: int = _SOLVER_TIMEOUT_SECONDS) -> Optional[str]:
    """Run a solver program under python3, return stdout (or None on any failure).

    Mirrors execution_gate.check_executes' sandbox posture: parse first, then a
    short-timeout subprocess. Any failure (no code, syntax error, nonzero exit,
    timeout, spawn error) returns None so the caller abstains.
    """
    code = (code or "").strip()
    if not code:
        return None
    try:
        ast.parse(code)
    except SyntaxError:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception:  # pragma: no cover - spawn failure is environmental
        return None
    try:
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return None
    if proc.returncode != 0:
        return None
    return out.decode(errors="replace")


async def solve_consensus(
    worker, task_prompt: str, *, k: int = SOLVER_SAMPLES, rtol: float = NUMERIC_RTOL
) -> tuple[Optional[float], float]:
    """Sample the solver `k` times, run each, and return (consensus_value, cost).

    Only successful, numeric runs vote; the value is trusted only if they reach
    `_consensus`. Returns (None, cost) when the task isn't a self-contained
    computation or the runs don't agree."""
    cost = 0.0
    values: list[float] = []
    not_computable = 0
    for _ in range(k):
        solver_out, c, err = await run_text(worker, f"{SOLVER_PROMPT}\n\n## Task\n{task_prompt}")
        cost += c
        if err or not solver_out:
            continue
        if "NOT_COMPUTABLE" in solver_out and not _ANSWER_RE.search(solver_out):
            not_computable += 1
            # The model's own "this isn't a computation" signal, twice with no
            # number yet -> stop paying for a task it says can't be computed
            # (prose/factual-lookup); flaky-but-attempting solvers never hit this.
            if not_computable >= 2 and not values:
                break
            continue
        stdout = await run_solver_code(extract_code(solver_out))
        v = parse_number(parse_solver_answer(stdout)) if stdout is not None else None
        if v is not None:
            values.append(v)
    return _consensus(values, rtol), cost


async def compute_check(
    worker, task_prompt: str, candidate: str, *, k: int = SOLVER_SAMPLES, rtol: float = NUMERIC_RTOL
) -> tuple[Optional[bool], float, str]:
    """Tool-augmented numeric check. Returns (verdict, cost_usd, detail).

    verdict:
      False  — a self-consistent solver computed a clean answer and the
               candidate's extracted number DISAGREES beyond `rtol` (a catch);
      None   — ABSTAIN: no solver consensus, no clean candidate number, or the
               numbers agree. Never returns True — agreement does not prove
               overall correctness, so the base judge remains the sole accept.

    v3 (Era 18): no LLM on either side. The solver number must survive
    `solve_consensus`; the candidate's number is parsed deterministically and the
    executed answer is checked against its LAST `LAST_K` numbers (the conclusion),
    so an intermediate step that happens to contain the final value doesn't hide
    a wrong conclusion, and no extraction model can misread the candidate.
    """
    computed, cost = await solve_consensus(worker, task_prompt, k=k, rtol=rtol)
    if computed is None:
        return None, cost, "no solver consensus"

    tail = extract_ordered_numbers(candidate)[-LAST_K:]
    if not tail:
        return None, cost, f"no candidate number (computed={computed:g})"
    if any(_rel_diff(computed, v) <= rtol for v in tail):
        return None, cost, f"agrees (computed={computed:g} in {[round(v, 4) for v in tail]})"
    return False, cost, (
        f"candidate {[round(v, 4) for v in tail]} disagrees with computed {computed:g}"
    )


async def tool_augmented_judge(
    worker, task_prompt: str, candidate: str, *, rubric: str = GENERAL_RUBRIC
) -> tuple[Optional[bool], float, str]:
    """Base judge + recall-only compute-check. Returns (verdict, cost_usd, detail).

    The base `GENERAL_RUBRIC` verdict stands unless the compute-check catches a
    numeric disagreement on an answer the base judge ACCEPTED — the only
    override allowed (accept -> reject). A base reject is never softened, and an
    abstaining tool leaves the base verdict untouched.
    """
    base_verdict, base_cost, _reply, base_err = await judge_one(
        worker, task_prompt, candidate, rubric=rubric
    )
    tool_verdict, tool_cost, detail = await compute_check(worker, task_prompt, candidate)
    total_cost = base_cost + tool_cost
    if base_verdict is True and tool_verdict is False:
        return False, total_cost, f"tool override: {detail}"
    tag = "base" if tool_verdict is None else "tool-confirmed"
    return base_verdict, total_cost, f"{tag}: {detail}"
