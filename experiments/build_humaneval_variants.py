#!/usr/bin/env python3
"""build_humaneval_variants.py — the contamination probe for the HumanEval+ anchor.

HumanEval is from 2021 and sits in every training set. That matters for a
*longitudinal* claim specifically: if one local arm's post-training absorbed
more leaked solutions than another's, "arm B beats arm A" is leakage, not
capability — which is exactly the inference the study wants to make.

**Renaming is not a probe.** The obvious design — rename the entrypoint and
parameters, keep semantics identical — cannot work. A model that genuinely
internalised the solution still emits it under a new name, and emitting the
right body *is* correct behaviour. Renaming breaks exact-match retrieval and
nothing else, so a model that passes tells us nothing. (Worse: unnatural names
would make a drop measure confusion instead of contamination, and the probe
would read backwards.)

**So the variant changes the required semantics.** An inclusive bound becomes
exclusive, a return convention inverts, an edge case gains a required
behaviour — stated in the docstring, with the canonical solution edited to
match. Expected outputs are recomputed by executing the *edited* solution over
the *same* inputs, so the input distribution is held fixed and only the oracle
moves. A model reciting the memorised body now fails, and only a model that
read the spec passes.

Two gates, enforced at build time rather than by review:

  Gate A — the variant's own reference passes the variant's tests.
  Gate B — the ORIGINAL canonical solution FAILS the variant's tests.

Gate B is the probe. If the original solution still passes, the spec edit did
not bite on this input set, the variant cannot discriminate a memorised answer,
and it is rejected. A variant that silently fails to discriminate would produce
a reassuring null and quietly retire a live question — the funnel's 0.0%, one
level up.

The oracle path is reused from build_humaneval_bank.py rather than
reimplemented, so the variant bank and the anchor bank are graded by identical
machinery.

Usage:
    ./build_humaneval_variants.py build          # spec -> bank, both gates enforced
    ./build_humaneval_variants.py build --json   # machine-readable report
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from build_humaneval_bank import (  # noqa: E402
    CALL_TIMEOUT_S,
    MAX_ASSERTS,
    _CallTimeout,
    _call_with_timeout,
    _make_tests,
    _roundtrips,
    _verify,
)

ANCHOR_PATH = _HERE / "prompts_humaneval_plus.jsonl"
ANCHOR_REFS_PATH = _HERE / "prompts_humaneval_plus_refs.jsonl"

# Hand-authored, one JSON object per line:
#   {task_id, prompt, reference, defect_class, note, expect}
# `prompt` is the full stub the model sees (same wrapper text as the anchor);
# `reference` is a standalone correct program for the VARIANT spec.
# The entrypoint name is deliberately NOT changed — see the module docstring.
# `expect` is "accept" (default) or "reject". A "reject" row is a deliberate
# CONTROL — a spec edit that should NOT discriminate (e.g. a cosmetic reword) —
# and it makes Gate B test-covered by its own build: if a control is accepted,
# the gate is broken and the build fails. Without controls, "no rejections"
# would be indistinguishable from "the gate never fires".
SPEC_PATH = _HERE / "humaneval_variants.jsonl"
OUT_PATH = _HERE / "prompts_humaneval_variants.jsonl"

# A variant with too few surviving cases cannot resolve anything. The spec edit
# can legitimately make some inputs raise under the new contract, and those get
# dropped by the shared oracle path, so this floor has to be checked after.
MIN_CASES = 5

_HEADER = """\
# experiments/prompts_humaneval_variants.jsonl — the contamination probe for
# prompts_humaneval_plus.jsonl, built by experiments/build_humaneval_variants.py
# from the hand-authored specs in experiments/humaneval_variants.jsonl.
# Each row carries a DELIBERATELY ALTERED spec whose expected outputs were
# recomputed from an edited canonical solution over the anchor's own inputs.
# Every row is build-time guaranteed to FAIL the original canonical solution
# (Gate B) — that is what makes a pass here evidence of reading rather than
# recall. Same schema as the anchor bank.
"""


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"{path} missing")
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _by_task_id(rows: list[dict]) -> dict[str, dict]:
    return {r["task_id"]: r for r in rows if r.get("task_id")}


def _anchor_cases(anchor: dict) -> tuple[list, list, float]:
    """Recover (input_args, expected, atol) from the anchor row's own tests.

    The inputs deliberately come from the **committed anchor bank** rather than
    from the 7.7 MB EvalPlus dump, which is not committed. Two reasons, and the
    second is the important one:

    1. The probe stays reproducible from committed artifacts alone — the same
       property `orch bench verify` relies on. A clean clone can rebuild it.
    2. The input set becomes *byte-identical* to the anchor's, rather than
       re-derived. The anchor already dropped inputs its own oracle rejected,
       so re-deriving from raw could admit inputs the anchor never graded on
       and quietly make the two banks incomparable.

    Each anchor assert has the shape
    `assert _meq(entrypoint(*[args]), expected, atol)`, so the args are a
    literal that parses back out exactly.
    """
    inputs: list = []
    expected: list = []
    atol: float = 0.0
    for node in ast.parse(anchor["tests"]).body:
        if not (isinstance(node, ast.Assert) and isinstance(node.test, ast.Call)):
            continue
        call_node, expected_node, atol_node = node.test.args
        inputs.append(ast.literal_eval(call_node.args[0].value))
        expected.append(ast.literal_eval(expected_node))
        atol = ast.literal_eval(atol_node)
    return inputs, expected, atol


def _recompute(reference: str, entry_point: str, inputs: list, drops: Counter) -> list:
    """Execute the VARIANT reference over the anchor's inputs, in this process.

    Only the oracle moves: same inputs, new expected outputs. A spec edit can
    legitimately make some inputs invalid under the new contract (a stricter
    precondition, say), so those are dropped with a reason rather than being
    allowed to fail the build.
    """
    namespace: dict = {}
    exec(reference, namespace)  # noqa: S102 — hand-authored reference, not input
    fn = namespace[entry_point]

    cases = []
    for args in inputs:
        if not _roundtrips(args):
            drops["repr_no_roundtrip"] += 1
            continue
        try:
            value = _call_with_timeout(fn, copy.deepcopy(args), CALL_TIMEOUT_S)
        except AssertionError:
            drops["contract_rejected"] += 1
            continue
        except _CallTimeout:
            drops["call_timeout"] += 1
            continue
        except Exception:  # noqa: BLE001
            drops["call_error"] += 1
            continue
        if not _roundtrips(value):
            drops["repr_no_roundtrip"] += 1
            continue
        cases.append((args, value))
    return cases


def cmd_build(as_json: bool) -> int:
    specs = _load_jsonl(SPEC_PATH)
    if not specs:
        sys.exit(f"{SPEC_PATH} has no variant specs — nothing to build")

    anchors = _by_task_id(_load_jsonl(ANCHOR_PATH))
    anchor_refs = _by_task_id(_load_jsonl(ANCHOR_REFS_PATH))

    accepted: list[dict] = []
    rejected: list[dict] = []
    controls_ok: list[dict] = []
    failures: list[dict] = []
    drops: Counter = Counter()

    def resolve(spec: dict, report: dict, *, rejected_with: str | None) -> None:
        """Route an outcome by what the spec said to expect.

        A rejection is normally a spec bug. For a control row it is the
        expected result and confirms Gate B fires; a control that gets
        *accepted* means the gate is broken, which is the serious failure.
        """
        wants_reject = spec.get("expect", "accept") == "reject"
        if rejected_with is not None:
            entry = {**report, "reason": rejected_with}
            (controls_ok if wants_reject else failures).append(entry)
            rejected.append(entry)
        elif wants_reject:
            failures.append({
                **report,
                "reason": "CONTROL WAS ACCEPTED — this spec was marked expect=reject, "
                          "so Gate B failed to fire. The probe cannot be trusted "
                          "until this is explained.",
            })

    for spec in specs:
        task_id = spec.get("task_id")
        report = {"task_id": task_id, "defect_class": spec.get("defect_class")}

        missing = [
            name
            for name, table in (("anchor", anchors), ("anchor_ref", anchor_refs))
            if task_id not in table
        ]
        if missing:
            resolve(spec, report, rejected_with=f"unknown task_id in {'/'.join(missing)}")
            continue

        anchor, anchor_ref = anchors[task_id], anchor_refs[task_id]
        entry_point = anchor["entrypoint"]
        inputs, anchor_expected, atol = _anchor_cases(anchor)

        # Only the oracle moves: the anchor's own inputs, the variant's outputs.
        try:
            cases = _recompute(spec["reference"], entry_point, inputs, drops)
        except Exception as exc:  # noqa: BLE001 — a bad hand-authored reference
            resolve(spec, report, rejected_with=f"variant reference did not execute: {exc}")
            continue

        cases = cases[:MAX_ASSERTS]
        changed = sum(
            1 for (args, got), was in zip(cases, anchor_expected) if got != was
        )
        if len(cases) < MIN_CASES:
            resolve(spec, report, rejected_with=(
                f"only {len(cases)} cases survived the new contract "
                f"(floor is {MIN_CASES}) — cannot resolve anything"
            ))
            continue

        tests = _make_tests(entry_point, cases, atol)

        # Gate A — the variant must be satisfiable as written.
        ok_a, _, err_a = _verify(spec["reference"], tests)
        if not ok_a:
            resolve(spec, report, rejected_with=(
                f"GATE A: variant reference fails its own tests ({err_a})"
            ))
            continue

        # Gate B — the memorised answer must be WRONG. This is the probe.
        ok_b, _, _ = _verify(anchor_ref["reference"], tests)
        if ok_b:
            resolve(spec, report, rejected_with=(
                "GATE B: the original canonical solution still PASSES — "
                "the spec edit does not bite on these inputs, so this "
                "variant cannot discriminate recall from reading"
            ))
            continue

        resolve(spec, report, rejected_with=None)
        if spec.get("expect", "accept") == "reject":
            continue

        accepted.append({
            "prompt": spec["prompt"],
            "bucket": anchor["bucket"],
            "tier": anchor["tier"],
            "entrypoint": entry_point,
            "verify": anchor["verify"],
            "tests": tests,
            "task_id": task_id,
            "variant_of": task_id,
            "defect_class": spec.get("defect_class"),
            "n_cases": len(cases),
            "n_outputs_changed": changed,
        })

    if accepted:
        OUT_PATH.write_text(
            _HEADER + "".join(json.dumps(r) + "\n" for r in accepted)
        )

    result = {
        "specs": len(specs),
        "accepted": len(accepted),
        "controls_verified": len(controls_ok),
        "failures": len(failures),
        "failure_detail": failures,
        "input_drops": dict(drops),
        "out_path": str(OUT_PATH) if accepted else None,
    }

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"variant specs read       {len(specs)}")
        print(f"accepted (both gates)    {len(accepted)}")
        print(f"controls verified        {len(controls_ok)}   "
              f"(expect=reject rows that Gate B correctly rejected)")
        print(f"failures                 {len(failures)}")
        if drops:
            print("\ninput cases dropped by the new contract:")
            for reason, n in sorted(drops.items(), key=lambda kv: -kv[1]):
                print(f"  {reason:<24} {n:>5}")
        if controls_ok:
            print("\ncontrols that fired as designed — this is the gate proving itself:")
            for r in controls_ok:
                print(f"  {r['task_id']:<18} {r.get('defect_class')}")
        if failures:
            print("\nFAILURES — each needs a spec edit, not a retry:")
            for r in failures:
                print(f"  {r['task_id']:<18} {r['reason']}")
        if not controls_ok:
            print(
                "\nNOTE: no control rows. With no expect=reject spec, "
                '"zero failures" cannot be\n      distinguished from "Gate B never fires". '
                "Author at least one control."
            )
        if accepted:
            print(f"\nwrote {OUT_PATH}")
            print(
                "\nEvery accepted row is build-time guaranteed to fail the original\n"
                "canonical solution, so a pass on this bank is evidence of reading\n"
                "the spec rather than recalling the answer."
            )
        else:
            print("\nNothing accepted — no bank written.")

    # A failure is a spec bug, not a warning to scroll past. A verified control
    # is not a failure — it is the probe's own self-test passing.
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build", help="build the variant bank, enforcing both gates")
    build.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args()
    if args.cmd == "build":
        sys.exit(cmd_build(args.json))


if __name__ == "__main__":
    main()
