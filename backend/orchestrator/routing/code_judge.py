"""
code_judge.py — a generated-test differential judge for CODE tasks.

Why this exists (findings.md Era 19): on HumanEval+ the plain reading-judge's
fail-recall was 0.688 — 10 wrong answers served. granite's real failures there
are plausible, compiling, subtly-wrong implementations, exactly the regime where
judgment-by-reading saturates. The code-domain twin of Era 18's lesson: the fix
is a TOOL that manufactures the hidden test the judge lacks.

For code the tool is strictly stronger than the text case's solver, because the
expected output can be COMPUTED by executed code instead of stated by a model:

  1. GENERATE — the judge model, from the task prompt ALONE (it never sees the
     candidate's code or the bank's hidden tests), writes K independent
     reference implementations, each with a CASES list of test INPUTS.
  2. EXECUTE — every reference and the candidate run on the pooled inputs in
     the same subprocess sandbox as tool_judge's solver. The expected output
     for an input is the EXECUTED CONSENSUS of the references (>=2 agree and a
     strict majority of the comparable runs).
  3. COMPARE — deterministic: `ast.literal_eval` of the printed reprs, checked
     with float tolerance. Per Era 18 v1/v2, no LLM sits anywhere in the
     compare path — no model states an expected output, none reads the
     candidate.

INVARIANT (same as tool_judge.py, enforced structurally): RECALL-ONLY.
`differential_check` can only return False (the candidate contradicts an
executed consensus) or None (ABSTAIN on any failure: no entrypoint in the
prompt, <2 runnable references, no consensus input, sandbox timeout). The
wrapper may flip a base accept -> reject, never the reverse. A wrong consensus
(K references sharing a bug) is therefore an over-escalation to cloud — money,
not a served wrong answer (the Era 18 reframe).

SECURITY: runs model-generated Python in a short-timeout subprocess — the same
posture as execution_gate.py / tool_judge.py. Local single-user context is the
intended use.
"""
from __future__ import annotations

import ast
import math
import re
from typing import Any, Optional

from ..workers.postprocess import extract_code
from .judge_gate import JUDGE_RUBRIC, judge_one, run_text
from .tool_judge import run_solver_code

GEN_SAMPLES = 3            # independent (reference + CASES) generations per task
MAX_CASES = 20             # pooled input cap across generations
# Reject only on >=2 disagreeing consensus inputs. One disagreement in ~20 is
# noise-shaped (an out-of-contract or ambiguous generated input); two
# independent ones are systematic — the same "never act on a single piece of
# evidence" posture as tool_judge's >=2-of-K solver consensus. Calibrated on
# the recorded HumanEval+ replay (catches disagreed on 15/4/2 inputs, 6 of 9
# false alarms on exactly 1, including the only cloud-fail row).
MIN_DISAGREEMENTS = 2
FLOAT_RTOL = 1e-6
FLOAT_ATOL = 1e-9
_HARNESS_TIMEOUT_SECONDS = 10   # matches the bank builder's verify timeout
_DUMP_TIMEOUT_SECONDS = 5
_MAX_PROGRAM_CHARS = 8000
_MAX_CASE_REPR_CHARS = 2000

GEN_PROMPT = (
    "You are given a programming task that asks for a Python function. Write a "
    "single Python code block containing TWO things:\n"
    "1. A complete, correct, self-contained implementation of the required "
    "function — the exact function name and signature the task asks for, plus "
    "any imports it needs. Solve the task from scratch.\n"
    "2. After the function, a module-level variable CASES: a list where each "
    "item is a list of positional arguments for ONE call to the function. Give "
    "6-10 diverse inputs: typical cases, boundaries (empty, single element, "
    "zero, negatives, duplicates), and any tricky case the task's wording hints "
    "at. Use ONLY inputs that satisfy the task's stated constraints, and only "
    "plain literals (numbers, strings, booleans, None, lists, tuples, dicts).\n"
    "Do not call the function at top level and do not print anything. Output "
    "ONLY the code block — no prose, no explanation."
)

_PROMPT_DEF_RE = re.compile(r"^\s*def\s+([A-Za-z_]\w*)\s*\(", re.MULTILINE)
_CASE_LINE_RE = re.compile(r"^__CJ__ (\d+) (OK|ERR) ?(.*)$", re.MULTILINE)
_CASES_DUMP_SNIPPET = '\n\nprint("__CJ_CASES__", repr(CASES))\n'
_CASES_DUMP_RE = re.compile(r"^__CJ_CASES__ (.*)$", re.MULTILINE)

# Appended after the code under test; CASES is overwritten with the pooled
# inputs so every reference and the candidate see the identical case list.
# deepcopy guards a function that mutates its arguments; BaseException so a
# per-case sys.exit cannot kill the remaining cases. repr never emits a raw
# newline, so each outcome is one parseable line.
_RUNNER_TEMPLATE = """

CASES = {cases_literal}

def __cj_main():
    import copy as _copy
    for _i, _args in enumerate(CASES):
        try:
            _r = {entrypoint}(*_copy.deepcopy(list(_args)))
            print("__CJ__", _i, "OK", repr(_r))
        except BaseException as _e:
            print("__CJ__", _i, "ERR", type(_e).__name__)

__cj_main()
"""


def _defined_functions(code: str) -> set[str]:
    """Top-level function names in `code`; empty set if it doesn't parse."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return set()
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def extract_entrypoint(task_prompt: str, candidate_code: str) -> Optional[str]:
    """The function under test, derived deterministically or not at all.

    The name must come from the TASK PROMPT (a stub/signature the prompt shows)
    so that independently generated references implement the same callable; of
    the prompt's `def` names the candidate actually defines, the LAST wins (a
    stub that includes a helper lists the target function last). None -> the
    caller abstains — a prompt with no visible signature is out of scope for
    the differential check, by design.
    """
    prompt_names = _PROMPT_DEF_RE.findall(task_prompt or "")
    if not prompt_names:
        return None
    candidate_names = _defined_functions(candidate_code)
    shared = [name for name in prompt_names if name in candidate_names]
    return shared[-1] if shared else None


def values_equal(a: Any, b: Any, *, rtol: float = FLOAT_RTOL, atol: float = FLOAT_ATOL) -> bool:
    """Deterministic structural equality with float tolerance.

    Numbers compare via isclose (bool==int keeps Python semantics); lists and
    tuples cross-compare elementwise (a correct arm returning a tuple where the
    references return a list is not a defect worth an escalation); dicts by
    keys+values; everything else by ==.
    """
    if isinstance(a, bool) != isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=rtol, abs_tol=atol)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(
            values_equal(x, y, rtol=rtol, atol=atol) for x, y in zip(a, b)
        )
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(
            values_equal(v, b[k], rtol=rtol, atol=atol) for k, v in a.items()
        )
    return a == b


_NO_CONSENSUS = object()


def value_consensus(values: list[Any]) -> Any:
    """The agreed expected output, or _NO_CONSENSUS.

    Same posture as tool_judge._consensus: the largest values_equal-cluster
    must have >=2 members AND a strict majority of the comparable runs, so one
    contradicting reference blocks consensus (invariant over catch). Returns
    the sentinel, not None — None is a legitimate expected output.
    """
    if len(values) < 2:
        return _NO_CONSENSUS
    best: list[Any] = []
    anchor_of_best: Any = _NO_CONSENSUS
    for anchor in values:
        cluster = [v for v in values if values_equal(anchor, v)]
        if len(cluster) > len(best):
            best = cluster
            anchor_of_best = anchor
    if len(best) < 2 or len(best) <= len(values) - len(best):
        return _NO_CONSENSUS
    return anchor_of_best


def parse_outcomes(stdout: str) -> dict[int, tuple[str, str]]:
    """`__CJ__ <i> OK <repr>` / `__CJ__ <i> ERR <ExcType>` lines -> {i: (kind, payload)}."""
    out: dict[int, tuple[str, str]] = {}
    for m in _CASE_LINE_RE.finditer(stdout or ""):
        out[int(m.group(1))] = (m.group(2), m.group(3).strip())
    return out


async def dump_cases(code: str) -> Optional[list]:
    """Execute a generation's own code to serialize its CASES as literals.

    Running the code (rather than parsing the assignment) lets a generation
    build inputs programmatically; `literal_eval` of the printed repr then
    guarantees everything pooled is literal-safe, so the identical list can be
    re-embedded in every harness. Any failure -> None (this generation simply
    contributes no inputs; it may still vote as a reference).
    """
    stdout = await run_solver_code(code + _CASES_DUMP_SNIPPET, timeout=_DUMP_TIMEOUT_SECONDS)
    if stdout is None:
        return None
    matches = _CASES_DUMP_RE.findall(stdout)
    if not matches:
        return None
    try:
        cases = ast.literal_eval(matches[-1].strip())
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None
    if not isinstance(cases, list):
        return None
    return [c for c in cases if isinstance(c, (list, tuple))]


async def differential_check(
    worker, task_prompt: str, candidate: str, *, k: int = GEN_SAMPLES,
    min_disagreements: int = MIN_DISAGREEMENTS,
) -> tuple[Optional[bool], float, str]:
    """Generated-test differential check. Returns (verdict, cost_usd, detail).

    verdict:
      False — the candidate disagrees with an executed reference consensus on
              at least `min_disagreements` generated inputs (a catch);
      None  — ABSTAIN: no prompt entrypoint, <2 usable references, no
              consensus input, disagreements below the reject threshold, or
              the candidate agrees everywhere. Never True — agreement on
              generated inputs does not prove correctness, so the base judge
              remains the sole accept.

    The candidate's code is executed, never re-read by a model; hidden bank
    tests are not accepted by this signature at all.
    """
    candidate_code = extract_code(candidate or "").strip()
    entrypoint = extract_entrypoint(task_prompt, candidate_code)
    if entrypoint is None:
        return None, 0.0, "no shared entrypoint in prompt+candidate"

    cost = 0.0
    references: list[str] = []
    pooled: list[Any] = []
    seen: set[str] = set()
    for _ in range(k):
        gen_out, c, err = await run_text(worker, f"{GEN_PROMPT}\n\n## Task\n{task_prompt}")
        cost += c
        if err or not gen_out:
            continue
        code = extract_code(gen_out).strip()
        if not code or len(code) > _MAX_PROGRAM_CHARS:
            continue
        if entrypoint not in _defined_functions(code):
            continue  # didn't implement the asked function -> cannot vote
        references.append(code)
        for case in await dump_cases(code) or []:
            case_repr = repr(case)
            if len(case_repr) <= _MAX_CASE_REPR_CHARS and case_repr not in seen:
                seen.add(case_repr)
                pooled.append(case)
    pooled = pooled[:MAX_CASES]
    if len(references) < 2 or not pooled:
        return None, cost, (
            f"insufficient material ({len(references)} references, {len(pooled)} cases)"
        )

    runner = _RUNNER_TEMPLATE.format(cases_literal=repr(pooled), entrypoint=entrypoint)
    reference_outcomes: list[dict[int, tuple[str, str]]] = []
    for code in references:
        stdout = await run_solver_code(code + runner, timeout=_HARNESS_TIMEOUT_SECONDS)
        if stdout is not None:
            reference_outcomes.append(parse_outcomes(stdout))
    if len(reference_outcomes) < 2:
        return None, cost, "references failed to run"

    candidate_stdout = await run_solver_code(candidate_code + runner, timeout=_HARNESS_TIMEOUT_SECONDS)
    if candidate_stdout is None:
        return None, cost, "candidate harness failed"  # timeout/junk -> abstain
    candidate_outcomes = parse_outcomes(candidate_stdout)

    checked = 0
    mismatches: list[tuple[int, Any, Any]] = []
    for i in range(len(pooled)):
        votes: list[Any] = []
        for outcomes in reference_outcomes:
            kind, payload = outcomes.get(i, ("MISSING", ""))
            if kind != "OK":
                continue  # an erroring reference cannot vote a value
            try:
                votes.append(ast.literal_eval(payload))
            except (SyntaxError, ValueError, MemoryError, RecursionError):
                continue  # non-literal repr cannot be compared deterministically
        expected = value_consensus(votes)
        if expected is _NO_CONSENSUS:
            continue
        checked += 1
        kind, payload = candidate_outcomes.get(i, ("MISSING", ""))
        if kind != "OK":
            mismatches.append((i, expected, f"<{kind}: {payload}>"))
            continue
        try:
            got = ast.literal_eval(payload)
        except (SyntaxError, ValueError, MemoryError, RecursionError):
            continue  # incomparable output -> protect the invariant, skip
        if not values_equal(expected, got):
            mismatches.append((i, expected, got))

    if checked == 0:
        return None, cost, "no consensus inputs"
    if len(mismatches) >= min_disagreements:
        i, expected, got = mismatches[0]
        return False, cost, (
            f"{len(mismatches)}/{checked} consensus inputs disagree — "
            f"e.g. {entrypoint}(*{pooled[i]!r}): expected {expected!r}, got {got!r}"
        )
    if mismatches:
        return None, cost, (
            f"below reject threshold: {len(mismatches)}/{checked} consensus inputs "
            f"disagree (min {min_disagreements})"
        )
    return None, cost, f"agrees on {checked} consensus inputs"


async def code_augmented_judge(
    worker, task_prompt: str, candidate: str, *, rubric: str = JUDGE_RUBRIC, k: int = GEN_SAMPLES
) -> tuple[Optional[bool], float, str]:
    """Base judge + recall-only differential check. Returns (verdict, cost, detail).

    The base verdict stands unless the differential check catches an executed
    disagreement on an answer the base judge ACCEPTED — the only override
    allowed (accept -> reject). A base reject/abstain already escalates, so the
    tool is skipped there entirely: recall-only makes its output unusable, and
    K generations are the expensive part of this judge.
    """
    base_verdict, base_cost, _reply, _err = await judge_one(
        worker, task_prompt, candidate, rubric=rubric
    )
    if base_verdict is not True:
        return base_verdict, base_cost, "base: reject/abstain (tool skipped — recall-only)"
    tool_verdict, tool_cost, detail = await differential_check(worker, task_prompt, candidate, k=k)
    total_cost = base_cost + tool_cost
    if tool_verdict is False:
        return False, total_cost, f"tool override: {detail}"
    tag = "base" if tool_verdict is None else "tool-confirmed"
    return True, total_cost, f"{tag}: {detail}"
