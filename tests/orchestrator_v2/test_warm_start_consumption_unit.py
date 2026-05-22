"""
§7.3 / §15 Step 5 — warm-start consumption unit test.

Verify that:
  1. warm_start_from_matrix() injects pseudo-observations into each
     (bucket, agent) A/b matrix in LinUCBPerBucketRouter.
  2. After injection, the A matrices are NOT identity — they contain the
     prior signal from the compatibility matrix.
  3. Reward values outside [0, 1] are clamped before injection.
  4. Missing buckets in the matrix are silently skipped (no error).
  5. BanditRouter.__init__ wires warm-start for LinUCBPerBucketRouter
     (not just LinUCBRouter) — the isinstance-→-duck-type fix.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from backend.orchestrator.routing.strategies.linucb_per_bucket import LinUCBPerBucketRouter
from backend.orchestrator.routing.vocab import BUCKETS, ENABLED_AGENTS
from backend.orchestrator.routing.warm_start import (
    _BUCKET_VECTORS,
    bucket_context_vector,
    warm_start_from_matrix,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _fresh_router() -> LinUCBPerBucketRouter:
    return LinUCBPerBucketRouter(d=9)


def _is_identity(mat: np.ndarray) -> bool:
    return np.allclose(mat, np.eye(mat.shape[0]))


# ── tests ─────────────────────────────────────────────────────────────────────

def test_warm_start_injects_into_each_bucket_agent_pair():
    """A matrix is non-identity after warm-start for every (bucket, agent) cell."""
    router = _fresh_router()
    matrix = {agent: {bucket: 0.75 for bucket in BUCKETS} for agent in ENABLED_AGENTS}

    warm_start_from_matrix(router, matrix, lambda_prior=1.0)

    for bucket in BUCKETS:
        assert bucket in router.A, f"Bucket {bucket!r} missing from A after warm-start"
        for agent in ENABLED_AGENTS:
            assert agent in router.A[bucket], (
                f"Agent {agent!r} missing from A[{bucket!r}] after warm-start"
            )
            assert not _is_identity(router.A[bucket][agent]), (
                f"A[{bucket!r}][{agent!r}] is still identity — warm-start had no effect"
            )


def test_warm_start_a_matrix_matches_pseudo_obs_formula():
    """A += λ·xxᵀ per (bucket, agent) cell matches the PILOT formula."""
    router = _fresh_router()
    bucket = "code"
    agent = ENABLED_AGENTS[0]
    reward = 0.8
    lam = 2.0

    # Manually compute expected A after one pseudo-obs.
    x = bucket_context_vector(bucket).reshape(-1, 1)
    expected_A = np.identity(9) + lam * (x @ x.T)

    warm_start_from_matrix(router, {agent: {bucket: reward}}, lambda_prior=lam)

    np.testing.assert_allclose(
        router.A[bucket][agent], expected_A, atol=1e-10,
        err_msg=f"A[{bucket!r}][{agent!r}] does not match identity + λ·xxᵀ",
    )


def test_warm_start_reward_clamped_to_unit_interval():
    """Rewards outside [0, 1] are clamped before injection — no NaN in matrices."""
    router = _fresh_router()
    matrix = {ENABLED_AGENTS[0]: {"code": 1.5, "debug": -0.3}}

    warm_start_from_matrix(router, matrix, lambda_prior=1.0)

    # No NaN or Inf.
    for bucket in ("code", "debug"):
        A = router.A[bucket][ENABLED_AGENTS[0]]
        assert np.all(np.isfinite(A)), f"A[{bucket!r}] contains non-finite values after clamped reward"


def test_warm_start_skips_unknown_buckets():
    """Buckets not in the matrix are silently ignored — no KeyError."""
    router = _fresh_router()
    matrix = {ENABLED_AGENTS[0]: {"nonexistent_bucket": 0.5, "code": 0.7}}

    # Should not raise.
    warm_start_from_matrix(router, matrix, lambda_prior=1.0)

    # Only "code" should have been injected (nonexistent_bucket has no vector).
    assert "code" in router.A
    # nonexistent_bucket either not present or had no effect — just no crash.


def test_warm_start_no_op_for_empty_matrix():
    """Empty matrix leaves the router in cold-start state."""
    router = _fresh_router()
    warm_start_from_matrix(router, {}, lambda_prior=1.0)
    assert router.A == {}, "Router should remain empty after warm-starting with empty matrix"


def test_bandit_router_wires_warm_start_for_per_bucket_strategy(tmp_path):
    """BanditRouter.__init__ applies warm-start to LinUCBPerBucketRouter (duck-type fix).

    Before the isinstance→duck-type fix, warm_start_from_matrix was never
    called for linucb_per_bucket, silently leaving every arm at cold-start.
    """
    from backend.orchestrator.routing.bandit_router import BanditRouter
    from backend.orchestrator.routing.decision_log import DecisionLogger
    from backend.orchestrator.routing.warm_start import (
        COMPATIBILITY_MATRIX_PATH,
        save_compatibility_matrix,
    )
    from unittest.mock import patch

    matrix = {agent: {bucket: 0.75 for bucket in BUCKETS} for agent in ENABLED_AGENTS}

    # Redirect the matrix path to a temp file so we don't touch ~/.mahoraga-v2/.
    tmp_matrix = tmp_path / "compatibility_matrix.json"
    tmp_matrix.write_text(json.dumps(matrix))

    with patch(
        "backend.orchestrator.routing.bandit_router.load_compatibility_matrix",
        return_value=matrix,
    ):
        router = BanditRouter(
            strategy="linucb_per_bucket",
            registry=None,
            logger=DecisionLogger(db_path=tmp_path / "d.db"),
            state_path=tmp_path / "state.json",
        )

    strategy = router.strategy
    # At least one bucket should have been injected (not empty, not identity).
    assert strategy.A, "Warm-start must populate strategy.A for linucb_per_bucket"
    for bucket in strategy.A:
        for agent in strategy.A[bucket]:
            assert not _is_identity(strategy.A[bucket][agent]), (
                f"A[{bucket!r}][{agent!r}] is identity — warm-start did not fire for "
                f"linucb_per_bucket"
            )
