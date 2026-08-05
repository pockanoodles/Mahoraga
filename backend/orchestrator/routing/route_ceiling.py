"""
route_ceiling.py — how much can *any* router learn from this data?

Era 20 quantified a +11.6-pt gap between the per-prompt oracle and the best
static arm and called it "the motivation for semantic routing." Era 23 ruled
out the reward as the confounder and left A1 (semantic routing) as the last
standing lever. This module asks the question that was never asked: **is the
oracle gap reachable at all?**

Two ceilings, both computed offline from recorded runs with zero inference.

── A. Arm-selection ceiling ──────────────────────────────────────────────────

The oracle-vs-round-robin gap is not evidence of learnable structure. For a
two-arm cross it is an algebraic identity:

    oracle - round_robin = D / (2n)

where D is the number of prompts on which exactly one arm passed. Any pair of
arms that ever disagree produces a positive gap — including two *identical*
models whose disagreements are pure sampling noise. The gap measures
disagreement, not skill.

What matters is whether the disagreement is *predictable*. `arm_ceiling()`
answers that with a leave-one-out kNN probe — deliberately the same mechanism
episodic memory already uses, so the number is the ceiling of the machinery in
the system, not of some classifier we would never ship. Every prompt is routed
by its k nearest neighbours' *full-information* outcomes (both arms observed —
strictly more than an online learner ever sees), so the probe is an upper
bound. A label-permutation test says whether the result clears chance.

If a full-information LOO probe cannot beat the best static arm, no online
bandit over the same representation can either, and the gap is sampling noise.

── B. Escalation ceiling ─────────────────────────────────────────────────────

The cascade (Era 19) is where this project's real headroom lives: local arm
runs, a judge decides whether to escalate to cloud. `escalation_ceiling()`
places the judge's measured operating point on the achievable frontier —
the pass@1 obtainable at each escalation rate by a perfectly-ranked gate —
and asks whether cheap features add fail-recall *beyond* the judge verdict at
a matched escalation rate (i.e. at matched cost).

── Representations ───────────────────────────────────────────────────────────

Pluggable and dependency-free by default:

  handcraft — the 9-dim TaskContext vector the bandit actually consumes
  lexical   — in-repo TF-IDF (unigram+bigram, sublinear tf, L2), numpy only
  semantic  — MiniLM via EmbeddingService; skipped when unavailable

`semantic` needs `requirements-semantic.txt` installed and the model present.
Where it is missing the probe reports `available: false` rather than guessing.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import numpy as np

from .context import TaskContext

# Probe neighbourhood sizes swept by default. Small k is the regime episodic
# memory runs in (MIN_EPISODES_FOR_BIAS=3); large k approaches the global prior.
DEFAULT_K_VALUES: tuple[int, ...] = (3, 5, 10, 20)

# Permutation resamples for the significance test. 2000 gives a resolution of
# 5e-4 on the p-value, which is well past any threshold we care about.
DEFAULT_PERMUTATIONS = 2_000

# A probe must clear the best static arm by at least this much to count as a
# real gain before we even look at the p-value.
MIN_MEANINGFUL_GAIN = 0.005

# Significance threshold for the permutation test.
ALPHA_LEVEL = 0.05


# ── Representations ───────────────────────────────────────────────────────────


@dataclass
class Representation:
    """A text -> unit-vector encoder plus a name and availability flag.

    `matrix` returns one L2-normalised row per text. Unavailable
    representations (e.g. semantic without sentence-transformers) carry
    `available=False` and are reported, not silently dropped.
    """

    name: str
    available: bool
    encode_all: Optional[Callable[[Sequence[str]], np.ndarray]] = None
    detail: str = ""

    def matrix(self, texts: Sequence[str]) -> np.ndarray:
        if not self.available or self.encode_all is None:
            raise RuntimeError(f"representation {self.name!r} is unavailable")
        m = np.asarray(self.encode_all(texts), dtype=np.float64)
        if m.ndim != 2 or m.shape[0] != len(texts):
            raise ValueError(
                f"representation {self.name!r} returned shape {m.shape} for "
                f"{len(texts)} texts"
            )
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        return m / np.maximum(norms, 1e-12)


_TOKEN_RE = re.compile(r"[a-z_][a-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens plus adjacent bigrams."""
    words = _TOKEN_RE.findall(text.lower())
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]


def tfidf_matrix(texts: Sequence[str], *, min_df: int = 2) -> np.ndarray:
    """Sublinear-tf, smoothed-idf, L2-normalised TF-IDF. numpy only.

    Kept in-repo deliberately: this module must run in CI and on a laptop with
    nothing but the base requirements installed.
    """
    docs = [_tokenize(t) for t in texts]
    df: Counter[str] = Counter()
    for d in docs:
        df.update(set(d))
    vocab = {t: i for i, t in enumerate(sorted(t for t, c in df.items() if c >= min_df))}
    n = len(texts)
    m = np.zeros((n, max(len(vocab), 1)), dtype=np.float64)
    if not vocab:
        return m
    idf = np.zeros(len(vocab))
    for t, i in vocab.items():
        idf[i] = math.log((1.0 + n) / (1.0 + df[t])) + 1.0
    for row, d in enumerate(docs):
        counts = Counter(d)
        for t, c in counts.items():
            j = vocab.get(t)
            if j is not None:
                m[row, j] = (1.0 + math.log(c)) * idf[j]
    return m


def handcraft_representation() -> Representation:
    """The 9-dim TaskContext vector — what LinUCB actually sees today."""

    def enc(texts: Sequence[str]) -> np.ndarray:
        return np.array([TaskContext.from_task({"goal": t}).to_vector() for t in texts])

    return Representation("handcraft", True, enc, detail="9-dim TaskContext (bandit input)")


def lexical_representation(min_df: int = 2) -> Representation:
    """In-repo TF-IDF over unigrams + bigrams."""

    def enc(texts: Sequence[str]) -> np.ndarray:
        return tfidf_matrix(texts, min_df=min_df)

    return Representation("lexical", True, enc, detail="TF-IDF unigram+bigram, in-repo")


def semantic_representation() -> Representation:
    """MiniLM sentence embeddings via the existing EmbeddingService.

    Unavailable (rather than fatal) when sentence-transformers or the model
    weights are missing — the caller reports the gap honestly.
    """
    try:
        from .embeddings import EmbeddingService, MODEL_ID

        svc = EmbeddingService()
        if not svc.available:
            return Representation(
                "semantic", False, detail="EmbeddingService reports unavailable"
            )
    except Exception as exc:  # pragma: no cover - import/env dependent
        return Representation("semantic", False, detail=f"unavailable: {exc}")

    def enc(texts: Sequence[str]) -> np.ndarray:
        vecs = svc.encode_batch(list(texts))
        if any(v is None for v in vecs):
            raise RuntimeError("embedding service returned None mid-batch")
        return np.array(vecs)

    return Representation("semantic", True, enc, detail=f"{MODEL_ID} via EmbeddingService")


def default_representations() -> list[Representation]:
    return [handcraft_representation(), lexical_representation(), semantic_representation()]


# ── Leave-one-out kNN probe ───────────────────────────────────────────────────


def _loo_neighbour_order(X: np.ndarray) -> np.ndarray:
    """For each row, the indices of every other row by descending cosine.

    Rows are unit-normalised, so the Gram matrix is cosine similarity. The
    diagonal is masked to -inf: a prompt is never its own neighbour, which is
    the whole point of leave-one-out.
    """
    sim = X @ X.T
    np.fill_diagonal(sim, -np.inf)
    return np.argsort(-sim, axis=1), sim


def _neighbour_weights(order: np.ndarray, sim: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """(top-k neighbour indices, their vote weights) for every row.

    Weights are cosine similarities clipped at 0; a row whose neighbours are
    all orthogonal-or-worse falls back to uniform voting rather than dividing
    by zero. Computed once per k and reused across permutations — the
    neighbourhood structure depends only on the texts, which never change.
    """
    nn = order[:, :k]
    w = np.clip(np.take_along_axis(sim, nn, axis=1), 0.0, None)
    dead = w.sum(axis=1) <= 0.0
    if dead.any():
        w[dead] = 1.0
    return nn, w


def _knn_policy_pass(
    P: np.ndarray, nn: np.ndarray, w: np.ndarray
) -> tuple[float, np.ndarray]:
    """Route every prompt by its k nearest neighbours' recorded outcomes.

    `P[i, a]` is 1.0 iff arm `a` passed prompt `i`. Ties break toward the
    lowest arm index (argmax), which is stable across runs. Returns realised
    pass@1 and the per-prompt arm picks.
    """
    est = np.einsum("ij,ija->ia", w, P[nn]) / w.sum(axis=1)[:, None]
    picks = np.argmax(est, axis=1)
    return float(P[np.arange(len(P)), picks].mean()), picks


@dataclass
class ProbeResult:
    """One representation's LOO-kNN result at its best k."""

    representation: str
    available: bool
    detail: str = ""
    best_k: Optional[int] = None
    pass_at_1: Optional[float] = None
    by_k: dict[int, float] = field(default_factory=dict)
    gain_over_best_static: Optional[float] = None
    p_value: Optional[float] = None
    n_permutations: int = 0

    def as_dict(self) -> dict:
        return {
            "representation": self.representation,
            "available": self.available,
            "detail": self.detail,
            "best_k": self.best_k,
            "pass_at_1": round(self.pass_at_1, 4) if self.pass_at_1 is not None else None,
            "by_k": {k: round(v, 4) for k, v in self.by_k.items()},
            "gain_over_best_static": (
                round(self.gain_over_best_static, 4)
                if self.gain_over_best_static is not None else None
            ),
            "p_value": round(self.p_value, 4) if self.p_value is not None else None,
            "n_permutations": self.n_permutations,
        }


def probe_representation(
    texts: Sequence[str],
    P: np.ndarray,
    rep: Representation,
    *,
    k_values: Sequence[int] = DEFAULT_K_VALUES,
    n_permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = 42,
) -> ProbeResult:
    """LOO-kNN routing under one representation, with a permutation test.

    The null shuffles the *outcome rows* across prompts, destroying any
    association between text and which arm wins while preserving the marginal
    pass rates exactly. The p-value is the fraction of shuffles whose best-k
    probe matches or beats the observed one — so it already accounts for the
    optimism of picking the best k.
    """
    if not rep.available:
        return ProbeResult(rep.name, False, rep.detail)

    X = rep.matrix(texts)
    order, sim = _loo_neighbour_order(X)
    ks = [k for k in k_values if 1 <= k < len(texts)]
    if not ks:
        return ProbeResult(rep.name, False, f"{rep.detail} (too few prompts for any k)")
    neighbourhoods = {k: _neighbour_weights(order, sim, k) for k in ks}
    by_k = {k: _knn_policy_pass(P, *neighbourhoods[k])[0] for k in ks}
    best_k = max(by_k, key=by_k.get)
    observed = by_k[best_k]
    best_static = float(P.mean(axis=0).max())

    p_value = None
    if n_permutations > 0:
        rng = np.random.default_rng(seed)
        at_least = 0
        for _ in range(n_permutations):
            Pp = P[rng.permutation(len(texts))]
            null_best = max(_knn_policy_pass(Pp, *neighbourhoods[k])[0] for k in ks)
            if null_best >= observed - 1e-12:
                at_least += 1
        p_value = (at_least + 1) / (n_permutations + 1)

    return ProbeResult(
        representation=rep.name,
        available=True,
        detail=rep.detail,
        best_k=best_k,
        pass_at_1=observed,
        by_k=by_k,
        gain_over_best_static=observed - best_static,
        p_value=p_value,
        n_permutations=n_permutations,
    )


# ── A. Arm-selection ceiling ──────────────────────────────────────────────────


def outcome_matrix(env) -> tuple[list[str], np.ndarray]:
    """(prompts, P) from a `reward_fidelity_replay.Environment`.

    `P[i, a]` = 1.0 iff arm `env.arms[a]` truly passed prompt i (hidden tests).
    """
    P = np.array(
        [[1.0 if env.outcomes[(p, a)].passed else 0.0 for a in env.arms] for p in env.prompts]
    )
    return list(env.prompts), P


def disagreement_stats(P: np.ndarray) -> dict:
    """The decomposition that makes the oracle gap interpretable.

    For two arms, `oracle - round_robin == n_split / (2n)` exactly; the
    identity is asserted (not merely reported) so a future change to the
    baseline definitions cannot silently break the interpretation.
    """
    n, n_arms = P.shape
    per_prompt_passes = P.sum(axis=1)
    all_pass = int((per_prompt_passes == n_arms).sum())
    none_pass = int((per_prompt_passes == 0).sum())
    split = int(n - all_pass - none_pass)

    round_robin = float(P.mean())
    oracle = float(P.max(axis=1).mean())
    statics = P.mean(axis=0)
    best_static = float(statics.max())

    out = {
        "n_prompts": n,
        "n_arms": n_arms,
        "all_pass": all_pass,
        "none_pass": none_pass,
        "split": split,
        "round_robin": round(round_robin, 4),
        "best_static": round(best_static, 4),
        "oracle": round(oracle, 4),
        "oracle_over_round_robin": round(oracle - round_robin, 4),
        "oracle_over_best_static": round(oracle - best_static, 4),
    }
    if n_arms == 2 and n:
        identity = split / (2 * n)
        out["split_over_2n"] = round(identity, 4)
        out["identity_holds"] = abs((oracle - round_robin) - identity) < 1e-9
    return out


@dataclass
class ArmCeiling:
    stats: dict
    probes: list[ProbeResult]
    verdict: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "stats": self.stats,
            "probes": [p.as_dict() for p in self.probes],
            "verdict": self.verdict,
            "detail": self.detail,
        }


def arm_ceiling(
    env,
    *,
    representations: Optional[Sequence[Representation]] = None,
    k_values: Sequence[int] = DEFAULT_K_VALUES,
    n_permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = 42,
) -> ArmCeiling:
    """Is per-prompt arm selection learnable on this recorded cross?

    LEARNABLE requires a probe that both clears the best static arm by
    MIN_MEANINGFUL_GAIN and survives the permutation test at ALPHA_LEVEL.
    Anything else is NOT-DETECTABLE: with a full-information LOO probe failing,
    an online bandit over the same representation cannot do better, and the
    oracle gap on this data is sampling noise rather than routable skill.
    """
    prompts, P = outcome_matrix(env)
    stats = disagreement_stats(P)
    reps = list(representations) if representations is not None else default_representations()
    probes = [
        probe_representation(
            prompts, P, r, k_values=k_values, n_permutations=n_permutations, seed=seed
        )
        for r in reps
    ]

    winners = [
        p for p in probes
        if p.available
        and p.gain_over_best_static is not None
        and p.gain_over_best_static >= MIN_MEANINGFUL_GAIN
        and p.p_value is not None
        and p.p_value <= ALPHA_LEVEL
    ]
    if winners:
        best = max(winners, key=lambda p: p.gain_over_best_static or 0.0)
        verdict = "LEARNABLE"
        detail = (
            f"{best.representation} k={best.best_k} reaches {best.pass_at_1:.4f}, "
            f"{best.gain_over_best_static:+.4f} over best static "
            f"(p={best.p_value:.4f}) — per-prompt routing has real signal here."
        )
    else:
        avail = [p for p in probes if p.available]
        best = max(avail, key=lambda p: p.gain_over_best_static or -1.0) if avail else None
        verdict = "NOT-DETECTABLE"
        detail = (
            "no representation beat the best static arm by "
            f"{MIN_MEANINGFUL_GAIN} at p<={ALPHA_LEVEL}"
        )
        if best is not None and best.pass_at_1 is not None:
            p_txt = f", p={best.p_value:.4f}" if best.p_value is not None else " (no permutation test)"
            detail += (
                f"; best was {best.representation} k={best.best_k} at "
                f"{best.pass_at_1:.4f} ({best.gain_over_best_static:+.4f}{p_txt})"
            )
        detail += (
            f". The {stats['oracle_over_best_static']:+.4f} oracle gap comes from "
            f"{stats['split']} split prompts whose winner is unpredictable from the "
            "prompt — it is disagreement, not routable skill."
        )
    return ArmCeiling(stats=stats, probes=probes, verdict=verdict, detail=detail)


# ── B. Escalation ceiling ─────────────────────────────────────────────────────


@dataclass
class CascadeRow:
    """One recorded local-then-maybe-cloud cascade decision."""

    prompt: str
    local_output: str
    local_passed: bool
    judge_accept: bool
    escalated: bool
    cloud_passed: bool
    cloud_cost: float
    final_passed: bool
    bucket: str = "code"
    # False when the recorded run never produced a cloud answer for this row.
    # Such rows cannot be counterfactually escalated — we do not know what
    # cloud would have said, and scoring the absent answer as a failure would
    # understate every gate that wanted to escalate it.
    cloud_recorded: bool = True


def load_cascade_rows(path) -> list[CascadeRow]:
    """Read a `bench live-route` results file into CascadeRow records.

    Rows missing the local-side fields are skipped rather than defaulted —
    a cascade row without `local_passed` carries no information for this
    analysis and silently coercing it to False would invent failures.
    """
    import json
    from pathlib import Path

    rows: list[CascadeRow] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "local_passed" not in r or "judge_verdict" not in r:
            continue
        rows.append(CascadeRow(
            prompt=r.get("prompt_full") or r.get("prompt") or "",
            local_output=r.get("local_output") or r.get("output_full") or "",
            local_passed=bool(r["local_passed"]),
            judge_accept=bool(r["judge_verdict"]),
            escalated=bool(r.get("escalated", False)),
            cloud_passed=bool(r.get("cloud_passed", False)),
            cloud_cost=float(r.get("cloud_cost") or 0.0),
            final_passed=bool(r.get("final_passed", r["local_passed"])),
            bucket=r.get("bucket") or "code",
            cloud_recorded="cloud_passed" in r and bool(r.get("cloud_output")),
        ))
    return rows


def _cascade_pass(rows: Sequence[CascadeRow], escalate: np.ndarray) -> tuple[float, float]:
    """(pass@1, $/1k tasks) for an arbitrary escalate mask over recorded rows.

    Escalated rows take the recorded cloud outcome; un-escalated rows keep the
    local one. A row whose cloud outcome was never recorded cannot be
    counterfactually escalated, so the mask is ignored for it — inventing an
    outcome either way would bias every policy that wanted to escalate it.
    """
    n = len(rows)
    if n == 0:
        return 0.0, 0.0
    passes = 0.0
    cost = 0.0
    for r, esc in zip(rows, escalate):
        if esc and r.cloud_recorded:
            passes += 1.0 if r.cloud_passed else 0.0
            cost += r.cloud_cost
        else:
            passes += 1.0 if r.local_passed else 0.0
    return passes / n, cost / n * 1000.0


def escalation_frontier(rows: Sequence[CascadeRow], *, points: int = 21) -> list[dict]:
    """Best achievable pass@1 within each escalation *budget* — the oracle gate.

    A perfect gate spends its budget on the rows the local arm failed and
    cloud got right (gain +1), and declines to escalate anything else: a
    neutral escalation only burns money, and a negative one also loses
    quality. The budget is therefore a cap, not a quota, which makes the
    curve a genuine non-decreasing upper envelope — the thing a learned gate
    is measured against.

    `budget_rate` is the cap; `esc_rate` is what the oracle actually spent.
    Rows whose cloud outcome was never recorded are not escalation candidates.
    """
    n = len(rows)
    if n == 0:
        return []
    # Gain from escalating each row: +1 rescue, -1 regression, 0 neutral.
    gain = np.array([
        ((1.0 if r.cloud_passed else 0.0) - (1.0 if r.local_passed else 0.0))
        if r.cloud_recorded else -np.inf
        for r in rows
    ])
    order = np.argsort(-gain, kind="stable")
    n_worth = int((gain > 0).sum())
    out = []
    for i in range(points):
        budget_rate = i / (points - 1) if points > 1 else 0.0
        k = min(int(round(budget_rate * n)), n_worth)
        esc = np.zeros(n, dtype=bool)
        esc[order[:k]] = True
        p, c = _cascade_pass(rows, esc)
        out.append({
            "budget_rate": round(budget_rate, 4),
            "esc_rate": round(k / n, 4),
            "pass_at_1": round(p, 4),
            "cost_per_1k": round(c, 2),
        })
    return out


def judge_operating_point(rows: Sequence[CascadeRow]) -> dict:
    """Where the recorded judge sits: recall, over-escalations, pass@1, cost."""
    n = len(rows)
    esc = np.array([not r.judge_accept for r in rows])
    failed = np.array([not r.local_passed for r in rows])
    caught = int((esc & failed).sum())
    n_failed = int(failed.sum())
    over = int((esc & ~failed).sum())
    p, c = _cascade_pass(rows, esc)
    return {
        "esc_rate": round(float(esc.mean()), 4) if n else 0.0,
        "fail_recall": round(caught / n_failed, 4) if n_failed else None,
        "n_caught": caught,
        "n_failed": n_failed,
        "over_escalations": over,
        "pass_at_1": round(p, 4),
        "cost_per_1k": round(c, 2),
    }


def _loo_knn_scores(X: np.ndarray, y: np.ndarray, k: int) -> np.ndarray:
    """Leave-one-out kNN estimate of P(y=1) for every row."""
    order, sim = _loo_neighbour_order(X)
    nn, w = _neighbour_weights(order, sim, k)
    return (w * y[nn]).sum(axis=1) / w.sum(axis=1)


@dataclass
class EscalationCeiling:
    n_rows: int
    always_local: float
    always_cloud: float
    always_cloud_cost: float
    recorded: dict
    judge: dict
    frontier: list[dict]
    frontier_at_judge_rate: Optional[dict]
    probes: list[dict]
    verdict: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "n_rows": self.n_rows,
            "always_local": round(self.always_local, 4),
            "always_cloud": round(self.always_cloud, 4),
            "always_cloud_cost": round(self.always_cloud_cost, 2),
            "recorded": self.recorded,
            "judge": self.judge,
            "frontier": self.frontier,
            "frontier_at_judge_rate": self.frontier_at_judge_rate,
            "probes": self.probes,
            "verdict": self.verdict,
            "detail": self.detail,
        }


def escalation_ceiling(
    rows: Sequence[CascadeRow],
    *,
    representations: Optional[Sequence[Representation]] = None,
    k_values: Sequence[int] = DEFAULT_K_VALUES,
) -> EscalationCeiling:
    """Can a learned gate beat the judge at matched cost?

    Each representation gets a LOO-kNN failure predictor over the local
    *output* text, in two flavours: features alone, and features combined with
    the judge verdict (the judge is a strong prior, so the interesting question
    is the increment on top of it). Both are compared to the judge at the
    judge's own escalation rate — matched rate means matched cloud spend, so
    any pass@1 difference is free.
    """
    n = len(rows)
    if n == 0:
        raise ValueError("no cascade rows to analyse")

    local = np.array([1.0 if r.local_passed else 0.0 for r in rows])
    judge_accept = np.array([1.0 if r.judge_accept else 0.0 for r in rows])
    cloud = np.array([1.0 if r.cloud_passed else 0.0 for r in rows])
    always_cloud_cost = float(sum(r.cloud_cost for r in rows) / n * 1000.0)

    recorded_esc = np.array([r.escalated for r in rows])
    rec_p, rec_c = _cascade_pass(rows, recorded_esc)
    judge = judge_operating_point(rows)
    frontier = escalation_frontier(rows)
    at_rate = min(
        frontier, key=lambda f: abs(f["esc_rate"] - judge["esc_rate"])
    ) if frontier else None

    reps = list(representations) if representations is not None else default_representations()
    texts = [r.local_output for r in rows]
    budget = int(round(judge["esc_rate"] * n))
    probes: list[dict] = []
    for rep in reps:
        if not rep.available:
            probes.append({"representation": rep.name, "available": False, "detail": rep.detail})
            continue
        X = rep.matrix(texts)
        for with_judge in (False, True):
            # Blend: the judge verdict enters as an extra unit-weighted column
            # so cosine neighbourhoods are formed over "text + verdict".
            Xa = np.hstack([X, judge_accept.reshape(-1, 1)]) if with_judge else X
            Xa = Xa / np.maximum(np.linalg.norm(Xa, axis=1, keepdims=True), 1e-12)
            best = None
            for k in [k for k in k_values if 1 <= k < n]:
                score = _loo_knn_scores(Xa, local, k)
                esc = np.zeros(n, dtype=bool)
                # Escalate the `budget` rows least likely to have passed.
                esc[np.argsort(score, kind="stable")[:budget]] = True
                p, c = _cascade_pass(rows, esc)
                caught = int((esc & (local == 0)).sum())
                cand = {
                    "k": k, "pass_at_1": round(p, 4), "cost_per_1k": round(c, 2),
                    "fail_recall": round(caught / max(int((local == 0).sum()), 1), 4),
                }
                if best is None or cand["pass_at_1"] > best["pass_at_1"]:
                    best = cand
            probes.append({
                "representation": rep.name,
                "available": True,
                "with_judge": with_judge,
                "matched_esc_rate": round(budget / n, 4),
                **(best or {}),
                "delta_vs_judge": round((best or {}).get("pass_at_1", 0.0) - judge["pass_at_1"], 4),
            })

    beats = [
        p for p in probes
        if p.get("available") and p.get("delta_vs_judge", 0.0) >= MIN_MEANINGFUL_GAIN
    ]
    if beats:
        best = max(beats, key=lambda p: p["delta_vs_judge"])
        verdict = "GATE-IMPROVABLE"
        detail = (
            f"{best['representation']} (judge feature: {best['with_judge']}) k={best['k']} "
            f"reaches {best['pass_at_1']:.4f} at the judge's own escalation rate "
            f"({best['delta_vs_judge']:+.4f} over the judge, same spend)."
        )
    else:
        verdict = "JUDGE-SUFFICIENT"
        headroom = (
            at_rate["pass_at_1"] - judge["pass_at_1"] if at_rate else 0.0
        )
        detail = (
            "no representation improved on the judge verdict at matched escalation "
            f"rate — the judge is a sufficient statistic for this decision here. "
            f"Oracle-gate headroom at the same rate is {headroom:+.4f}, so the "
            "remaining gain is in the judge's own recall, not in re-ranking its output."
        )

    return EscalationCeiling(
        n_rows=n,
        always_local=float(local.mean()),
        always_cloud=float(cloud.mean()),
        always_cloud_cost=always_cloud_cost,
        recorded={"esc_rate": round(float(recorded_esc.mean()), 4),
                  "pass_at_1": round(rec_p, 4), "cost_per_1k": round(rec_c, 2)},
        judge=judge,
        frontier=frontier,
        frontier_at_judge_rate=at_rate,
        probes=probes,
        verdict=verdict,
        detail=detail,
    )


# ── Entrypoint ────────────────────────────────────────────────────────────────


def run_ceiling(
    bank_path=None,
    results_paths: Optional[Sequence] = None,
    cascade_path=None,
    *,
    representations: Optional[Sequence[Representation]] = None,
    k_values: Sequence[int] = DEFAULT_K_VALUES,
    n_permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = 42,
) -> dict:
    """Run whichever ceilings the supplied data supports.

    The arm ceiling needs a force-explore cross (+ the gold bank to re-grade
    it); the escalation ceiling needs a live-route cascade file. Either may be
    omitted — the report carries only the sections it could compute.
    """
    report: dict = {"arm_ceiling": None, "escalation_ceiling": None}

    if bank_path is not None and results_paths:
        from .reward_fidelity_replay import build_environment

        env = build_environment(bank_path, list(results_paths))
        report["arm_ceiling"] = arm_ceiling(
            env, representations=representations, k_values=k_values,
            n_permutations=n_permutations, seed=seed,
        ).as_dict()
        report["arm_ceiling"]["environment"] = {
            "n_prompts": len(env.prompts),
            "arms": env.arms,
            "n_discriminating": len(env.discriminating),
            "n_dropped_incomplete": env.n_dropped_incomplete,
        }

    if cascade_path is not None:
        rows = load_cascade_rows(cascade_path)
        if rows:
            report["escalation_ceiling"] = escalation_ceiling(
                rows, representations=representations, k_values=k_values
            ).as_dict()

    return report
