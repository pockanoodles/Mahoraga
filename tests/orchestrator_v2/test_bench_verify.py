"""Tests for the published-claims verifier (routing.benchmark.verify).

This module exists to stop a published number from drifting away from the file
that produced it, so the tests are mostly about its *failure* behaviour: a
verifier that passes when it should not is worse than no verifier, because it
launders an unbacked claim as a checked one.

The last test in this file checks the real committed manifest. If it fails,
either the README/RESULTS numbers changed without their artifacts, or an
artifact changed without the prose — both are the finding, not a flaky test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.orchestrator.routing.benchmark.verify import (
    DEFAULT_CLAIMS,
    PROJECT_ROOT,
    _round_half_up,
    compute_metrics,
    render_verification,
    verify_claims,
)


def _row(*, final=True, cloud=True, local=True, escalated=False,
         total_cost=0.0, cloud_cost=0.2, judge_cost=0.0):
    return {
        "final_passed": final,
        "cloud_passed": cloud,
        "local_passed": local,
        "escalated": escalated,
        "total_cost": total_cost,
        "cloud_cost": cloud_cost,
        "judge_cost": judge_cost,
    }


def _write(tmp_path: Path, rows: list[dict], claims: dict) -> Path:
    art = tmp_path / "experiments"
    art.mkdir(exist_ok=True)
    (art / "run.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    path = art / "claims.json"
    path.write_text(json.dumps(claims))
    return path


def _claims(metrics: dict, artifact="experiments/run.jsonl") -> dict:
    return {"claims": [{
        "id": "t", "description": "test claim", "artifact": artifact,
        "metrics": metrics,
    }]}


# ── metric computation ───────────────────────────────────────────────────────


def test_metrics_match_hand_computation():
    rows = [_row(local=True, total_cost=0.0),
            _row(local=False, escalated=True, total_cost=0.3, final=True),
            _row(local=False, escalated=False, final=False)]
    m = compute_metrics(rows)
    assert m["n"] == 3
    assert m["routed_pass_at_1"] == pytest.approx(2 / 3)
    assert m["local_pass_at_1"] == pytest.approx(1 / 3)
    assert m["escalation_rate"] == pytest.approx(1 / 3)
    # 2 genuine local failures, 1 of them escalated
    assert m["judge_fail_recall"] == pytest.approx(0.5)
    assert m["routed_cost_per_1k"] == pytest.approx(0.3 / 3 * 1000)
    assert m["cost_reduction"] == pytest.approx(1 - (0.3 / 0.6))


def test_recall_is_measured_against_ground_truth_not_the_verdict():
    """The judge agreeing with itself proves nothing; recall is vs. real fails."""
    rows = [_row(local=False, escalated=True), _row(local=False, escalated=False),
            _row(local=True, escalated=True)]
    # 2 true failures, 1 escalated -> 0.5, and the escalated pass is ignored
    assert compute_metrics(rows)["judge_fail_recall"] == pytest.approx(0.5)


def test_missing_field_yields_none_not_zero():
    """A run without cloud baselines must not report a 100% cost cut."""
    rows = [{"final_passed": True, "local_passed": True, "escalated": False}]
    m = compute_metrics(rows)
    assert m["cloud_pass_at_1"] is None
    assert m["cost_reduction"] is None
    assert m["routed_cost_per_1k"] is None


def test_no_true_failures_makes_recall_none_not_one():
    rows = [_row(local=True), _row(local=True)]
    assert compute_metrics(rows)["judge_fail_recall"] is None


def test_zero_cloud_cost_does_not_divide_by_zero():
    rows = [_row(cloud_cost=0.0, total_cost=0.0)]
    assert compute_metrics(rows)["cost_reduction"] is None


def test_empty_artifact():
    assert compute_metrics([]) == {"n": 0}


# ── rounding ─────────────────────────────────────────────────────────────────


def test_rounding_is_half_up_not_bankers():
    """Publishing a number must not depend on which side of even it fell."""
    assert _round_half_up(0.6875, 3) == 0.688
    assert _round_half_up(0.6885, 3) == 0.689   # round() gives 0.688 here
    assert _round_half_up(8.465, 2) == 8.47


# ── claim checking ───────────────────────────────────────────────────────────


def test_matching_claim_passes(tmp_path):
    rows = [_row(), _row()]
    path = _write(tmp_path, rows, _claims({"routed_pass_at_1": {"value": 1.0, "decimals": 3}}))
    results = verify_claims(path, root=tmp_path)
    assert results[0].ok


def test_drifted_claim_fails_and_reports_both_numbers(tmp_path):
    rows = [_row(final=True), _row(final=False)]   # actual 0.500
    path = _write(tmp_path, rows, _claims({"routed_pass_at_1": {"value": 0.921, "decimals": 3}}))
    results = verify_claims(path, root=tmp_path)
    assert not results[0].ok
    detail = results[0].checks[0].detail
    assert "0.921" in detail and "0.500" in detail


def test_claim_is_checked_at_the_published_precision(tmp_path):
    """0.9207 backs a published 0.921; it does not back a published 0.9207."""
    rows = [_row(final=True)] * 92 + [_row(final=False)] * 8   # 0.92
    path = _write(tmp_path, rows, _claims({"routed_pass_at_1": {"value": 0.92, "decimals": 2}}))
    assert verify_claims(path, root=tmp_path)[0].ok
    path = _write(tmp_path, rows, _claims({"routed_pass_at_1": {"value": 0.921, "decimals": 3}}))
    assert not verify_claims(path, root=tmp_path)[0].ok


def test_missing_artifact_fails_rather_than_skipping(tmp_path):
    """An unbacked published number is exactly what this must catch."""
    path = _write(tmp_path, [_row()], _claims(
        {"routed_pass_at_1": {"value": 1.0, "decimals": 3}},
        artifact="experiments/does_not_exist.jsonl",
    ))
    result = verify_claims(path, root=tmp_path)[0]
    assert not result.ok
    assert "not found" in (result.error or "")


def test_uncomputable_metric_fails_rather_than_defaulting(tmp_path):
    rows = [{"final_passed": True, "local_passed": True, "escalated": False}]
    path = _write(tmp_path, rows, _claims({"cost_reduction": {"value": 0.765, "decimals": 3}}))
    result = verify_claims(path, root=tmp_path)[0]
    assert not result.ok
    assert "not computable" in result.checks[0].detail


def test_render_names_the_failing_claim(tmp_path):
    rows = [_row(final=False)]
    path = _write(tmp_path, rows, _claims({"routed_pass_at_1": {"value": 0.921, "decimals": 3}}))
    text = render_verification(verify_claims(path, root=tmp_path))
    assert "[FAIL]" in text
    assert "1 of 1 claims FAILED" in text


def test_render_lists_where_the_number_is_published(tmp_path):
    """A failure must point at the prose that needs fixing."""
    manifest = _claims({"routed_pass_at_1": {"value": 1.0, "decimals": 3}})
    manifest["claims"][0]["published_in"] = ["README.md"]
    path = _write(tmp_path, [_row()], manifest)
    assert "README.md" in render_verification(verify_claims(path, root=tmp_path))


# ── the real manifest ────────────────────────────────────────────────────────


def test_committed_claims_still_match_their_artifacts():
    """The headline numbers in README.md and docs/RESULTS.md are recomputed
    here from the per-case JSONL files in the repo. A failure means the prose
    and the data disagree — fix one of them, do not relax this test."""
    results = verify_claims(DEFAULT_CLAIMS, root=PROJECT_ROOT)
    assert results, "claims manifest is empty"
    failures = [r for r in results if not r.ok]
    assert not failures, render_verification(results)
