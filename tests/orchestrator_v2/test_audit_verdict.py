"""Tests for the audit verdict: pricing provenance, the break-even arithmetic,
and that the engine can say "stay" as readily as "split"."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from backend.orchestrator.agentbench.stats import Rate, task_rate
from backend.orchestrator.audit import pricing
from backend.orchestrator.audit.verdict import (
    SPLIT, STAY, SWITCH, Assumptions, Billing, Evidence, breakeven_rate, decide, render, usd,
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


def test_a_stay_read_off_timeouts_is_not_decided():
    # 10% resolve, but 30 of the 36 failures ran out of time: the rate is a floor.
    late = dataclasses.replace(ev(4), failed=36, timed_out=30)
    v = decide(late, A, Billing.api(150))
    assert (v.call, v.confident) == (STAY, False)
    assert v.reason.startswith("30 of 36 failed attempts ran out of time")
    assert "longer --timeout" in v.sensitivity[0]
    # Timeouts that are a minority of the failures leave the stay standing.
    v = decide(dataclasses.replace(ev(4), failed=36, timed_out=17), A, Billing.api(150))
    assert (v.call, v.confident) == (STAY, True)
    # A split stands, since more time could only raise the rate.
    v = decide(dataclasses.replace(ev(36), failed=4, timed_out=3), A, Billing.api(2000))
    assert (v.call, v.confident) == (SPLIT, True)


def test_an_interval_spanning_the_threshold_is_not_a_confident_call():
    # Few tasks: wide interval. The point pays, the low end doesn't.
    v = decide(ev(4, tasks=6), A, Billing.api(1500))
    assert not v.confident
    lo, mid, hi = v.monthly_usd
    assert lo < mid < hi


def test_a_losing_net_reads_as_minus_dollars_not_dollar_minus():
    # 5 of 18 at $5 a cloud task: the point loses money, the top of the interval wins.
    v = decide(ev(5, tasks=18), A, Billing.api(1000))
    assert (v.call, v.confident) == (STAY, False) and v.monthly_usd[1] < 0
    assert f"(~{usd(v.monthly_usd[1])}/mo)" in v.reason and "$-" not in v.reason
    # A split that isn't safe to rely on: the point pays, the low end of the interval loses.
    v = decide(ev(3, tasks=6), A, Billing.api(1500))
    assert (v.call, v.confident) == (SPLIT, False) and v.monthly_usd[0] < 0
    assert f"reaches {usd(v.monthly_usd[0])}/mo" in v.reason and "$-" not in v.reason


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


def test_breakeven_rate_is_one_function_the_verdict_quotes():
    a = Assumptions(tasks_per_month=100, deferrable_share=1.0, triage_minutes=6,
                    hourly_usd=60, watts=0, usd_per_kwh=0)
    assert breakeven_rate(ev(10), a, 4.0) == pytest.approx(0.6)  # (0 + 6) / (4 + 6)
    # With a real power bill: 40 W for 10 min at $0.30/kWh is $0.002, triage $2.50.
    e = ev(10, minutes=10)
    assert breakeven_rate(e, A, 10.0) == pytest.approx((0.002 + 2.5) / (10 + 2.5))
    # The verdict's sensitivity line reads the same number.
    v = decide(e, A, Billing.api(2000))  # $10 a cloud task
    assert f"≥ {breakeven_rate(e, A, 10.0):.0%}" in v.sensitivity[0]


def test_nothing_pays_when_a_cloud_task_costs_less_than_the_electricity():
    watts_hog = Assumptions(tasks_per_month=100, watts=4000, usd_per_kwh=1.0)  # $0.67 an attempt
    assert breakeven_rate(ev(10, minutes=10), watts_hog, 0.10) > 1


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
           cond="feedback", tier="test-verified", machine: dict | None = None):
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
                        extra={}, rep=rep, tier=tier, run_id=f"run-{name}" if machine else "")
            (b.attempts / f"{sha}_{rep}.json").write_text(json.dumps(asdict(a)))
    if machine:
        (b.dir / "runs").mkdir()
        (b.dir / "runs" / f"run-{name}.json").write_text(
            json.dumps({"run_id": f"run-{name}", "machine": machine}))
    return b


def test_evidence_pools_tasks_across_repos_as_units(tmp_path):
    from backend.orchestrator.audit.evidence import from_benches

    a = _bench(tmp_path, "one", {"s1": [True, True, True], "s2": [False]})
    b = _bench(tmp_path, "two", {"s1": [False], "s3": [True]})  # same sha, other repo
    e = from_benches([a, b], "fake:m", "feedback")
    assert (e.rate.tasks, e.rate.attempts) == (4, 6)
    assert e.rate.point == 0.5  # 2 of 4 tasks, not 4 of 6 attempts
    assert e.minutes_per_attempt == 10 and "2 repos" in e.label
    assert (e.failed, e.timed_out) == (2, 0)
    assert "predate run manifests" in e.hardware


def test_evidence_refuses_a_missing_arm_and_mixed_tiers(tmp_path):
    from backend.orchestrator.audit.evidence import from_benches

    a = _bench(tmp_path, "one", {"s1": [True]})
    with pytest.raises(ValueError, match="no attempts"):
        from_benches([a], "fake:other", "feedback")
    b = _bench(tmp_path, "two", {"s2": [True]}, tier="judge-graded")
    with pytest.raises(ValueError, match="mixes grading tiers"):
        from_benches([a, b], "fake:m", "feedback")


# ── Published claims, recomputed from an export ───────────────────────────────

STUDIO = {"model": "Mac16,9", "memory_gb": 64}


def _export(tmp_path, benches) -> list[dict]:
    from backend.orchestrator.agentbench import export

    path = tmp_path / "out" / "attempts.jsonl"
    export.write(path, benches)
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_export_keeps_counted_attempts_on_live_tasks_with_their_machine(tmp_path):
    a = _bench(tmp_path, "one", {"s1": [True, False], "s2": [False]}, machine=STUDIO)
    a.save_tasks([t for t in a.load_tasks() if t.sha != "s2"])  # s2 dropped since
    rows = _export(tmp_path, [a])
    assert [(r["sha"], r["rep"], r["resolved"]) for r in rows] == [("s1", 0, True), ("s1", 1, False)]
    assert {r["repo"] for r in rows} == {"one"}
    assert {r["machine"] for r in rows} == {"Mac16,9, 64 GB"}


def test_export_says_each_attempts_limit_and_whether_it_ran_out(tmp_path):
    from backend.orchestrator.agentbench.export import limit_of

    rows = _export(tmp_path, [_bench(tmp_path, "one", {"s1": [True]}, machine=STUDIO)])
    assert (rows[0]["timed_out"], rows[0]["limit_s"]) == (False, 1200.0)  # the default
    assert limit_of({"settings": {"timeout": 5400.0}}) == 5400.0
    assert limit_of({"settings": {"queue_job": "0ee57c"}}) == 1200.0  # a worker run before #60
    assert limit_of({}) is None  # no manifest: nobody wrote the limit down


def _row(sha, ok, *, limit=1200.0, late=False, arm="x:m", machine="Mac16,1, 16 GB", secs=60.0):
    return {"repo": "r", "sha": sha, "committed": "2026-05-01T00:00:00Z", "arm": arm,
            "cond": "blind", "rep": 0, "resolved": ok, "secs": secs, "tier": "test-verified",
            "machine": machine, "timed_out": late, "limit_s": limit}


def test_an_attempt_that_ended_inside_a_shorter_limit_stands_at_a_longer_one():
    from backend.orchestrator.audit.claims import metrics

    rows = [_row("a", True),                                   # ended on its own at 20 min
            _row("b", False, late=True), _row("b", True, limit=5400.0),  # cut, then rerun
            _row("c", False, late=True),                       # cut, never rerun: pending
            _row("d", False, limit=5400.0, late=True),          # ran out at 90 min
            _row("e", True, limit=9000.0)]                     # can't be cut shorter later
    at_90 = metrics(rows, {"select": {"arm": "x:m", "cond": "blind", "limit_s": 5400}})
    assert (at_90["tasks"], at_90["resolved"], at_90["timed_out"]) == (3, 2, 1)
    at_20 = metrics(rows, {"select": {"arm": "x:m", "cond": "blind", "limit_s": 1200}})
    assert (at_20["tasks"], at_20["resolved"], at_20["timed_out"]) == (3, 1, 2)
    old = [{k: v for k, v in r.items() if k not in ("timed_out", "limit_s")} for r in rows]
    with pytest.raises(ValueError, match="export it again"):
        metrics(old, {"select": {"arm": "x:m", "cond": "blind", "limit_s": 5400}})


def test_a_claimed_stay_read_off_timeouts_is_not_decided():
    from backend.orchestrator.audit.claims import metrics

    rows = ([_row(f"s{i}", False, late=True, secs=1200.0) for i in range(6)]
            + [_row(f"t{i}", i < 1, secs=300.0) for i in range(4)])
    claim = {"select": {"arm": "x:m", "cond": "blind"},
             "verdict": {"tasks_per_month": 200, "api_spend": 150}}
    got = metrics(rows, claim)
    assert (got["failed"], got["timed_out"]) == (9, 6)
    assert (got["call"], got["confident"]) == (STAY, "no")
    no_flags = [{k: v for k, v in r.items() if k != "timed_out"} for r in rows]
    assert metrics(no_flags, claim)["confident"] == "yes"  # an old export can't tell


def test_a_paired_claim_counts_the_tasks_only_one_side_solved():
    from backend.orchestrator.audit.claims import metrics

    laptop = [_row(s, ok) for s, ok in zip("abcdef", (True, False, False, True, False, False))]
    studio = [_row(s, ok, arm="x:big", machine="Mac17,14, 64 GB")
              for s, ok in zip("abcdeg", (True, True, True, False, False, True))]
    claim = {"select": {"arm": "x:m", "cond": "blind"},
             "versus": {"select": {"arm": "x:big", "cond": "blind"}}}
    got = metrics(laptop, claim, versus_rows=studio)
    assert (got["tasks"], got["pairs"]) == (6, 5)  # f and g ran on one side only
    assert (got["pair_resolved"], got["versus_resolved"]) == (2, 3)
    assert (got["only_this"], got["only_versus"]) == (1, 2)
    assert got["mcnemar_p"] == 1.0
    assert metrics(laptop + studio, claim)["pairs"] == 5  # both sides from one artifact
    with pytest.raises(ValueError, match="one per task"):
        metrics(laptop + [_row("a", False)], claim, versus_rows=studio)
    with pytest.raises(ValueError, match="share no tasks"):
        metrics(laptop, claim, versus_rows=[_row("z", True, arm="x:big")])
    with pytest.raises(ValueError, match="2 machines"):  # the other slice is checked too
        metrics(laptop, claim, versus_rows=studio + [_row("h", True, arm="x:big")])


def test_pairing_follows_a_sha_that_lengthened_and_refuses_an_ambiguous_one():
    from backend.orchestrator.audit.claims import metrics

    claim = {"select": {"arm": "x:m", "cond": "blind"},
             "versus": {"select": {"arm": "x:big", "cond": "blind"}}}
    got = metrics([_row("abc1234", True)], claim,
                  versus_rows=[_row("abc12345", False, arm="x:big")])
    assert (got["pairs"], got["only_this"]) == (1, 1)
    with pytest.raises(ValueError, match="matches 2 tasks"):
        metrics([_row("abc1234", True)], claim,
                versus_rows=[_row("abc12345", False, arm="x:big"),
                             _row("abc12346", True, arm="x:big")])
    with pytest.raises(ValueError, match="more than one task on this side"):
        metrics([_row("abc12345", True), _row("abc12346", False)], claim,
                versus_rows=[_row("abc1234", False, arm="x:big")])


def test_verify_reads_the_versus_artifact(tmp_path):
    from backend.orchestrator.routing.benchmark.verify import verify_claim

    def write(name, rows):
        (tmp_path / name).write_text("".join(json.dumps(r) + "\n" for r in rows))

    write("laptop.jsonl", [_row(s, s == "a") for s in "abc"])
    write("studio.jsonl", [_row(s, s != "c", arm="x:big", machine="Mac17,14, 64 GB") for s in "abc"])
    claim = {"id": "p", "kind": "agentbench", "artifact": "laptop.jsonl",
             "select": {"arm": "x:m", "cond": "blind"},
             "versus": {"artifact": "studio.jsonl", "select": {"arm": "x:big", "cond": "blind"}},
             "metrics": {"pairs": {"value": 3, "decimals": 0},
                         "only_versus": {"value": 1, "decimals": 0},
                         "mcnemar_p": {"value": 1.0, "decimals": 2}}}
    assert verify_claim(claim, tmp_path).ok
    r = verify_claim({**claim, "versus": {**claim["versus"], "artifact": "gone.jsonl"}}, tmp_path)
    assert not r.ok and "artifact not found: gone.jsonl" in r.error
    (tmp_path / "torn.jsonl").write_text('{"repo": "r", "sha"')
    r = verify_claim({**claim, "versus": {**claim["versus"], "artifact": "torn.jsonl"}}, tmp_path)
    assert not r.ok and "artifact unreadable: torn.jsonl" in r.error


def test_claim_metrics_are_the_verdict_engines_numbers(tmp_path):
    from backend.orchestrator.audit.claims import metrics
    from backend.orchestrator.audit.evidence import from_benches
    from backend.orchestrator.audit.verdict import breakeven_cloud_cost

    benches = [_bench(tmp_path, "one", {f"s{i}": [i < 3] for i in range(12)}, machine=STUDIO),
               _bench(tmp_path, "two", {f"s{i}": [i < 1] for i in range(8)}, machine=STUDIO)]
    claim = {"select": {"arm": "fake:m", "cond": "feedback"},
             "verdict": {"tasks_per_month": 200, "api_spend": 150}}
    got = metrics(_export(tmp_path, benches), claim)
    ev = from_benches(benches, "fake:m", "feedback")
    v = decide(ev, A, Billing.api(150))
    assert (got["tasks"], got["resolved"]) == (20, 4)
    assert (got["rate"], got["rate_lo"], got["rate_hi"]) == (ev.rate.point, ev.rate.lo, ev.rate.hi)
    assert got["call"] == v.call == STAY
    assert (got["net_lo"], got["net"], got["net_hi"]) == v.monthly_usd
    assert got["breakeven_rate"] == breakeven_rate(ev, A, 0.75)
    assert got["breakeven_cloud_usd"] == breakeven_cloud_cost(ev, A)
    one = metrics(_export(tmp_path, benches), {"select": {**claim["select"], "repos": ["two"]}})
    assert (one["tasks"], one["resolved"]) == (8, 1) and "call" not in one


def test_claims_split_by_commit_date_and_refuse_two_machines():
    from backend.orchestrator.audit.claims import metrics

    def row(sha, committed, ok, machine="Mac16,9, 64 GB"):
        return {"repo": "r", "sha": sha, "committed": committed, "arm": "x:m", "cond": "blind",
                "rep": 0, "resolved": ok, "secs": 60.0, "tier": "test-verified", "machine": machine}

    rows = [row("a", "2026-03-01T10:00:00+00:00", True),
            row("b", "2026-04-16T09:00:00-04:00", False), row("c", "2026-05-01T00:00:00Z", True)]
    after = metrics(rows, {"select": {"arm": "x:m", "cond": "blind", "committed_from": "2026-04-16"}})
    assert (after["tasks"], after["resolved"]) == (2, 1)
    before = metrics(rows, {"select": {"arm": "x:m", "cond": "blind", "committed_before": "2026-04-16"}})
    assert (before["tasks"], before["resolved"]) == (1, 1)
    with pytest.raises(ValueError, match="commit date"):
        metrics(rows + [row("d", "", True)],
                {"select": {"arm": "x:m", "cond": "blind", "committed_from": "2026-04-16"}})
    with pytest.raises(ValueError, match="2 machines"):
        metrics(rows + [row("d", "", True, machine="Mac15,7, 16 GB")],
                {"select": {"arm": "x:m", "cond": "blind"}})
    other = [{**r, "arm": "y:m"} for r in rows[:2]]
    paired = metrics(rows + other, {"select": {"arm": "x:m", "cond": "blind", "tasks_of": "y:m"}})
    assert (paired["tasks"], paired["resolved"]) == (2, 1)
    with pytest.raises(ValueError, match="no rows for z:m"):
        metrics(rows, {"select": {"arm": "x:m", "cond": "blind", "tasks_of": "z:m"}})
    with pytest.raises(ValueError, match="knows only"):
        metrics(rows, {"select": {"arm": "x:m", "cond": "blind", "model": "m"}})


def test_verify_checks_an_agentbench_claim_and_its_call(tmp_path):
    from backend.orchestrator.routing.benchmark.verify import verify_claim

    b = _bench(tmp_path, "one", {f"s{i}": [i < 2] for i in range(10)}, machine=STUDIO)
    rows = _export(tmp_path, [b])
    (tmp_path / "out" / "attempts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    claim = {"id": "c", "kind": "agentbench", "artifact": "out/attempts.jsonl",
             "select": {"arm": "fake:m", "cond": "feedback"},
             "verdict": {"tasks_per_month": 200, "api_spend": 150},
             "metrics": {"tasks": {"value": 10, "decimals": 0},
                         "rate": {"value": 0.2, "decimals": 2},
                         "call": {"value": "stay"}}}
    assert verify_claim(claim, tmp_path).ok
    claim["metrics"]["call"] = {"value": "split"}
    r = verify_claim(claim, tmp_path)
    assert not r.ok and "published split, artifact yields stay" in r.checks[-1].detail
    r = verify_claim({**claim, "select": {"arm": "fake:m"}}, tmp_path)
    assert not r.ok and "can't be computed" in r.error
    assert "unknown claim kind" in verify_claim({**claim, "kind": "nope"}, tmp_path).error


def _plain(output: str) -> str:
    """CLI error text without colour codes, box borders or wrapping, which
    differ between a terminal and CI."""
    import re
    return re.sub(r"[\s│╭╮╰╯─]+", "", re.sub(r"\x1b\[[0-9;]*m", "", output))


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
    assert both.exit_code != 0 and "exactlyone" in _plain(both.output)
    unverified = CliRunner().invoke(app, ["verdict", str(tmp_path / "one"), "--arm", "fake:m",
                                          "--tasks-per-month", "200", "--plan", "claude-max-20x"])
    assert unverified.exit_code != 0 and "--plan-price" in _plain(unverified.output)


def test_evidence_refuses_attempts_from_two_machines(tmp_path):
    from backend.orchestrator.audit.evidence import from_benches

    laptop = {"model": "Mac16,7", "memory_gb": 16}
    a = _bench(tmp_path, "one", {"s1": [True]}, machine=laptop)
    b = _bench(tmp_path, "two", {"s2": [False]}, machine=laptop)
    assert from_benches([a, b], "fake:m", "feedback").hardware == "Mac16,7, 16 GB"
    c = _bench(tmp_path, "three", {"s3": [True]}, machine={"model": "Mac17,14", "memory_gb": 64})
    with pytest.raises(ValueError, match="2 machines"):
        from_benches([a, c], "fake:m", "feedback")


def test_an_answer_about_payment_reads_as_a_plan_or_a_spend():
    from backend.orchestrator.audit.usage import parse_payment

    assert parse_payment("150") == {"api_spend": 150.0}
    assert parse_payment("$150") == {"api_spend": 150.0}
    assert parse_payment("claude-pro") == {"plan": "claude-pro", "plan_price": None}
    assert parse_payment("claude-max-20x 200") == {"plan": "claude-max-20x", "plan_price": 200.0}
    for bad in ("", "150 20", "claude-pro cheap", "a b c"):
        with pytest.raises(ValueError):
            parse_payment(bad)


def test_usage_is_saved_only_when_the_verdict_can_use_it(tmp_path):
    from backend.orchestrator.audit import usage

    with pytest.raises(ValueError, match="exactly one"):
        usage.save(tmp_path, usage.Usage(tasks_per_month=10))
    with pytest.raises(ValueError, match="--plan-price"):
        usage.save(tmp_path, usage.Usage(tasks_per_month=10, plan="claude-max-20x"))
    assert usage.load(tmp_path) is None
    u = usage.Usage(tasks_per_month=200, api_spend=150)
    usage.save(tmp_path, u)
    assert usage.load(tmp_path) == u and u.describe() == "200 tasks a month, $150 a month of API"


def test_cli_verdict_uses_the_saved_usage_and_flags_override_it(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from backend.orchestrator.audit import usage
    from backend.orchestrator.cli.commands.agentbench import app

    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(tmp_path / "state"))
    _bench(tmp_path, "one", {f"s{i}": [i < 2] for i in range(10)})
    args = ["verdict", str(tmp_path / "one"), "--arm", "fake:m"]
    unsaved = CliRunner().invoke(app, args)
    assert unsaved.exit_code != 0 and "--tasks-per-month" in _plain(unsaved.output)
    usage.save(tmp_path / "state", usage.Usage(tasks_per_month=200, api_spend=150))
    run = CliRunner().invoke(app, args + ["--json"])
    assert run.exit_code == 0, run.output
    assert json.loads(run.output)["assumptions"]["tasks_per_month"] == 200
    run = CliRunner().invoke(app, args + ["--json", "--tasks-per-month", "40", "--plan", "claude-pro"])
    assert run.exit_code == 0, run.output
    assert json.loads(run.output)["assumptions"]["tasks_per_month"] == 40
