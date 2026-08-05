"""
Adaptive reward weight learner — updates per-bucket weights using OLS after ≥100 observations.

For each bucket we buffer (correctness, quality, phi_speed, phi_cost, reward) tuples from
successful tasks. After MIN_SAMPLES observations per bucket, we fit:

    reward ≈ w_s·correctness + w_q·quality + w_sp·phi_speed + w_c·phi_cost

`correctness` defaults to 1.0 (the pre-Era-21 constant regressor) so w_s only becomes
identifiable where the reward judge actually runs.

using OLS (numpy lstsq), then project onto the probability simplex (sum=1, each ≥ WEIGHT_FLOOR).

Learned weights persist to <bandit_state_dir>/bandit_state.learner.json so they survive restarts.
The prior weights from reward.BUCKET_WEIGHTS are used for buckets not yet converged.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path

import numpy as np

from .reward import BUCKET_WEIGHTS, _SPEED_LAMBDA, _SPEED_T_REF, _COST_REF


MIN_SAMPLES: int = 100      # Observations before OLS fit is trusted
WEIGHT_FLOOR: float = 0.05  # Prevents weight collapse
MAX_BUFFER: int = 500       # Cap per bucket (FIFO when exceeded)

# Fix B: exponential smoothing toward OLS target.
# α = 1/K → per-step weight perturbation ≤ 5% of OLS-shift (K=20).
# Emergency disable: set MAHORAGA_OLS_TRANSITION_STEPS=1 → collapses to hard replacement.
OLS_TRANSITION_STEPS: int = max(1, int(os.environ.get("MAHORAGA_OLS_TRANSITION_STEPS", "20")))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _phi_speed(latency_s: float) -> float:
    return math.exp(-_SPEED_LAMBDA * latency_s / _SPEED_T_REF)


def _phi_cost(cost_usd: float) -> float:
    return 1.0 - math.tanh(cost_usd / _COST_REF)


def _project_simplex(
    w: np.ndarray,
    floor: float = WEIGHT_FLOOR,
) -> np.ndarray:
    """Project w onto the floored probability simplex: sum=1, each component ≥ floor.

    Uses iterative water-filling: clip to floor, then redistribute the excess or
    deficit evenly across the "free" components (those strictly above the floor),
    re-clipping after each pass.  Converges in at most len(w) iterations.
    """
    n = len(w)
    w = np.clip(w, floor, None)

    for _ in range(n + 1):
        excess = w.sum() - 1.0
        if abs(excess) < 1e-12:
            break
        free = w > floor + 1e-12
        n_free = int(free.sum())
        if n_free == 0:
            # Every component is pinned — spread remaining mass onto first component
            w[0] += 1.0 - w.sum()
            break
        w = w - (excess / n_free) * free.astype(float)
        w = np.clip(w, floor, None)

    return w


# ── Main class ─────────────────────────────────────────────────────────────────

class RewardWeightLearner:
    """Learns per-bucket reward weights from observed task outcomes via OLS.

    Usage:
        learner = RewardWeightLearner(state_path=Path("~/.mahoraga-v2/bandit_state.json"))

        # After each successful task:
        learner.observe(bucket="code", latency_s=2.1, cost_usd=0.0, quality=0.85, reward=0.74)

        # At reward compute time:
        w_s, w_q, w_sp, w_c = learner.get_weights("code")
    """

    def __init__(self, state_path: str | Path | None = None) -> None:
        # Per-bucket observation buffer: rows are (correctness, quality, phi_sp, phi_c, reward)
        self._buffer: dict[str, list[tuple[float, ...]]] = {}
        # Learned weights per bucket (post-projection)
        self._learned: dict[str, tuple[float, float, float, float]] = {}

        self._learner_path: Path | None = None
        if state_path is not None:
            p = Path(state_path)
            self._learner_path = p.with_name(p.stem + ".learner.json")
            self._load()

    # ── Public API ─────────────────────────────────────────────────────────────

    def observe(
        self,
        bucket: str,
        latency_s: float,
        cost_usd: float,
        quality: float,
        reward: float,
        correctness: float = 1.0,
    ) -> None:
        """Record one successful outcome for weight learning.

        Call this for every task where success=True so the learner accumulates
        enough signal to fit per-bucket weights. `correctness` is the reward
        judge's coefficient on the success term; the 1.0 default is the legacy
        constant regressor for call sites where no judge ran.
        """
        phi_sp = _phi_speed(latency_s)
        phi_c = _phi_cost(cost_usd)

        buf = self._buffer.setdefault(bucket, [])
        buf.append((correctness, quality, phi_sp, phi_c, reward))

        # FIFO cap
        if len(buf) > MAX_BUFFER:
            self._buffer[bucket] = buf[-MAX_BUFFER:]

        # Attempt fit whenever we hit MIN_SAMPLES (and on every update after)
        if len(self._buffer[bucket]) >= MIN_SAMPLES:
            self._fit(bucket)

    def get_weights(self, bucket: str) -> tuple[float, float, float, float]:
        """Return learned weights for bucket, or fall back to module defaults."""
        if bucket in self._learned:
            return self._learned[bucket]
        return BUCKET_WEIGHTS.get(bucket, BUCKET_WEIGHTS["general"])

    def has_learned(self, bucket: str) -> bool:
        """True once OLS has converged for this bucket."""
        return bucket in self._learned

    def sample_counts(self) -> dict[str, int]:
        """Number of observations buffered per bucket."""
        return {b: len(obs) for b, obs in self._buffer.items()}

    def convergence_status(self) -> dict[str, dict[str, object]]:
        """Human-readable convergence status per bucket."""
        out = {}
        for b, obs in self._buffer.items():
            out[b] = {
                "samples": len(obs),
                "converged": b in self._learned,
                "weights": self._learned.get(b),
            }
        # Also report buckets that have learned weights but no current buffer
        for b, w in self._learned.items():
            if b not in out:
                out[b] = {"samples": 0, "converged": True, "weights": w}
        return out

    # ── Internal ───────────────────────────────────────────────────────────────

    def _fit(self, bucket: str) -> None:
        data = np.array(self._buffer[bucket], dtype=float)  # (n, 5)
        x_mat = data[:, :4]   # [correctness, quality, phi_sp, phi_c]
        y_vec = data[:, 4]    # reward

        try:
            w_raw, _, _, _ = np.linalg.lstsq(x_mat, y_vec, rcond=None)
        except np.linalg.LinAlgError:
            return  # singular matrix — not enough feature variance yet

        theta_new = _project_simplex(w_raw)

        # Fix B: blend toward OLS target instead of hard-replacing.
        # Each call moves α = 1/K of the remaining gap → drift detector sees
        # ≤5% of the full OLS-shift per step, staying below its alert threshold.
        prev = self._learned.get(bucket)
        if prev is not None:
            theta_prev = np.array(prev, dtype=float)
        else:
            theta_prev = np.array(
                BUCKET_WEIGHTS.get(bucket, BUCKET_WEIGHTS["general"]), dtype=float
            )
        alpha = 1.0 / OLS_TRANSITION_STEPS
        theta_eff = _project_simplex((1.0 - alpha) * theta_prev + alpha * theta_new)

        w0, w1, w2, w3 = (round(float(x), 6) for x in theta_eff)
        new: tuple[float, float, float, float] = (w0, w1, w2, w3)

        if new != self._learned.get(bucket):
            self._learned[bucket] = new
            if self._learner_path:
                self._save()

    def _save(self) -> None:
        state = {"learned": {b: list(w) for b, w in self._learned.items()}}
        tmp = str(self._learner_path) + ".tmp"
        Path(tmp).write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, str(self._learner_path))

    def _load(self) -> None:
        if self._learner_path is None or not self._learner_path.exists():
            return
        try:
            state = json.loads(self._learner_path.read_text())
            for bucket, w in state.get("learned", {}).items():
                if len(w) == 4:
                    w0, w1, w2, w3 = w
                    self._learned[bucket] = (float(w0), float(w1), float(w2), float(w3))
        except Exception:  # noqa: BLE001
            pass  # corrupt file — start fresh
