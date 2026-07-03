# backend/orchestrator/routing/warm_start.py
"""
Warm-start a LinUCB bandit from a compatibility matrix of benchmark results.

Based on PILOT (Panda et al., EMNLP 2025): injecting benchmark pseudo-observations
as A+=λ·xxᵀ, b+=λ·r·x reduces early regret by Ω(‖θ*−θ_prior‖²).
"""
from __future__ import annotations
from pathlib import Path
import json
import numpy as np

from .vocab import BUCKETS

COMPATIBILITY_MATRIX_PATH = Path.home() / ".mahoraga-v2" / "compatibility_matrix.json"
TUNED_HYPERPARAMS_PATH    = Path.home() / ".mahoraga-v2" / "tuned_hyperparams.json"


# Dims: word_count_norm, code_kw_density, is_question, complexity_tier,
#       file_count, has_error_kw, has_creation_kw, has_research_kw,
#       queue_depth_norm
_BUCKET_VECTORS: dict[str, list[float]] = {
    "code":     [0.15, 0.50, 0.0, 0.67, 0.1, 0.0,  0.7, 0.0,  0.0],
    "test":     [0.10, 0.40, 0.0, 0.50, 0.2, 0.0,  0.6, 0.0,  0.0],
    "debug":    [0.15, 0.30, 0.0, 0.67, 0.2, 1.0,  0.3, 0.0,  0.0],
    "research": [0.30, 0.05, 1.0, 0.33, 0.0, 0.0,  0.0, 1.0,  0.0],
    "plan":     [0.25, 0.10, 0.0, 0.67, 0.0, 0.0,  0.5, 0.3,  0.0],
    "review":   [0.20, 0.15, 0.0, 0.33, 0.1, 0.0,  0.1, 0.5,  0.0],
    "refactor": [0.15, 0.35, 0.0, 0.67, 0.3, 0.2,  0.4, 0.0,  0.0],
    "general":  [0.15, 0.10, 0.5, 0.33, 0.0, 0.0,  0.2, 0.3,  0.0],
    "security": [0.20, 0.20, 0.0, 0.67, 0.1, 0.3,  0.2, 0.3,  0.0],
}
assert set(_BUCKET_VECTORS.keys()) == set(BUCKETS), (
    f"_BUCKET_VECTORS keys out of sync with vocab.BUCKETS. "
    f"Missing: {set(BUCKETS) - set(_BUCKET_VECTORS.keys())}. "
    f"Extra: {set(_BUCKET_VECTORS.keys()) - set(BUCKETS)}."
)


def bucket_context_vector(bucket: str) -> np.ndarray:
    """Return the representative 9-dim context vector for a capability bucket."""
    vec = _BUCKET_VECTORS.get(bucket, _BUCKET_VECTORS["general"])
    return np.array(vec, dtype=np.float64)


def warm_start_from_matrix(
    router,  # LinUCBRouter — duck-typed so harness strategies also work
    compatibility_matrix: dict,
    lambda_prior: float = 1.0,
) -> None:
    """Inject benchmark results as pseudo-observations into the bandit.

    compatibility_matrix format:
        {"ollama": {"code": 0.72, "plan": 0.65, ...}, "aider": {...}, ...}

    For each (agent, bucket, reward) triple, calls router.inject_pseudo_obs
    with the bucket's representative context vector.

    lambda_prior=1.0 means one pseudo-observation per cell.
    Higher values = stronger prior, slower adaptation.
    """
    if not compatibility_matrix:
        return
    if not hasattr(router, "inject_pseudo_obs"):
        return
    for agent, bucket_rewards in compatibility_matrix.items():
        for bucket, reward in bucket_rewards.items():
            x = bucket_context_vector(bucket)
            reward = float(max(0.0, min(1.0, reward)))
            router.inject_pseudo_obs(agent, x, reward, lambda_prior=lambda_prior, bucket=bucket)


def load_compatibility_matrix() -> dict | None:
    """Load the compatibility matrix from ~/.mahoraga-v2/compatibility_matrix.json."""
    if not COMPATIBILITY_MATRIX_PATH.exists():
        return None
    try:
        with open(COMPATIBILITY_MATRIX_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_compatibility_matrix(matrix: dict) -> None:
    """Persist the compatibility matrix to ~/.mahoraga-v2/compatibility_matrix.json."""
    COMPATIBILITY_MATRIX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(COMPATIBILITY_MATRIX_PATH, "w") as f:
        json.dump(matrix, f, indent=2)
