"""Integrity guard for the gold verifiable bank (experiments/prompts_verifiable.jsonl).

Every bank row must have a committed reference solution that passes its hidden
tests and a plausible near-miss mutant that fails them — proving the tests are
both satisfiable and sensitive (a vacuous test string that always passes would
silently inflate every arm's pass@1). References/mutants live in
experiments/prompts_verifiable_refs.jsonl, keyed by entrypoint. Execution goes
through the same run_case() path `orch bench report verify` uses, fenced like a
model answer, so extract_code() integration is exercised too.

This replaces the pre-2026-07-26 local-only scratchpad self-validation script,
which was never committed and has been lost — the guard now runs in CI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.orchestrator.routing.verify_replay import load_bank, run_case

_REPO = Path(__file__).resolve().parents[2]
BANK_PATH = _REPO / "experiments" / "prompts_verifiable.jsonl"
REFS_PATH = _REPO / "experiments" / "prompts_verifiable_refs.jsonl"

_VALID_BUCKETS = {"code", "debug"}
_VALID_TIERS = {"easy", "medium", "hard"}
_CASE_TIMEOUT_SECONDS = 15


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
REFS_BY_ENTRYPOINT = {r["entrypoint"]: r for r in REF_ROWS}


# ── structural integrity ─────────────────────────────────────────────────────

def test_bank_rows_well_formed():
    for row in BANK_ROWS:
        ep = row.get("entrypoint")
        assert ep, f"row missing entrypoint: {row.get('prompt', '')[:60]}"
        assert row.get("prompt"), f"{ep}: empty prompt"
        assert row.get("tests"), f"{ep}: empty tests"
        assert row.get("verify") == "solution", f"{ep}: verify != solution"
        assert row.get("bucket") in _VALID_BUCKETS, f"{ep}: bad bucket {row.get('bucket')}"
        assert row.get("tier") in _VALID_TIERS, f"{ep}: bad tier {row.get('tier')}"


def test_entrypoints_unique():
    eps = [r["entrypoint"] for r in BANK_ROWS]
    dupes = {e for e in eps if eps.count(e) > 1}
    assert not dupes, f"duplicate entrypoints: {dupes}"


def test_prompts_unique():
    # verify_replay joins outputs to rows by exact prompt text — a duplicate
    # prompt would make two rows indistinguishable at scoring time.
    prompts = [r["prompt"] for r in BANK_ROWS]
    assert len(prompts) == len(set(prompts)), "duplicate prompt text in bank"


def test_tests_reference_entrypoint():
    for row in BANK_ROWS:
        assert row["entrypoint"] in row["tests"], (
            f"{row['entrypoint']}: tests never call the entrypoint"
        )


def test_refs_cover_bank_exactly():
    bank_eps = {r["entrypoint"] for r in BANK_ROWS}
    ref_eps = set(REFS_BY_ENTRYPOINT)
    assert bank_eps == ref_eps, (
        f"missing refs: {bank_eps - ref_eps}; orphan refs: {ref_eps - bank_eps}"
    )
    for r in REF_ROWS:
        assert r.get("reference"), f"{r['entrypoint']}: empty reference"
        assert r.get("mutant"), f"{r['entrypoint']}: empty mutant"


def test_load_bank_sees_every_row():
    bank = load_bank(BANK_PATH)
    assert len(bank) == len(BANK_ROWS)


# ── execution: reference passes, mutant fails ────────────────────────────────

_TESTS_BY_ENTRYPOINT = {r["entrypoint"]: r["tests"] for r in BANK_ROWS}
_SHARED_ENTRYPOINTS = sorted(set(_TESTS_BY_ENTRYPOINT) & set(REFS_BY_ENTRYPOINT))


def _fence(code: str) -> str:
    # Present the code exactly as a model answer would arrive, so
    # extract_code() runs on the same shape it sees in a real bench run.
    return f"```python\n{code}\n```"


@pytest.mark.parametrize("entrypoint", _SHARED_ENTRYPOINTS)
def test_reference_passes_hidden_tests(entrypoint):
    ref = REFS_BY_ENTRYPOINT[entrypoint]["reference"]
    passed, err = run_case(
        _fence(ref), _TESTS_BY_ENTRYPOINT[entrypoint], timeout=_CASE_TIMEOUT_SECONDS
    )
    assert passed, f"{entrypoint}: reference failed its own tests: {err}"


@pytest.mark.parametrize("entrypoint", _SHARED_ENTRYPOINTS)
def test_mutant_fails_hidden_tests(entrypoint):
    mutant = REFS_BY_ENTRYPOINT[entrypoint]["mutant"]
    passed, _ = run_case(
        _fence(mutant), _TESTS_BY_ENTRYPOINT[entrypoint], timeout=_CASE_TIMEOUT_SECONDS
    )
    assert not passed, f"{entrypoint}: mutant passed — tests are not sensitive"
