"""Tests for route_ceiling — the "what can any router learn?" analyzer.

The synthetic fixtures are the point of these tests: a *planted-signal* cross
where the winning arm is perfectly predictable from the prompt text, and a
*pure-noise* cross where it is not. The analyzer must call the first LEARNABLE
and the second NOT-DETECTABLE. Anything that passes both is measuring
something real rather than reproducing whatever the recorded data happens to
say today.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass

import numpy as np
import pytest

from backend.orchestrator.routing import route_ceiling as rc


# ── Fixtures: synthetic environments ──────────────────────────────────────────


@dataclass
class _FakeOutcome:
    passed: bool


class _FakeEnv:
    """Minimal stand-in for reward_fidelity_replay.Environment."""

    def __init__(self, prompts, arms, passed):
        self.prompts = list(prompts)
        self.arms = list(arms)
        self.outcomes = {
            (p, a): _FakeOutcome(bool(passed[i][j]))
            for i, p in enumerate(self.prompts)
            for j, a in enumerate(self.arms)
        }
        self.discriminating = [
            p for i, p in enumerate(self.prompts) if sum(passed[i]) == 1
        ]
        self.n_dropped_incomplete = 0


def _planted_env(n: int = 120, seed: int = 0) -> _FakeEnv:
    """Half the prompts are about databases, half about strings.

    Arm A passes every database prompt and fails every string prompt; arm B is
    the mirror image. The text says exactly which arm wins, so any working
    probe must find it.
    """
    rng = random.Random(seed)
    db_words = ["database", "index", "transaction", "query", "schema", "rollback"]
    st_words = ["string", "substring", "unicode", "concatenate", "whitespace", "regex"]
    prompts, passed = [], []
    for i in range(n):
        is_db = i % 2 == 0
        words = db_words if is_db else st_words
        prompts.append(" ".join(rng.choice(words) for _ in range(12)) + f" case {i}")
        passed.append([1, 0] if is_db else [0, 1])
    return _FakeEnv(prompts, ["arm_a", "arm_b"], passed)


def _noise_env(n: int = 120, seed: int = 1) -> _FakeEnv:
    """Arms are exchangeable: same per-prompt difficulty, independent coins.

    Splits still happen — that is the whole point — but nothing about the text
    predicts which arm wins.
    """
    rng = random.Random(seed)
    vocab = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
    prompts, passed = [], []
    for i in range(n):
        prompts.append(" ".join(rng.choice(vocab) for _ in range(12)) + f" case {i}")
        p_i = rng.choice([0.5, 0.7, 0.9])
        passed.append([int(rng.random() < p_i), int(rng.random() < p_i)])
    return _FakeEnv(prompts, ["arm_a", "arm_b"], passed)


def _reps():
    """Only the dependency-free representations — CI has no model weights."""
    return [rc.handcraft_representation(), rc.lexical_representation()]


# ── disagreement_stats: the identity that reframes the oracle gap ─────────────


def test_two_arm_identity_holds_on_recorded_shaped_matrix():
    # 107 all-pass, 18 none-pass, 39 split — the recorded HumanEval+ shape.
    P = np.array([[1, 1]] * 107 + [[0, 0]] * 18 + [[1, 0]] * 20 + [[0, 1]] * 19, dtype=float)
    s = rc.disagreement_stats(P)
    assert s["split"] == 39
    assert s["identity_holds"] is True
    assert s["oracle_over_round_robin"] == pytest.approx(39 / (2 * 164), abs=1e-4)


def test_identity_holds_for_arbitrary_random_matrices():
    rng = np.random.default_rng(7)
    for _ in range(25):
        P = (rng.random((rng.integers(5, 80), 2)) < rng.random()).astype(float)
        s = rc.disagreement_stats(P)
        assert s["identity_holds"] is True


def test_identical_arms_produce_zero_gap():
    P = np.array([[1, 1], [0, 0], [1, 1]], dtype=float)
    s = rc.disagreement_stats(P)
    assert s["split"] == 0
    assert s["oracle_over_round_robin"] == 0.0


def test_stats_omit_identity_for_three_arms():
    P = np.array([[1, 0, 1], [0, 1, 0]], dtype=float)
    s = rc.disagreement_stats(P)
    assert s["n_arms"] == 3
    assert "split_over_2n" not in s


# ── TF-IDF ────────────────────────────────────────────────────────────────────


def test_tfidf_rows_are_finite_and_separate_topics():
    texts = ["database index query", "database query rollback", "unicode substring regex"]
    m = rc.tfidf_matrix(texts, min_df=1)
    assert np.isfinite(m).all()
    m = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)
    assert m[0] @ m[1] > m[0] @ m[2]


def test_tfidf_empty_vocab_does_not_crash():
    # min_df=2 with all-unique tokens leaves no vocabulary at all.
    m = rc.tfidf_matrix(["alpha", "beta"], min_df=2)
    assert m.shape[0] == 2
    assert np.isfinite(m).all()


# ── The probe: planted signal vs pure noise ───────────────────────────────────


def test_probe_finds_planted_signal():
    env = _planted_env()
    out = rc.arm_ceiling(env, representations=_reps(), n_permutations=200, seed=3)
    assert out.verdict == "LEARNABLE"
    lex = next(p for p in out.probes if p.representation == "lexical")
    # Perfectly separable by topic — the probe should be near-perfect.
    assert lex.pass_at_1 > 0.95
    assert lex.p_value <= 0.05


def test_probe_reports_not_detectable_on_exchangeable_arms():
    env = _noise_env()
    out = rc.arm_ceiling(env, representations=_reps(), n_permutations=200, seed=3)
    assert out.verdict == "NOT-DETECTABLE"
    assert "disagreement, not routable skill" in out.detail
    # Splits exist — the null is not degenerate.
    assert out.stats["split"] > 0


def test_permutation_test_is_deterministic_under_a_fixed_seed():
    env = _noise_env()
    a = rc.arm_ceiling(env, representations=_reps(), n_permutations=100, seed=11)
    b = rc.arm_ceiling(env, representations=_reps(), n_permutations=100, seed=11)
    assert [p.as_dict() for p in a.probes] == [p.as_dict() for p in b.probes]


def test_unavailable_representation_is_reported_not_dropped():
    env = _noise_env(n=20)
    dead = rc.Representation("semantic", False, detail="no weights")
    out = rc.arm_ceiling(env, representations=[*_reps(), dead], n_permutations=0)
    names = [p.representation for p in out.probes]
    assert "semantic" in names
    sem = next(p for p in out.probes if p.representation == "semantic")
    assert sem.available is False and sem.pass_at_1 is None


def test_probe_is_an_upper_bound_never_below_worst_arm():
    env = _noise_env(n=60, seed=5)
    _, P = rc.outcome_matrix(env)
    out = rc.arm_ceiling(env, representations=_reps(), n_permutations=0)
    worst = float(P.mean(axis=0).min())
    for p in out.probes:
        if p.available:
            # A kNN vote over two arms can do worse than the best arm, but
            # landing below the *worst* arm on 60 prompts would mean the
            # neighbour bookkeeping is inverted.
            assert p.pass_at_1 >= worst - 0.2


def test_knn_never_uses_a_prompt_as_its_own_neighbour():
    # Planted signal, but every label flipped for one prompt. If the probe
    # leaked self-labels it would route that prompt correctly regardless.
    env = _planted_env(n=40)
    prompts, P = rc.outcome_matrix(env)
    P[0] = [0.0, 1.0] if P[0][0] == 1.0 else [1.0, 0.0]
    rep = rc.lexical_representation()
    X = rep.matrix(prompts)
    order, sim = rc._loo_neighbour_order(X)
    assert (order[:, 0] != np.arange(len(prompts))).all()
    nn, w = rc._neighbour_weights(order, sim, 5)
    _, picks = rc._knn_policy_pass(P, nn, w)
    # Neighbours all say "the other arm", so the flipped prompt is misrouted.
    assert P[0, picks[0]] == 0.0


# ── Escalation ceiling ────────────────────────────────────────────────────────


def _cascade_rows(n=60, seed=2):
    """Local fails a third of the time; the judge catches two thirds of those."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        local_passed = rng.random() > 0.33
        caught = (not local_passed) and rng.random() < 0.66
        over = local_passed and rng.random() < 0.1
        judge_accept = not (caught or over)
        escalated = not judge_accept
        cloud_passed = rng.random() < 0.95
        rows.append(rc.CascadeRow(
            prompt=f"prompt {i} " + " ".join(rng.choice(["a", "b", "c"]) for _ in range(6)),
            local_output=f"def f_{i}(): return {i}",
            local_passed=local_passed,
            judge_accept=judge_accept,
            escalated=escalated,
            cloud_passed=cloud_passed,
            cloud_cost=0.008,
            final_passed=cloud_passed if escalated else local_passed,
        ))
    return rows


def test_judge_operating_point_matches_hand_count():
    rows = [
        rc.CascadeRow("p", "o", local_passed=False, judge_accept=False, escalated=True,
                      cloud_passed=True, cloud_cost=0.01, final_passed=True),
        rc.CascadeRow("p", "o", local_passed=False, judge_accept=True, escalated=False,
                      cloud_passed=True, cloud_cost=0.01, final_passed=False),
        rc.CascadeRow("p", "o", local_passed=True, judge_accept=False, escalated=True,
                      cloud_passed=True, cloud_cost=0.01, final_passed=True),
        rc.CascadeRow("p", "o", local_passed=True, judge_accept=True, escalated=False,
                      cloud_passed=True, cloud_cost=0.01, final_passed=True),
    ]
    j = rc.judge_operating_point(rows)
    assert j["n_failed"] == 2 and j["n_caught"] == 1
    assert j["fail_recall"] == pytest.approx(0.5)
    assert j["over_escalations"] == 1
    assert j["esc_rate"] == pytest.approx(0.5)


def test_frontier_is_a_non_decreasing_upper_envelope():
    rows = _cascade_rows()
    f = rc.escalation_frontier(rows)
    passes = [pt["pass_at_1"] for pt in f]
    # Budget is a cap, not a quota: the oracle never spends it on a row that
    # would lose quality, so the curve can only go up.
    assert all(b >= a - 1e-9 for a, b in zip(passes, passes[1:]))
    assert f[0]["budget_rate"] == 0.0 and f[0]["esc_rate"] == 0.0
    assert f[-1]["budget_rate"] == pytest.approx(1.0)
    # At full budget the oracle escalates exactly the rescuable rows.
    rescuable = sum(1 for r in rows if r.cloud_passed and not r.local_passed)
    assert f[-1]["esc_rate"] == pytest.approx(rescuable / len(rows), abs=1e-4)


def test_frontier_never_escalates_rows_without_a_recorded_cloud_answer():
    rows = _cascade_rows(n=40)
    for r in rows[:20]:
        r.cloud_recorded = False
    f = rc.escalation_frontier(rows)
    rescuable = sum(
        1 for r in rows if r.cloud_recorded and r.cloud_passed and not r.local_passed
    )
    assert f[-1]["esc_rate"] == pytest.approx(rescuable / len(rows), abs=1e-4)


def test_frontier_at_zero_equals_always_local():
    rows = _cascade_rows()
    f = rc.escalation_frontier(rows)
    always_local = sum(r.local_passed for r in rows) / len(rows)
    assert f[0]["pass_at_1"] == pytest.approx(always_local, abs=1e-4)
    assert f[0]["cost_per_1k"] == pytest.approx(0.0)


def test_escalation_ceiling_flags_judge_sufficient_when_text_is_uninformative():
    rows = _cascade_rows()
    out = rc.escalation_ceiling(rows, representations=_reps())
    assert out.verdict == "JUDGE-SUFFICIENT"
    assert out.n_rows == len(rows)
    # Every probe is scored at the judge's own escalation rate.
    for p in out.probes:
        if p.get("available"):
            assert p["matched_esc_rate"] == pytest.approx(out.judge["esc_rate"], abs=0.02)


def test_escalation_ceiling_detects_an_improvable_gate():
    """Plant a text feature that predicts local failure better than the judge.

    Outputs containing "BROKEN" always fail. The judge spends half its budget
    on over-escalations of healthy rows and misses half the broken ones, so a
    text probe that reads the output can beat it *at the same budget* — which
    is exactly the improvement the verdict is supposed to detect.
    """
    rows = []
    for i in range(80):
        broken = i % 4 == 0                      # 20 broken
        over_escalate = (not broken) and i % 8 == 1   # 10 wasted escalations
        judge_escalates = (broken and i % 8 == 0) or over_escalate   # 10 caught + 10 wasted
        rows.append(rc.CascadeRow(
            prompt=f"task {i}",
            local_output=("BROKEN placeholder stub " if broken else "def solve(): return 1 ") * 3,
            local_passed=not broken,
            judge_accept=not judge_escalates,
            escalated=judge_escalates,
            cloud_passed=True,
            cloud_cost=0.008,
            final_passed=True if judge_escalates else not broken,
        ))
    out = rc.escalation_ceiling(rows, representations=_reps())
    assert out.verdict == "GATE-IMPROVABLE"
    # The judge wastes half its budget; a perfect reader of the output spends
    # all of it on the broken rows.
    assert out.judge["over_escalations"] == 10
    best = max(p["delta_vs_judge"] for p in out.probes if p.get("available"))
    assert best >= rc.MIN_MEANINGFUL_GAIN


def test_escalation_ceiling_rejects_empty_input():
    with pytest.raises(ValueError):
        rc.escalation_ceiling([])


def test_load_cascade_rows_skips_rows_without_local_fields(tmp_path):
    p = tmp_path / "cascade.jsonl"
    p.write_text("\n".join([
        "# a comment line",
        json.dumps({"prompt_full": "a", "local_passed": True, "judge_verdict": True,
                    "local_output": "x", "cloud_passed": True, "cloud_cost": 0.01,
                    "escalated": False, "final_passed": True}),
        json.dumps({"prompt_full": "b", "cloud_passed": True}),   # no local fields
        "not json at all",
    ]))
    rows = rc.load_cascade_rows(p)
    assert len(rows) == 1
    assert rows[0].prompt == "a"


# ── Entrypoint ────────────────────────────────────────────────────────────────


def test_run_ceiling_with_only_a_cascade_file(tmp_path):
    p = tmp_path / "cascade.jsonl"
    p.write_text("\n".join(
        json.dumps({
            "prompt_full": f"p{i}", "local_output": f"out {i}",
            "local_passed": i % 3 != 0, "judge_verdict": i % 3 != 0,
            "escalated": i % 3 == 0, "cloud_passed": True,
            "cloud_cost": 0.008, "final_passed": True,
        }) for i in range(30)
    ))
    report = rc.run_ceiling(None, None, p, representations=_reps())
    assert report["arm_ceiling"] is None
    assert report["escalation_ceiling"]["n_rows"] == 30


def test_run_ceiling_with_no_data_returns_empty_sections():
    report = rc.run_ceiling(None, None, None)
    assert report == {"arm_ceiling": None, "escalation_ceiling": None}
