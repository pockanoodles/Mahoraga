"""Unit tests for the generated-test differential judge (code_judge).

code_judge is the code-domain twin of tool_judge (findings Era 19): the judge
model writes K independent reference implementations + CASES from the task
prompt ALONE, everything runs in the real subprocess sandbox, and the expected
output per input is the EXECUTED CONSENSUS of the references — no LLM anywhere
in the compare path.

The fake worker only ever serves the reference generations and the base
verdict — the candidate's code is executed, never re-read by a model. The
references and the candidate run for real in the subprocess sandbox.

Invariant under test: the differential check is RECALL-ONLY — accept -> reject
is the only override; `differential_check` never returns True, a base reject
skips the tool entirely, and every failure path (no prompt entrypoint, <2
references, no consensus input) ABSTAINS, preserving ref-accept = 1.0.
"""
from __future__ import annotations

from backend.orchestrator.workers.base import WorkerEvent
from backend.orchestrator.routing import code_judge
from backend.orchestrator.routing.code_judge import (
    GEN_PROMPT,
    extract_entrypoint,
    values_equal,
    value_consensus,
    parse_outcomes,
    dump_cases,
    differential_check,
    code_augmented_judge,
)


class _ScriptedWorker:
    """Routes by goal content: reference-generation call vs base verdict. `gen`
    may be a str (same each call) or a list (cycled) to simulate flaky output."""

    id = "fake:codejudge"

    def __init__(self, *, base: str = "", gen=""):
        self._base = base
        self._gen = gen
        self._i = 0
        self.calls: list[str] = []

    def _next_gen(self) -> str:
        if isinstance(self._gen, (list, tuple)):
            v = self._gen[self._i % len(self._gen)]
            self._i += 1
            return v
        return self._gen

    async def execute(self, attempt, task, feedback=None):
        if GEN_PROMPT[:40] in task.goal:
            kind, reply = "gen", self._next_gen()
        else:
            kind, reply = "base", self._base
        self.calls.append(kind)
        yield WorkerEvent(type="metrics", payload={"cost_usd": 0.0})
        yield WorkerEvent(type="attempt.completed", payload={"summary": reply})

    def clear_history(self, task_id: str) -> None:
        pass


# A trivially verifiable task: the prompt shows the stub, so extract_entrypoint
# can bind the callable, and correctness is decidable on tiny int inputs.
_TASK_PROMPT = (
    "Complete the function:\n"
    "```python\n"
    "def double(x):\n"
    "```\n"
    "Return double of x."
)

_GOOD_GEN = (
    "```python\n"
    "def double(x):\n"
    "    return x * 2\n"
    "\n"
    "CASES = [[1], [2], [-3], [0]]\n"
    "```"
)

_CANDIDATE_CORRECT = "```python\ndef double(x):\n    return x * 2\n```"
_CANDIDATE_WRONG = "```python\ndef double(x):\n    return x * 2 + 1\n```"
_CANDIDATE_CRASHES = "```python\ndef double(x):\n    raise ValueError('boom')\n```"


# ----- extract_entrypoint (pure) -----

def test_extract_entrypoint_from_prompt_stub():
    assert extract_entrypoint(_TASK_PROMPT, "def double(x):\n    return x * 2") == "double"


def test_extract_entrypoint_last_prompt_name_wins():
    # a stub that shows a helper lists the target function last
    prompt = "```python\ndef helper(y):\n    ...\n\ndef target(x):\n    ...\n```"
    cand = "def helper(y):\n    return y\n\ndef target(x):\n    return helper(x)"
    assert extract_entrypoint(prompt, cand) == "target"


def test_extract_entrypoint_abstains_without_prompt_def():
    assert extract_entrypoint("Just double x, no signature shown.", "def foo(x):\n    return x") is None


def test_extract_entrypoint_abstains_when_candidate_lacks_prompt_names():
    assert extract_entrypoint(_TASK_PROMPT, "def bar(x):\n    return x * 2") is None


def test_extract_entrypoint_abstains_on_invalid_candidate():
    assert extract_entrypoint(_TASK_PROMPT, "def double(:") is None


# ----- values_equal (pure) -----

def test_values_equal_float_tolerance():
    assert values_equal(0.1 + 0.2, 0.3)


def test_values_equal_list_tuple_cross_compare():
    assert values_equal([1, 2.0], (1, 2))
    assert not values_equal([1, 2], (2, 1))


def test_values_equal_nested_dict():
    assert values_equal({"a": [0.1 + 0.2], "b": {"c": 1}}, {"a": (0.3,), "b": {"c": 1.0}})
    assert not values_equal({"a": 1}, {"a": 2})


def test_values_equal_bool_vs_int_keeps_python_semantics():
    assert values_equal(True, 1)   # True == 1
    assert not values_equal(True, 2)


def test_values_equal_strings_exact():
    assert values_equal("abc", "abc")
    assert not values_equal("abc", "abd")


def test_values_equal_length_mismatch():
    assert not values_equal([1, 2], [1, 2, 3])


def test_values_equal_distinct_ints():
    assert not values_equal(66, 68)


# ----- value_consensus (pure) -----

def test_value_consensus_majority_wins():
    assert value_consensus([1, 1, 2]) == 1


def test_value_consensus_abstains():
    sentinel = code_judge._NO_CONSENSUS
    assert value_consensus([1, 2]) is sentinel            # tie -> abstain
    assert value_consensus([1, 1, 2, 2]) is sentinel      # no strict majority
    assert value_consensus([]) is sentinel
    assert value_consensus([1]) is sentinel               # single -> abstain


def test_value_consensus_none_is_a_value_not_the_sentinel():
    got = value_consensus([None, None])
    assert got is None and got is not code_judge._NO_CONSENSUS


# ----- parse_outcomes (pure) -----

def test_parse_outcomes_ok_and_err():
    got = parse_outcomes("__CJ__ 0 OK 42\n__CJ__ 1 ERR ValueError")
    assert got == {0: ("OK", "42"), 1: ("ERR", "ValueError")}


def test_parse_outcomes_ignores_junk_lines():
    got = parse_outcomes("warming up\n__CJ__ x OK 1\n__CJ__ 0 OK [1, 2]\ntrailer")
    assert got == {0: ("OK", "[1, 2]")}


def test_parse_outcomes_last_duplicate_index_wins():
    got = parse_outcomes("__CJ__ 0 OK 1\n__CJ__ 0 OK 2")
    assert got == {0: ("OK", "2")}


# ----- dump_cases (real sandbox) -----

async def test_dump_cases_literal_list():
    code = "def f(x):\n    return x\n\nCASES = [[1], [2]]"
    assert await dump_cases(code) == [[1], [2]]


async def test_dump_cases_missing_cases_variable():
    assert await dump_cases("def f(x):\n    return x") is None


async def test_dump_cases_programmatic_cases():
    code = "def f(x):\n    return x\n\nCASES = [[i] for i in range(3)]"
    assert await dump_cases(code) == [[0], [1], [2]]


async def test_dump_cases_filters_non_list_items():
    code = "def f(x):\n    return x\n\nCASES = [[1], 'junk', [2]]"
    assert await dump_cases(code) == [[1], [2]]


# ----- differential_check (end-to-end, real sandbox) -----

async def test_differential_abstains_on_correct_candidate_never_true():
    w = _ScriptedWorker(gen=_GOOD_GEN)
    verdict, _c, detail = await differential_check(w, _TASK_PROMPT, _CANDIDATE_CORRECT)
    assert verdict is None and "agrees" in detail  # agreement != proof -> abstain


async def test_differential_catches_subtly_wrong_candidate():
    w = _ScriptedWorker(gen=_GOOD_GEN)
    verdict, _c, detail = await differential_check(w, _TASK_PROMPT, _CANDIDATE_WRONG)
    assert verdict is False and "disagree" in detail and "expected" in detail


async def test_differential_catches_candidate_that_crashes():
    w = _ScriptedWorker(gen=_GOOD_GEN)
    verdict, _c, detail = await differential_check(w, _TASK_PROMPT, _CANDIDATE_CRASHES)
    assert verdict is False and "ValueError" in detail


async def test_differential_abstains_without_entrypoint():
    w = _ScriptedWorker(gen=_GOOD_GEN)
    verdict, _c, detail = await differential_check(
        w, _TASK_PROMPT, "I could not produce code for this."
    )
    assert verdict is None and "no shared entrypoint" in detail
    assert w.calls == []  # abstained before spending any generation


async def test_differential_single_disagreement_is_below_reject_threshold():
    # wrong on exactly one input (x=0) -> 1 mismatch < MIN_DISAGREEMENTS -> abstain
    one_off = "```python\ndef double(x):\n    return x * 2 if x != 0 else 1\n```"
    w = _ScriptedWorker(gen=_GOOD_GEN)
    verdict, _c, detail = await differential_check(w, _TASK_PROMPT, one_off)
    assert verdict is None and "below reject threshold" in detail


async def test_differential_min_disagreements_one_rejects_single_mismatch():
    one_off = "```python\ndef double(x):\n    return x * 2 if x != 0 else 1\n```"
    w = _ScriptedWorker(gen=_GOOD_GEN)
    verdict, _c, detail = await differential_check(
        w, _TASK_PROMPT, one_off, min_disagreements=1
    )
    assert verdict is False and "1/" in detail


async def test_differential_abstains_on_single_usable_reference():
    # 2 of 3 generations are garbage -> only 1 reference -> insufficient material
    w = _ScriptedWorker(gen=[_GOOD_GEN, "no code here", "also not code"])
    verdict, _c, detail = await differential_check(w, _TASK_PROMPT, _CANDIDATE_CORRECT)
    assert verdict is None and "insufficient material" in detail


# ----- code_augmented_judge: recall-only invariant -----

async def test_override_accept_to_reject_when_tool_catches():
    w = _ScriptedWorker(base='{"correct": true}', gen=_GOOD_GEN)
    verdict, _c, detail = await code_augmented_judge(w, _TASK_PROMPT, _CANDIDATE_WRONG)
    assert verdict is False and "tool override" in detail


async def test_no_override_when_tool_abstains():
    w = _ScriptedWorker(base='{"correct": true}', gen="not a code block at all")
    verdict, _c, _d = await code_augmented_judge(w, _TASK_PROMPT, _CANDIDATE_CORRECT)
    assert verdict is True


async def test_base_reject_returned_and_tool_never_invoked():
    w = _ScriptedWorker(base='{"correct": false}', gen=_GOOD_GEN)
    verdict, _c, detail = await code_augmented_judge(w, _TASK_PROMPT, _CANDIDATE_CORRECT)
    assert verdict is False and "tool skipped" in detail
    assert w.calls.count("gen") == 0 and w.calls == ["base"]


async def test_differential_check_never_returns_true():
    verdicts = []
    w = _ScriptedWorker(gen=_GOOD_GEN)  # no entrypoint in prompt -> abstain
    verdicts.append((await differential_check(w, "no signature here", "prose"))[0])
    w = _ScriptedWorker(gen="garbage")  # zero references -> abstain
    verdicts.append((await differential_check(w, _TASK_PROMPT, _CANDIDATE_CORRECT))[0])
    w = _ScriptedWorker(gen=_GOOD_GEN)  # real catch -> False, not True
    verdicts.append((await differential_check(w, _TASK_PROMPT, _CANDIDATE_WRONG))[0])
    assert all(v in (None, False) for v in verdicts)
