"""
LinUCB with per-bucket disjoint linear models.

Same algorithmic core as `linucb.LinUCBRouter` (Li, Chu, Langford, Schapire,
WWW 2010) but with per-classified-bucket A/b matrices instead of one
global pair per agent. Motivated by the empirical findings in
`docs/specs/semantic-routing.md §15.5–§15.6` — the global-θ design couples
buckets through the bandit state, which causes wrong picks in one bucket
to poison routing on others and lets memory faithfully reproduce those
mistakes.

Design (full scope: `docs/specs/per-bucket-bandits.md`):

  state shape:   A[bucket][agent] ∈ R^{d×d}
                 b[bucket][agent] ∈ R^{d×1}
                 t[bucket]        ∈ N

  bucket comes from `classify_bucket(context)` — same deterministic
  classifier used by per-bucket α gating, so names are consistent with
  vocab.BUCKETS: code, debug, plan, research, review, refactor, security,
  test, general.

  cold start: first time a (bucket, agent) pair is seen, initialise from
  one of three paths:
    1. fresh: A = I, b = prior · 1
    2. average-init from existing arms in the SAME bucket
    3. cross-bucket pool: blend with the agent's matrices in OTHER
       buckets, weighted by `bucket_pooling_weight` (0 = full
       specialisation, 1 = full pooling). Default 0.5.

  dLinUCB discount applies per-bucket on each bucket's update — buckets
  with more traffic decay faster. Existing γ=0.98 default preserved.

  persistence: schema version 3. v2 (flat agent → A/b) loads into a
  "default" pseudo-bucket so existing user state is preserved.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from .base import RoutingStrategy
from .static import classify_bucket
from ..vocab import ENABLED_AGENTS


# Cold-start priors: one entry per enabled agent. Equal priors → pure exploration
# at cold start; the bandit differentiates via reward signal, not priors.
# Keys must be a subset of vocab.ENABLED_AGENTS — enforced by test_vocab_prior_agent_subset.
_DEFAULT_PRIORS: dict[str, float] = {
    "ollama:qwen3.5":        0.75,
    "ollama:granite4.1-8b":  0.75,
}
assert set(_DEFAULT_PRIORS.keys()) <= set(ENABLED_AGENTS), (
    f"_DEFAULT_PRIORS references agents not in vocab.ENABLED_AGENTS: "
    f"{set(_DEFAULT_PRIORS.keys()) - set(ENABLED_AGENTS)}"
)

_PERSISTENCE_VERSION = 3
_LEGACY_BUCKET = "default"  # where v1/v2 flat state lands on migration


class LinUCBPerBucketRouter(RoutingStrategy):
    """Disjoint LinUCB with per-classified-bucket θ.

    BanditRouter calls `select_agent(context, available)` and
    `update(context, agent, reward)` exactly as for the v1 strategy.
    Bucket extraction is internal — no router-side changes needed.
    """

    name = "linucb_per_bucket"

    def __init__(
        self,
        d: int = 9,
        alpha: float = 1.0,
        decay: float = 0.98,
        priors: dict[str, float] | None = None,
        bucket_pooling_weight: float = 0.5,
    ) -> None:
        self.d = d
        self.alpha = alpha
        self.decay = decay
        self.priors: dict[str, float] = priors if priors is not None else _DEFAULT_PRIORS
        self.bucket_pooling_weight = float(bucket_pooling_weight)
        # bucket → agent → matrix
        self.A: dict[str, dict[str, np.ndarray]] = {}
        self.b: dict[str, dict[str, np.ndarray]] = {}
        self.t: dict[str, int] = {}

    # ── Initialisation ────────────────────────────────────────────────────────

    def _ensure_bucket(self, bucket: str) -> None:
        if bucket not in self.A:
            self.A[bucket] = {}
            self.b[bucket] = {}
            self.t[bucket] = 0

    def _init_agent(self, bucket: str, agent: str) -> None:
        self._ensure_bucket(bucket)
        if agent in self.A[bucket]:
            return

        existing_in_bucket = [a for a in self.A[bucket] if a != agent]
        # Same agent's matrices in OTHER buckets — used for cross-bucket pooling.
        cross_bucket: list[tuple[np.ndarray, np.ndarray]] = []
        for b_other in self.A:
            if b_other == bucket:
                continue
            if agent in self.A[b_other]:
                cross_bucket.append(
                    (self.A[b_other][agent], self.b[b_other][agent])
                )

        # Three init paths.
        if existing_in_bucket and self.t[bucket] > 0:
            # Average-init from existing bucket arms (mirrors v1 _init_agent).
            avg_A = np.mean(
                [self.A[bucket][a] for a in existing_in_bucket], axis=0,
            )
            avg_b = np.mean(
                [self.b[bucket][a] for a in existing_in_bucket], axis=0,
            )
            init_A = 0.5 * avg_A + 0.5 * np.identity(self.d)
            init_b = 0.5 * avg_b
        elif cross_bucket and self.bucket_pooling_weight > 0:
            # No bucket arms yet; pool from this agent's other-bucket matrices.
            cold_A = np.identity(self.d)
            cold_b = self.priors.get(agent, 0.5) * np.ones((self.d, 1))
            pooled_A = np.mean([m[0] for m in cross_bucket], axis=0)
            pooled_b = np.mean([m[1] for m in cross_bucket], axis=0)
            w = self.bucket_pooling_weight
            init_A = (1.0 - w) * cold_A + w * pooled_A
            init_b = (1.0 - w) * cold_b + w * pooled_b
        else:
            # Pure cold start.
            init_A = np.identity(self.d)
            prior = self.priors.get(agent, 0.5)
            init_b = prior * np.ones((self.d, 1))

        # If we have cross-bucket info AND we used the same-bucket average
        # path, blend in the cross-bucket pool too (otherwise an agent's
        # own cross-bucket experience would be ignored as soon as the
        # bucket has any other arms — too aggressive).
        if existing_in_bucket and cross_bucket and self.bucket_pooling_weight > 0:
            pooled_A = np.mean([m[0] for m in cross_bucket], axis=0)
            pooled_b = np.mean([m[1] for m in cross_bucket], axis=0)
            w = self.bucket_pooling_weight
            init_A = (1.0 - w) * init_A + w * pooled_A
            init_b = (1.0 - w) * init_b + w * pooled_b

        self.A[bucket][agent] = init_A
        self.b[bucket][agent] = init_b

    # ── Public bandit API ─────────────────────────────────────────────────────

    def select_agent(self, context, available_agents: list[str]) -> str:
        if not available_agents:
            raise ValueError("available_agents must not be empty")
        bucket = classify_bucket(context)
        self._ensure_bucket(bucket)
        self.t[bucket] += 1

        x = context.to_vector().reshape(-1, 1)
        best_agent = available_agents[0]
        best_ucb = -float("inf")
        scores: dict[str, dict[str, float]] = {}

        for a in available_agents:
            self._init_agent(bucket, a)
            theta = np.linalg.solve(self.A[bucket][a], self.b[bucket][a])
            exploit = float((x.T @ theta).item())
            explore_sq = float(
                (x.T @ np.linalg.solve(self.A[bucket][a], x)).item()
            )
            explore_sq = max(0.0, explore_sq)
            explore = self.alpha * float(np.sqrt(explore_sq))
            ucb = exploit + explore
            scores[a] = {
                "ucb": round(ucb, 4),
                "exploit": round(exploit, 4),
                "explore": round(explore, 4),
                "variance": round(explore_sq, 6),
                "bucket": bucket,
            }
            if ucb > best_ucb:
                best_ucb = ucb
                best_agent = a

        self._last_scores = scores
        return best_agent

    def update(
        self,
        context,
        agent: str,
        reward: float,
        weight: float = 1.0,
    ) -> None:
        """Update bucket-specific A, b for the agent that actually ran.

        `weight` is the off-policy importance weight: 1.0 in the standard
        case (composer didn't override the bandit), or P_bandit(final_agent)
        when the composer flipped to a different agent. Effectively scales
        how much the matrices learn from this observation. See
        `routing/policy_correction.py`.
        """
        bucket = classify_bucket(context)
        self._init_agent(bucket, agent)
        x = context.to_vector().reshape(-1, 1)
        w = float(weight)
        if self.decay < 1.0:
            self.A[bucket][agent] = (
                self.decay * self.A[bucket][agent] + w * (x @ x.T)
            )
            self.b[bucket][agent] = (
                self.decay * self.b[bucket][agent] + w * reward * x
            )
        else:
            self.A[bucket][agent] = self.A[bucket][agent] + w * (x @ x.T)
            self.b[bucket][agent] = self.b[bucket][agent] + w * reward * x

    def get_scores(self) -> dict:
        return getattr(self, "_last_scores", {})

    def compute_scores(
        self, context, available_agents: list[str]
    ) -> dict:
        """Read-only UCB scoring — no t tick, no _last_scores update."""
        if not available_agents:
            return {}
        bucket = classify_bucket(context)
        self._ensure_bucket(bucket)
        x = context.to_vector().reshape(-1, 1)
        scores: dict[str, dict[str, float]] = {}
        for a in available_agents:
            self._init_agent(bucket, a)
            theta = np.linalg.solve(self.A[bucket][a], self.b[bucket][a])
            exploit = float((x.T @ theta).item())
            explore_sq = float(
                (x.T @ np.linalg.solve(self.A[bucket][a], x)).item()
            )
            explore_sq = max(0.0, explore_sq)
            explore = self.alpha * float(np.sqrt(explore_sq))
            scores[a] = {
                "ucb": round(exploit + explore, 4),
                "exploit": round(exploit, 4),
                "explore": round(explore, 4),
                "variance": round(explore_sq, 6),
                "bucket": bucket,
            }
        return scores

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def get_theta(self, agent: str, bucket: str | None = None) -> np.ndarray:
        if bucket is None:
            bucket = _LEGACY_BUCKET
        self._init_agent(bucket, agent)
        return (
            np.linalg.inv(self.A[bucket][agent]) @ self.b[bucket][agent]
        ).flatten()

    def per_bucket_summary(self) -> dict[str, dict[str, int]]:
        """Counts of arms per bucket — for telemetry / get_stats."""
        return {
            b: {"n_arms": len(self.A[b]), "t": self.t[b]}
            for b in self.A
        }

    # ── Warm-start integration ────────────────────────────────────────────────

    def inject_pseudo_obs(
        self,
        agent: str,
        x: np.ndarray,
        reward: float,
        lambda_prior: float = 1.0,
        bucket: str | None = None,
    ) -> None:
        """Inject one pseudo-observation. Bucket-aware extension.

        - When `bucket` is provided (e.g. by the per-bucket warm-start
          loader): inject only into that bucket.
        - When `bucket` is None: classify from `x` if possible, else
          broadcast across every existing bucket. Broadcast is the
          conservative choice for v1/v2-style global compatibility
          matrices that pre-date per-bucket awareness.
        """
        x_col = x.reshape(-1, 1)
        if bucket is not None:
            self._init_agent(bucket, agent)
            self.A[bucket][agent] += lambda_prior * (x_col @ x_col.T)
            self.b[bucket][agent] += lambda_prior * reward * x_col
            return

        if self.A:
            # Broadcast: distribute the prior across all known buckets so
            # no bucket is starved of warm-start signal.
            for b in self.A:
                self._init_agent(b, agent)
                self.A[b][agent] += lambda_prior * (x_col @ x_col.T)
                self.b[b][agent] += lambda_prior * reward * x_col
        else:
            self._init_agent(_LEGACY_BUCKET, agent)
            self.A[_LEGACY_BUCKET][agent] += lambda_prior * (x_col @ x_col.T)
            self.b[_LEGACY_BUCKET][agent] += lambda_prior * reward * x_col

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_state(self, path: str) -> None:
        state = {
            "version": _PERSISTENCE_VERSION,
            "d": self.d,
            "alpha": self.alpha,
            "decay": self.decay,
            "bucket_pooling_weight": self.bucket_pooling_weight,
            "buckets": {
                bucket: {
                    "t": self.t[bucket],
                    "agents": {
                        a: {
                            "A": self.A[bucket][a].tolist(),
                            "b": self.b[bucket][a].tolist(),
                        }
                        for a in self.A[bucket]
                    },
                }
                for bucket in self.A
            },
        }
        tmp = path + ".tmp"
        Path(tmp).write_text(json.dumps(state, indent=2))
        os.replace(tmp, path)

    def load_state(self, path: str) -> None:
        state = json.loads(Path(path).read_text())
        if state.get("d", self.d) != self.d:
            raise ValueError(
                f"Persisted state has d={state.get('d')}, but router "
                f"expects d={self.d}. Delete the state file to reset."
            )

        version = state.get("version", 1)

        # Reset state before loading.
        self.A = {}
        self.b = {}
        self.t = {}

        if version >= _PERSISTENCE_VERSION and "buckets" in state:
            # v3+ nested format.
            for bucket, bdata in state["buckets"].items():
                self._ensure_bucket(bucket)
                self.t[bucket] = bdata.get("t", 0)
                for a, mdata in bdata.get("agents", {}).items():
                    self.A[bucket][a] = np.array(mdata["A"])
                    self.b[bucket][a] = np.array(mdata["b"]).reshape(-1, 1)
        elif "agents" in state:
            # v1/v2 flat format. Park into the legacy bucket so user state
            # is preserved across the upgrade. Subsequent observations on
            # other buckets bootstrap fresh per-bucket state via
            # cross-bucket pooling.
            self._ensure_bucket(_LEGACY_BUCKET)
            self.t[_LEGACY_BUCKET] = state.get("t", 0)
            for a, mdata in state["agents"].items():
                self.A[_LEGACY_BUCKET][a] = np.array(mdata["A"])
                self.b[_LEGACY_BUCKET][a] = np.array(mdata["b"]).reshape(-1, 1)
        # else: empty state — leave as freshly initialised.
