"""
nonverifiable_bank.py — loader + scorer for the non-verifiable judge benchmark.

The verifiable bank (experiments/prompts_verifiable.jsonl) establishes each
answer's correctness by EXECUTION — run the hidden tests. On tasks with no
executable oracle (explain / reason / summarize / factual / instruct), that's
impossible, so this bank establishes ground truth BY CONSTRUCTION: every row
ships a hand-authored `reference` (a genuinely-correct answer) and a `mutant` (a
subtly-flawed answer with exactly one labeled defect). The reference is the
positive label, the mutant the negative — and a judge is scored on whether it
separates them from the prompt + answer ALONE (the production posture, findings
Era 13–14). This is the judge's real proving ground: on verifiable tasks you'd
just run the tests.

The mutant is authored to match the reference in length, fluency, and
confidence, differing ONLY in substance — so the score measures whether the
judge tracks correctness rather than rewarding elaboration (the length bias that
sank every pre-2026 judge, findings Era 7).

Pure functions only: no inference, no I/O beyond reading the two JSONL files.
`bench_report.judge-bank` drives the actual judge over these rows; this module
loads them and scores the verdicts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

VALID_BUCKETS = {"explain", "factual", "reason", "summarize", "instruct"}
VALID_TIERS = {"easy", "medium", "hard"}
# Union of the per-bucket defect taxonomies the bank is authored against. Naming
# the defect makes each negative label auditable (the analog of the verifiable
# bank proving its tests are sensitive).
VALID_DEFECTS = {
    "wrong-fact", "flawed-reasoning", "wrong-conclusion", "conflation",
    "inverted-causation", "subtle-omission", "wrong-quantity",
    "unfaithful-addition", "unfaithful-inversion", "overstatement",
    "partial-answer", "constraint-violation", "meaning-drift", "off-target",
}


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rows.append(json.loads(line))
    return rows


def load_bank(path: Path) -> dict[str, dict]:
    """Map id -> {prompt, bucket, tier, verify}. Keyed by id (non-code tasks
    have no entrypoint; id is the join key to the refs file)."""
    return {r["id"]: r for r in _load_jsonl(path)}


def load_refs(path: Path) -> dict[str, dict]:
    """Map id -> {reference, mutant, defect, flaw}."""
    return {r["id"]: r for r in _load_jsonl(path)}


@dataclass
class BankScore:
    """Aggregate discrimination of a judge over the labeled pairs.

    `ref_accept_rate`  — fraction of correct references the judge accepted (True).
    `mutant_catch_rate`— fraction of flawed mutants the judge rejected (False);
                         the escalation-relevant recall — can it catch bad
                         answers when no oracle exists?
    `paired_correct`   — rows where BOTH sides were judged right (ref accepted
                         AND mutant caught); the honest "did it actually
                         discriminate this pair" count.
    `accuracy`         — over all parsed judgments (2 per row).
    """
    n_rows: int
    ref_accepted: int
    ref_parsed: int
    mutant_caught: int
    mutant_parsed: int
    paired_correct: int
    unparsed: int
    by_bucket: dict[str, dict] = field(default_factory=dict)
    by_defect: dict[str, dict] = field(default_factory=dict)

    @property
    def ref_accept_rate(self) -> float:
        return self.ref_accepted / self.ref_parsed if self.ref_parsed else 0.0

    @property
    def mutant_catch_rate(self) -> float:
        return self.mutant_caught / self.mutant_parsed if self.mutant_parsed else 0.0

    @property
    def accuracy(self) -> float:
        correct = self.ref_accepted + self.mutant_caught
        parsed = self.ref_parsed + self.mutant_parsed
        return correct / parsed if parsed else 0.0

    @property
    def paired_rate(self) -> float:
        return self.paired_correct / self.n_rows if self.n_rows else 0.0

    def as_dict(self) -> dict:
        return {
            "n_rows": self.n_rows,
            "accuracy": round(self.accuracy, 4),
            "ref_accept_rate": round(self.ref_accept_rate, 4),
            "mutant_catch_rate": round(self.mutant_catch_rate, 4),
            "paired_correct": self.paired_correct,
            "paired_rate": round(self.paired_rate, 4),
            "unparsed": self.unparsed,
            "ref_accepted": self.ref_accepted,
            "ref_parsed": self.ref_parsed,
            "mutant_caught": self.mutant_caught,
            "mutant_parsed": self.mutant_parsed,
            "by_bucket": self.by_bucket,
            "by_defect": self.by_defect,
        }


def score(
    bank: dict[str, dict],
    refs: dict[str, dict],
    verdict_ref: dict[str, Optional[bool]],
    verdict_mut: dict[str, Optional[bool]],
) -> BankScore:
    """Score judge verdicts against the by-construction labels.

    `verdict_ref[id]` / `verdict_mut[id]` are the judge's booleans (None =
    unparseable, excluded from rates but counted in `unparsed`). Ground truth:
    reference is correct (want True), mutant is flawed (want False).
    """
    ids = [i for i in bank if i in refs]
    ref_accepted = ref_parsed = mutant_caught = mutant_parsed = 0
    paired_correct = unparsed = 0
    bucket_acc: dict[str, list[int]] = {}   # bucket -> [correct, parsed]
    defect_catch: dict[str, list[int]] = {}  # defect -> [caught, parsed]

    for i in ids:
        bucket = bank[i].get("bucket", "?")
        defect = refs[i].get("defect", "?")
        vr = verdict_ref.get(i)
        vm = verdict_mut.get(i)

        b = bucket_acc.setdefault(bucket, [0, 0])
        d = defect_catch.setdefault(defect, [0, 0])

        if vr is None:
            unparsed += 1
        else:
            ref_parsed += 1
            b[1] += 1
            if vr is True:
                ref_accepted += 1
                b[0] += 1
        if vm is None:
            unparsed += 1
        else:
            mutant_parsed += 1
            b[1] += 1
            d[1] += 1
            if vm is False:
                mutant_caught += 1
                b[0] += 1
                d[0] += 1
        if vr is True and vm is False:
            paired_correct += 1

    by_bucket = {
        bk: {"correct": c, "parsed": p, "accuracy": round(c / p, 4) if p else 0.0}
        for bk, (c, p) in sorted(bucket_acc.items())
    }
    by_defect = {
        df: {"caught": c, "parsed": p, "catch_rate": round(c / p, 4) if p else 0.0}
        for df, (c, p) in sorted(defect_catch.items())
    }
    return BankScore(
        n_rows=len(ids),
        ref_accepted=ref_accepted, ref_parsed=ref_parsed,
        mutant_caught=mutant_caught, mutant_parsed=mutant_parsed,
        paired_correct=paired_correct, unparsed=unparsed,
        by_bucket=by_bucket, by_defect=by_defect,
    )
