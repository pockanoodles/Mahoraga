"""Integrity guard for the non-verifiable judge bank + unit tests for its scorer.

The verifiable bank proves its labels by EXECUTION (reference passes tests,
mutant fails). A non-verifiable bank has no oracle, so its labels are by
CONSTRUCTION: a hand-authored correct `reference` and a subtly-flawed `mutant`
with one named `defect`. This guard can't run code to confirm the labels, so it
enforces the *structural* contract that keeps them trustworthy — most
importantly length parity, so a judge can't separate reference from mutant on
length instead of substance (the bias that sank every pre-2026 judge, Era 7).
Label correctness itself is enforced upstream by an independent adversarial
blind-audit at authoring time; this guard keeps the files well-formed.

Also covers the pure scorer in routing/nonverifiable_bank.py (no inference).
"""
from __future__ import annotations

from pathlib import Path

from backend.orchestrator.routing.nonverifiable_bank import (
    VALID_BUCKETS,
    VALID_DEFECTS,
    VALID_TIERS,
    load_bank,
    load_refs,
    score,
)

_REPO = Path(__file__).resolve().parents[2]
BANK_PATH = _REPO / "experiments" / "prompts_nonverifiable.jsonl"
REFS_PATH = _REPO / "experiments" / "prompts_nonverifiable_refs.jsonl"

BANK = load_bank(BANK_PATH)
REFS = load_refs(REFS_PATH)


# ── structural integrity ─────────────────────────────────────────────────────

def test_bank_rows_well_formed():
    for i, row in BANK.items():
        assert row.get("id") == i
        assert row.get("prompt"), f"{i}: empty prompt"
        assert row.get("bucket") in VALID_BUCKETS, f"{i}: bad bucket {row.get('bucket')}"
        assert row.get("tier") in VALID_TIERS, f"{i}: bad tier {row.get('tier')}"
        assert row.get("verify") == "judgment", f"{i}: verify != judgment"


def test_ids_unique_and_prompts_unique():
    prompts = [r["prompt"] for r in BANK.values()]
    assert len(prompts) == len(set(prompts)), "duplicate prompt text in bank"


def test_refs_cover_bank_exactly():
    assert set(BANK) == set(REFS), (
        f"missing refs: {set(BANK) - set(REFS)}; orphan refs: {set(REFS) - set(BANK)}"
    )


def test_refs_well_formed():
    for i, r in REFS.items():
        assert r.get("reference"), f"{i}: empty reference"
        assert r.get("mutant"), f"{i}: empty mutant"
        assert r["reference"] != r["mutant"], f"{i}: reference == mutant"
        assert r.get("defect") in VALID_DEFECTS, f"{i}: bad defect {r.get('defect')}"
        assert r.get("flaw"), f"{i}: empty flaw (the negative label must be auditable)"


def test_mutant_matches_reference_length():
    # The benchmark's validity rests on the mutant NOT being separable by length
    # — a judge that flags the shorter answer would score well without tracking
    # correctness at all. Enforce rough parity (mutant within 0.5x–2x reference).
    for i, r in REFS.items():
        lr, lm = len(r["reference"]), len(r["mutant"])
        ratio = lm / lr if lr else 0.0
        assert 0.5 <= ratio <= 2.0, (
            f"{i}: length parity broken (reference={lr}, mutant={lm}, ratio={ratio:.2f})"
        )


def test_bank_nonempty():
    assert len(BANK) >= 20, "bank suspiciously small"


# ── scorer unit tests (pure, no inference) ───────────────────────────────────

_BANK = {
    "a": {"id": "a", "bucket": "explain", "tier": "easy", "verify": "judgment", "prompt": "p"},
    "b": {"id": "b", "bucket": "factual", "tier": "hard", "verify": "judgment", "prompt": "p"},
}
_REFS = {
    "a": {"reference": "R", "mutant": "M", "defect": "wrong-fact", "flaw": "x"},
    "b": {"reference": "R", "mutant": "M", "defect": "overstatement", "flaw": "x"},
}


def test_perfect_judge_scores_all():
    vr = {"a": True, "b": True}   # accepts both references
    vm = {"a": False, "b": False}  # rejects both mutants
    sc = score(_BANK, _REFS, vr, vm)
    assert sc.accuracy == 1.0
    assert sc.ref_accept_rate == 1.0
    assert sc.mutant_catch_rate == 1.0
    assert sc.paired_correct == 2
    assert sc.unparsed == 0


def test_accept_everything_judge_catches_no_mutants():
    vr = {"a": True, "b": True}
    vm = {"a": True, "b": True}  # never rejects a mutant
    sc = score(_BANK, _REFS, vr, vm)
    assert sc.ref_accept_rate == 1.0
    assert sc.mutant_catch_rate == 0.0
    assert sc.paired_correct == 0
    assert sc.accuracy == 0.5  # refs right, mutants all wrong


def test_unparsed_excluded_from_rates_but_counted():
    vr = {"a": None, "b": True}
    vm = {"a": False, "b": None}
    sc = score(_BANK, _REFS, vr, vm)
    assert sc.unparsed == 2
    assert sc.ref_parsed == 1 and sc.ref_accepted == 1
    assert sc.mutant_parsed == 1 and sc.mutant_caught == 1
    assert sc.paired_correct == 0  # neither row had BOTH sides parsed+correct


def test_by_bucket_and_by_defect_breakdown():
    vr = {"a": True, "b": False}   # ref b wrongly rejected
    vm = {"a": False, "b": False}  # both mutants caught
    sc = score(_BANK, _REFS, vr, vm)
    # bucket 'explain' (row a): ref accepted + mutant caught = 2/2
    assert sc.by_bucket["explain"] == {"correct": 2, "parsed": 2, "accuracy": 1.0}
    # bucket 'factual' (row b): ref rejected (wrong) + mutant caught = 1/2
    assert sc.by_bucket["factual"] == {"correct": 1, "parsed": 2, "accuracy": 0.5}
    assert sc.by_defect["wrong-fact"]["catch_rate"] == 1.0
    assert sc.by_defect["overstatement"]["catch_rate"] == 1.0
