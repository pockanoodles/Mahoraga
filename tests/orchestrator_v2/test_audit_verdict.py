"""Tests for the audit verdict: pricing provenance, the break-even arithmetic,
and that the engine can say "stay" as readily as "split"."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.orchestrator.agentbench.stats import Rate, task_rate
from backend.orchestrator.audit import pricing
from backend.orchestrator.audit.verdict import (
    SPLIT, STAY, SWITCH, Assumptions, Billing, Evidence, decide, render,
)


def ev(solved: int, tasks: int = 40, minutes: float = 10.0, tier: str = "test-verified") -> Evidence:
    return Evidence(rate=task_rate({f"t{i}": [i < solved] for i in range(tasks)}),
                    minutes_per_attempt=minutes, tier=tier, label="fake:m / feedback, 1 repo")


A = Assumptions(tasks_per_month=200)


# ── Pricing ───────────────────────────────────────────────────────────────────


def test_pricing_rows_carry_provenance():
    plans = pricing.load()
    assert plans, "pricing.json is empty"
    for p in plans.values():
        assert p.source.startswith("https://") and p.as_of
        if p.verified:
            assert p.usd_per_month is not None


def test_unverified_prices_are_refused_unless_supplied():
    with pytest.raises(pricing.UnverifiedPrice, match="--plan-price"):
        pricing.plan("claude-max-20x")
    p = pricing.plan("claude-max-20x", price_override=200)
    assert p.usd_per_month == 200 and not p.verified and "supplied by the user" in p.note


def test_lower_tier_is_the_next_verified_tier_down():
    assert pricing.lower_tier(pricing.plan("claude-max-5x")).id == "claude-pro"
    assert pricing.lower_tier(pricing.plan("claude-pro")) is None


def test_lower_tier_skips_unverified_tiers(tmp_path: Path):
    rows = json.loads(pricing.DATA.read_text())
    for r in rows["plans"]:
        if r["id"] == "claude-max-20x":
            r.update(usd_per_month=200, verified=True)
        if r["id"] == "claude-max-5x":
            r["verified"] = False
    f = tmp_path / "p.json"
    f.write_text(json.dumps(rows))
    top = pricing.plan("claude-max-20x", path=f)
    assert pricing.lower_tier(top, path=f).id == "claude-pro"


# ── API billing ───────────────────────────────────────────────────────────────


def test_it_says_stay_when_local_is_weak():
    v = decide(ev(4), A, Billing.api(150))  # 10% resolve, $0.75 a cloud task
    assert (v.call, v.confident) == (STAY, True)
    assert "not wasting money" in v.reason
    lo, mid, hi = v.monthly_usd
    assert lo <= mid <= hi < 0


def test_it_says_split_when_local_pays_even_at_the_low_end():
    v = decide(ev(36), A, Billing.api(2000))  # 90% resolve, $10 a cloud task
    assert (v.call, v.confident) == (SPLIT, True)
    assert v.monthly_usd[0] >= max(5, 0.05 * 2000)


def test_an_interval_spanning_the_threshold_is_not_a_confident_call():
    # Few tasks: wide interval. The point pays, the low end doesn't.
    v = decide(ev(4, tasks=6), A, Billing.api(1500))
    assert not v.confident
    lo, mid, hi = v.monthly_usd
    assert lo < mid < hi


def test_break_even_rate_matches_the_formula():
    a = Assumptions(tasks_per_month=100, deferrable_share=1.0, triage_minutes=6,
                    hourly_usd=60, watts=0, usd_per_kwh=0)
    v = decide(ev(10), a, Billing.api(400))  # $4/task cloud, $6 triage, no power cost
    assert "≥ 60%" in v.sensitivity[0]  # (0 + 6) / (4 + 6)


def test_net_is_zero_at_the_break_even_rate():
    a = Assumptions(tasks_per_month=100, deferrable_share=1.0, triage_minutes=6,
                    hourly_usd=60, watts=0, usd_per_kwh=0)
    exact = Evidence(Rate(0.6, 0.6, 0.6, 50, 50), 10, "test-verified", "x")
    v = decide(exact, a, Billing.api(400))
    assert v.monthly_usd == pytest.approx((0, 0, 0), abs=1e-9)


def test_switch_needs_both_a_high_floor_and_nearly_all_work_deferrable():
    strong = ev(40)
    assert decide(strong, A, Billing.api(150)).call != SWITCH  # only 25% deferrable
    a = Assumptions(tasks_per_month=200, deferrable_share=0.95)
    assert decide(strong, a, Billing.api(150)).call == SWITCH


# ── Subscriptions ─────────────────────────────────────────────────────────────


def max5() -> Billing:
    p = pricing.plan("claude-max-5x")
    return Billing.subscription(p, pricing.lower_tier(p))


def test_on_a_flat_plan_weak_local_is_a_stay_with_no_dollar_figure():
    v = decide(ev(4), A, max5())
    assert v.call == STAY and v.monthly_usd is None
    assert "flat plan" in v.reason


def test_a_plan_bigger_than_your_usage_is_reported_without_local():
    a = Assumptions(tasks_per_month=200, usage_of_cap=0.1)  # 0.5 Pro-units used of 5
    v = decide(ev(4), a, max5())
    assert v.call == STAY and "without local at all" in v.reason


def test_dropping_a_tier_when_local_takes_enough():
    # Using 1.25 Pro-units of Max 5x's 5: dropping to Pro needs 0.25 of it moved.
    a = Assumptions(tasks_per_month=200, deferrable_share=0.5, usage_of_cap=0.25)
    v = decide(ev(36), a, max5())  # 90%: lo ~0.77 ≥ p_down 0.4
    assert (v.call, v.confident) == (SPLIT, True)
    assert "Claude Pro" in v.reason and v.monthly_usd[1] > 0


def test_hitting_the_cap_makes_headroom_the_reason():
    a = Assumptions(tasks_per_month=200, deferrable_share=0.5, usage_of_cap=1.0)
    v = decide(ev(30), a, Billing.subscription(pricing.plan("claude-pro"), None))
    assert v.call == SPLIT and "headroom" in v.reason


# ── Guards on the evidence ────────────────────────────────────────────────────


def test_too_few_tasks_decides_nothing():
    v = decide(ev(2, tasks=3), A, Billing.api(150))
    assert v.call == STAY and not v.confident and "too few" in v.reason


def test_v1_refuses_non_test_evidence():
    with pytest.raises(ValueError, match="test-verified"):
        decide(ev(10, tier="judge-graded"), A, Billing.api(150))


def test_render_lists_every_assumption():
    text = render(decide(ev(4), A, Billing.api(150)))
    for needle in ("VERDICT: STAY", "assumed, not measured", "200 cloud coding tasks",
                   "25% of them can wait", "what would change it", "−$"):
        assert needle in text


# ── Evidence pooled from benches ──────────────────────────────────────────────


def _bench(tmp_path: Path, name: str, results: dict[str, list[bool]], arm="fake:m",
           cond="feedback", tier="test-verified"):
    from dataclasses import asdict

    from backend.orchestrator.agentbench.bank import Bench, Task
    from backend.orchestrator.agentbench.runner import Attempt

    repo = tmp_path / name
    repo.mkdir()
    b = Bench(repo, root=tmp_path / "state")
    b.save_tasks([Task(sha=s, base="b", subject=s, body="", gold_src=["x.py"], gold_lines=1,
                       test_files=["t.py"], f2p=["t"], p2p=["u"]) for s in results])
    b.attempts.mkdir(parents=True)
    for sha, outs in results.items():
        for rep, ok in enumerate(outs):
            a = Attempt(sha=sha, arm=arm, cond=cond, resolved=ok, f2p_pass=int(ok), f2p_total=1,
                        regressions=0, regression_ids=[], suite_crashed=False,
                        edited_files=["x.py"], touched_tests=[], hit_gold_file=True,
                        secs=600.0, timed_out=False, llm_calls=1, tokens_sent=0, tokens_recv=0,
                        extra={}, rep=rep, tier=tier)
            (b.attempts / f"{sha}_{rep}.json").write_text(json.dumps(asdict(a)))
    return b


def test_evidence_pools_tasks_across_repos_as_units(tmp_path):
    from backend.orchestrator.audit.evidence import from_benches

    a = _bench(tmp_path, "one", {"s1": [True, True, True], "s2": [False]})
    b = _bench(tmp_path, "two", {"s1": [False], "s3": [True]})  # same sha, other repo
    e = from_benches([a, b], "fake:m", "feedback")
    assert (e.rate.tasks, e.rate.attempts) == (4, 6)
    assert e.rate.point == 0.5  # 2 of 4 tasks, not 4 of 6 attempts
    assert e.minutes_per_attempt == 10 and "2 repos" in e.label
    assert "predate run manifests" in e.hardware


def test_evidence_refuses_a_missing_arm_and_mixed_tiers(tmp_path):
    from backend.orchestrator.audit.evidence import from_benches

    a = _bench(tmp_path, "one", {"s1": [True]})
    with pytest.raises(ValueError, match="no attempts"):
        from_benches([a], "fake:other", "feedback")
    b = _bench(tmp_path, "two", {"s2": [True]}, tier="judge-graded")
    with pytest.raises(ValueError, match="mixes grading tiers"):
        from_benches([a, b], "fake:m", "feedback")


def test_cli_verdict(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from backend.orchestrator.cli.commands.agentbench import app

    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(tmp_path / "state"))
    _bench(tmp_path, "one", {f"s{i}": [i < 2] for i in range(10)})
    run = CliRunner().invoke(app, ["verdict", str(tmp_path / "one"), "--arm", "fake:m",
                                   "--tasks-per-month", "200", "--api-spend", "150"])
    assert run.exit_code == 0, run.output
    assert "VERDICT: STAY" in run.output
    both = CliRunner().invoke(app, ["verdict", str(tmp_path / "one"), "--arm", "fake:m",
                                    "--tasks-per-month", "200", "--api-spend", "150",
                                    "--plan", "claude-pro"])
    assert both.exit_code != 0 and "exactly one" in both.output
    unverified = CliRunner().invoke(app, ["verdict", str(tmp_path / "one"), "--arm", "fake:m",
                                          "--tasks-per-month", "200", "--plan", "claude-max-20x"])
    assert unverified.exit_code != 0 and "--plan-price" in unverified.output
