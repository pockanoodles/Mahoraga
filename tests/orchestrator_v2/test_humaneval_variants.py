"""Tests for the contamination probe builder (experiments/build_humaneval_variants.py).

The probe exists because HumanEval is from 2021 and sits in every training set,
which threatens a *longitudinal* claim specifically: if one arm's post-training
absorbed more leaked solutions than another's, "arm B beats arm A" is leakage,
not capability.

The property under test is the one the probe's validity rests on — **Gate B**:
a variant only counts if the ORIGINAL canonical solution *fails* it. A variant
whose spec edit does not bite would produce a reassuring null and quietly retire
a live question, so the gate is enforced at build time and tested here rather
than trusted to review.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_BUILDER = _REPO / "experiments" / "build_humaneval_variants.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("_hv_builder", _BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():
    return _load_builder()


def _build(builder, tmp_path, capsys) -> tuple[int, dict]:
    """Run the builder with output redirected away from the repo."""
    builder.OUT_PATH = tmp_path / "prompts_humaneval_variants.jsonl"
    code = builder.cmd_build(as_json=True)
    return code, json.loads(capsys.readouterr().out)


def test_spec_file_has_at_least_one_control(builder):
    """Without a control row, "zero failures" cannot be distinguished from
    "Gate B never fires" — the gate would be untested by its own build."""
    specs = builder._load_jsonl(builder.SPEC_PATH)
    controls = [s for s in specs if s.get("expect") == "reject"]
    assert controls, "the variant spec must carry at least one expect=reject control"


def test_build_succeeds_and_the_control_is_rejected(builder, tmp_path, capsys):
    code, result = _build(builder, tmp_path, capsys)
    assert code == 0, result.get("failure_detail")
    assert result["failures"] == 0
    # The control fired: Gate B rejected a spec edit that does not discriminate.
    assert result["controls_verified"] >= 1


def test_accepted_rows_carry_the_anchor_schema(builder, tmp_path, capsys):
    code, result = _build(builder, tmp_path, capsys)
    assert code == 0
    assert result["accepted"] >= 1
    rows = builder._load_jsonl(builder.OUT_PATH)
    anchors = builder._by_task_id(builder._load_jsonl(builder.ANCHOR_PATH))
    for row in rows:
        for key in ("prompt", "bucket", "tier", "entrypoint", "verify", "tests", "task_id"):
            assert key in row, f"{row.get('task_id')} missing {key}"
        # Graded by the same machinery as the anchor, so bucket/tier/entrypoint
        # must not drift from it.
        anchor = anchors[row["task_id"]]
        for key in ("bucket", "tier", "entrypoint", "verify"):
            assert row[key] == anchor[key]
        assert row["entrypoint"] in row["tests"]
        assert row["n_cases"] >= builder.MIN_CASES


def test_variant_prompt_differs_from_the_anchor_prompt(builder, tmp_path, capsys):
    """A variant that reads identically to the anchor is not a variant."""
    code, _ = _build(builder, tmp_path, capsys)
    assert code == 0
    anchors = builder._by_task_id(builder._load_jsonl(builder.ANCHOR_PATH))
    for row in builder._load_jsonl(builder.OUT_PATH):
        assert row["prompt"] != anchors[row["task_id"]]["prompt"]


def test_variant_changes_at_least_one_expected_output(builder, tmp_path, capsys):
    """Gate B's numeric counterpart: if no expected output moved, the spec edit
    was cosmetic on this input set even if the prose changed."""
    code, _ = _build(builder, tmp_path, capsys)
    assert code == 0
    for row in builder._load_jsonl(builder.OUT_PATH):
        assert row["n_outputs_changed"] >= 1, row["task_id"]


def test_inputs_come_from_the_committed_bank_not_the_raw_dump(builder):
    """The probe must be rebuildable from a clean clone. The 7.7 MB EvalPlus
    dump is not committed, so sourcing inputs from it would break that."""
    source = _BUILDER.read_text()
    assert "humaneval_plus_raw" not in source
    anchors = builder._load_jsonl(builder.ANCHOR_PATH)
    inputs, expected, _ = builder._anchor_cases(anchors[0])
    assert inputs and len(inputs) == len(expected)
