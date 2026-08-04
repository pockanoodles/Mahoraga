"""Integrity guard for the HumanEval+ bank (experiments/prompts_humaneval_plus.jsonl).

Every bank row must carry hidden tests that its committed reference program
(raw stub + canonical solution, in prompts_humaneval_plus_refs.jsonl, keyed by
task_id) actually passes. Execution goes through the same run_case() path
`orch bench report verify` uses, fenced like a model answer, so extract_code()
integration is exercised too. Unlike the hand-curated gold bank there are no
mutants — HumanEval+ test sensitivity is established upstream by evalplus.

The full per-task sweep (164 references) is marked slow; a deterministic
12-task slice (every 14th task by task_id number) runs unmarked so CI
(`pytest -m "not slow"`) still exercises the loader and real execution.
Bank + refs are rebuilt by experiments/build_humaneval_bank.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.orchestrator.routing.verify_replay import load_bank, run_case

_REPO = Path(__file__).resolve().parents[2]
BANK_PATH = _REPO / "experiments" / "prompts_humaneval_plus.jsonl"
REFS_PATH = _REPO / "experiments" / "prompts_humaneval_plus_refs.jsonl"

_VALID_TIERS = {"easy", "medium", "hard"}
_TASK_ID_RE = re.compile(r"^HumanEval/\d+$")
_CASE_TIMEOUT_SECONDS = 15
_CI_SLICE_STRIDE = 14


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rows.append(json.loads(line))
    return rows


BANK_ROWS = _load_jsonl(BANK_PATH)
REF_ROWS = _load_jsonl(REFS_PATH)
ROWS_BY_TASK_ID = {r["task_id"]: r for r in BANK_ROWS}
REFS_BY_TASK_ID = {r["task_id"]: r for r in REF_ROWS}


# ── structural integrity ─────────────────────────────────────────────────────

def test_bank_rows_well_formed():
    for row in BANK_ROWS:
        tid = row.get("task_id", "")
        assert _TASK_ID_RE.match(tid), f"bad task_id: {tid!r}"
        assert row.get("prompt"), f"{tid}: empty prompt"
        assert row.get("entrypoint"), f"{tid}: empty entrypoint"
        assert row.get("tests"), f"{tid}: empty tests"
        assert row.get("verify") == "solution", f"{tid}: verify != solution"
        assert row.get("bucket") == "code", f"{tid}: bad bucket {row.get('bucket')}"
        assert row.get("tier") in _VALID_TIERS, f"{tid}: bad tier {row.get('tier')}"


def test_task_ids_unique():
    tids = [r["task_id"] for r in BANK_ROWS]
    assert len(tids) == len(set(tids)), "duplicate task_ids in bank"


def test_prompts_unique():
    # verify_replay joins outputs to rows by exact prompt text — a duplicate
    # prompt would make two rows indistinguishable at scoring time.
    prompts = [r["prompt"] for r in BANK_ROWS]
    assert len(prompts) == len(set(prompts)), "duplicate prompt text in bank"


def test_tests_reference_entrypoint():
    for row in BANK_ROWS:
        assert row["entrypoint"] in row["tests"], (
            f"{row['task_id']}: tests never call the entrypoint"
        )


def test_refs_cover_bank_exactly():
    bank_ids = set(ROWS_BY_TASK_ID)
    ref_ids = set(REFS_BY_TASK_ID)
    assert bank_ids == ref_ids, (
        f"missing refs: {bank_ids - ref_ids}; orphan refs: {ref_ids - bank_ids}"
    )
    for r in REF_ROWS:
        assert r.get("reference"), f"{r['task_id']}: empty reference"
        assert r.get("entrypoint") == ROWS_BY_TASK_ID[r["task_id"]]["entrypoint"], (
            f"{r['task_id']}: entrypoint mismatch between bank and refs"
        )


def test_load_bank_sees_every_row():
    bank = load_bank(BANK_PATH)
    assert len(bank) == len(BANK_ROWS)


# ── execution: reference passes its hidden tests ─────────────────────────────

def _task_num(task_id: str) -> int:
    return int(task_id.rsplit("/", 1)[1])


_SORTED_TASK_IDS = sorted(ROWS_BY_TASK_ID, key=_task_num)
_CI_SLICE = set(_SORTED_TASK_IDS[::_CI_SLICE_STRIDE])

_PARAMS = [
    tid if tid in _CI_SLICE else pytest.param(tid, marks=pytest.mark.slow)
    for tid in _SORTED_TASK_IDS
]


def _fence(code: str) -> str:
    # Present the code exactly as a model answer would arrive, so
    # extract_code() runs on the same shape it sees in a real bench run.
    return f"```python\n{code}\n```"


@pytest.mark.parametrize("task_id", _PARAMS)
def test_reference_passes_hidden_tests(task_id):
    ref = REFS_BY_TASK_ID[task_id]["reference"]
    passed, err = run_case(
        _fence(ref), ROWS_BY_TASK_ID[task_id]["tests"], timeout=_CASE_TIMEOUT_SECONDS
    )
    assert passed, f"{task_id}: reference failed its own tests: {err}"
