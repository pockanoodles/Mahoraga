"""Tests for the site export: the numbers the landing page renders.

The page renders only what is in docs/site/site.json, so the export is worth
publishing only if nothing in it is typed by hand. Each test recomputes a
section from the engine's own functions, or from the raw files, and compares.
"""
from __future__ import annotations

import importlib.util
import json
import tomllib
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import pytest

from backend.orchestrator import demo
from backend.orchestrator.agentbench.report import outcome, summarise
from backend.orchestrator.agentbench.runner import load_attempts
from backend.orchestrator.agentbench.stats import task_rate
from backend.orchestrator.audit import claims as audit_claims
from backend.orchestrator.audit.verdict import Assumptions, Billing, decide

ROOT = Path(__file__).resolve().parents[2]
CLAIMS = ROOT / "experiments" / "claims.json"
PROBE_TASKS = ROOT / "experiments" / "agent_edit" / "tasks.jsonl"
SITE_JSON = ROOT / "docs" / "site" / "site.json"
COMMIT = "abc1234"
QWEN_BLIND = ("aider:qwen3.5:latest", "blind")


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export = _load_script("export_site")


@pytest.fixture(scope="module")
def site() -> dict:
    return export.build_site(commit=COMMIT)


def _probe_rows() -> list[dict]:
    return [json.loads(line) for line in PROBE_TASKS.read_text().splitlines() if line.strip()]


# ── Deterministic, and independent of the shell ──────────────────────────────


def test_two_exports_are_byte_identical_whatever_bench_root_the_shell_names(monkeypatch, tmp_path):
    first = export.render(export.build_site(commit=COMMIT))
    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(tmp_path / "somebody-elses-benches"))
    second = export.render(export.build_site(commit=COMMIT))
    assert first == second
    assert first.endswith("}\n") and "\\u" not in first  # readable, not escaped


def test_main_writes_what_render_gives_and_nothing_when_a_claim_has_drifted(
        monkeypatch, tmp_path, capsys):
    out = tmp_path / "site" / "site.json"
    assert export.main(out) == 0
    assert out.read_text(encoding="utf-8") == export.render(export.build_site())

    def refuse(*args):
        raise export.ExportError("claims their artifacts no longer back: x")

    monkeypatch.setattr(export, "_claims", refuse)
    elsewhere = tmp_path / "refused.json"
    assert export.main(elsewhere) == 1
    assert not elsewhere.exists() and "export refused" in capsys.readouterr().err


def test_source_names_the_repo_the_version_and_the_commit(site):
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"].get("version")
    if declared is None:
        pytest.skip("the version is dynamic; it is read from the installed package")
    assert site["source"] == {"repo": "pockanoodles/Mahoraga", "commit": COMMIT,
                              "version": declared}
    head = export._git(ROOT, "rev-parse", "--short=7", "HEAD").strip()
    assert export.build_site()["source"]["commit"] == head


def test_the_committed_export_is_what_the_exporter_makes_now():
    text = SITE_JSON.read_text(encoding="utf-8")
    committed = json.loads(text)
    fresh = export.build_site(commit=committed["source"]["commit"])  # the one field that moves
    stale = sorted(k for k in {*committed, *fresh} if committed.get(k) != fresh.get(k))
    assert not stale, (f"docs/site/site.json is stale in {stale}: run "
                       "`python scripts/export_site.py` and commit the result")
    assert text == export.render(committed), "docs/site/site.json was edited by hand"


def test_the_private_ops_figures_are_not_exported(site):
    assert set(site) == {"schema", "source", "claims", "probe", "verdict_demo", "settings",
                         "specimen"}


# ── Claims: claims.json, and only what its artifacts still back ──────────────


def test_every_claim_and_metric_is_claims_json_verbatim(site):
    manifest = json.loads(CLAIMS.read_text())["claims"]
    assert len(site["claims"]) == len(manifest) >= 2
    for got, want in zip(site["claims"], manifest):
        assert got == {k: want[k] for k in ("id", "description", "artifact", "config", "metrics")}
        assert got["metrics"] == want["metrics"]


def test_a_claim_its_artifact_no_longer_backs_is_not_exported(tmp_path):
    manifest = json.loads(CLAIMS.read_text())
    manifest["claims"][0]["metrics"]["routed_pass_at_1"]["value"] = 0.999
    drifted = tmp_path / "claims.json"
    drifted.write_text(json.dumps(manifest))
    with pytest.raises(export.ExportError, match="humaneval_plus_164_reading_judge"):
        export._claims(drifted, ROOT)


# ── The probe ────────────────────────────────────────────────────────────────


def test_probe_arm_rates_are_the_engines_task_rates(site):
    bench = demo.open_bench()
    tasks, attempts = bench.load_tasks(), load_attempts(bench)
    resolved = defaultdict(lambda: defaultdict(list))
    for a in attempts:
        resolved[a.arm, a.cond][a.sha].append(a.resolved)
    summaries = {(s.arm, s.cond): s for s in summarise(tasks, attempts)}
    arms = site["probe"]["arms"]
    assert [(r["arm"], r["cond"]) for r in arms] == list(summaries) and len(arms) == 4
    for row in arms:
        s = summaries[row["arm"], row["cond"]]
        assert row["rate"] == asdict(task_rate(resolved[row["arm"], row["cond"]]))
        assert (row["resolved"], row["n"], row["median_minutes"], row["broke_other_code"],
                row["multi_file_resolved"], row["multi_file_n"]) == (
            s.resolved, s.n, s.median_minutes, s.broke_other_code,
            s.multi_file_resolved, s.multi_file_n)


def test_the_probe_arms_are_the_readme_table(site):
    arms = {(r["arm"], r["cond"]): (r["resolved"], r["n"]) for r in site["probe"]["arms"]}
    assert arms == {("aider:qwen3.5:latest", "blind"): (5, 18),
                    ("aider:qwen3.5:latest", "feedback"): (5, 18),
                    ("aider:granite4.1:8b", "blind"): (0, 18),
                    ("aider:granite4.1:8b", "feedback"): (1, 18)}
    assert site["probe"]["repo"] == "Mahoraga"
    assert (site["probe"]["dates"], site["probe"]["machine"]) == (
        demo.meta()["dates"], demo.meta()["machine"])


def test_probe_tasks_carry_their_commit_body_and_every_outcome(site):
    bench = demo.open_bench()
    tasks = bench.load_tasks()
    cells = {(a.arm, a.cond, a.sha): a for a in load_attempts(bench)}
    rows = site["probe"]["tasks"]
    probe = _probe_rows()
    assert [r["sha"] for r in rows] == [p["sha"] for p in probe]  # tasks.jsonl order
    assert [r["body"] for r in rows] == [p["body"] for p in probe]  # the full commit bodies
    for row, t in zip(rows, tasks):
        assert (row["subject"], row["files"], row["lines"]) == (t.subject, len(t.gold_src),
                                                                t.gold_lines)
        assert len(row["outcomes"]) == 4
        for cell, got in row["outcomes"].items():
            arm, cond = cell.split(" / ")
            a = cells[arm, cond, t.sha]
            assert got == {"outcome": outcome(a), "minutes": round(a.secs / 60, 1)}


def test_the_replay_is_the_rate_after_each_task_in_tasks_file_order(site):
    replay = site["probe"]["replay"]
    steps = replay["steps"]
    bench = demo.open_bench()
    tasks = bench.load_tasks()
    cells = {(a.arm, a.cond, a.sha): a for a in load_attempts(bench)}
    assert (replay["arm"], replay["cond"]) == QWEN_BLIND
    assert "experiments/agent_edit/tasks.jsonl" in replay["order"]
    assert [s["n"] for s in steps] == list(range(1, 19))
    assert [s["sha"] for s in steps] == [p["sha"] for p in _probe_rows()]
    for step, t in zip(steps, tasks):
        a = cells[(*QWEN_BLIND, t.sha)]
        first_n = {s.sha: [cells[(*QWEN_BLIND, s.sha)].resolved] for s in tasks[:step["n"]]}
        assert step["outcome"] == outcome(a)
        assert step["rate"] == asdict(task_rate(first_n))
    qwen = next(r for r in site["probe"]["arms"] if (r["arm"], r["cond"]) == QWEN_BLIND)
    assert steps[-1]["rate"] == qwen["rate"]  # the replay ends on the table's number
    width = [s["rate"]["hi"] - s["rate"]["lo"] for s in steps]
    assert width[-1] < width[0]  # narrower overall, though not at every step


# ── The verdict demo ─────────────────────────────────────────────────────────


def test_the_curve_grid_is_log_spaced_plus_the_round_figures(site):
    grid = [row["cloud_usd_per_task"] for row in site["verdict_demo"]["curve"]]
    assert grid == sorted(set(grid))  # ascending, no duplicates
    assert {0.25, 0.5, 0.75, 1, 2, 5, 10, 20} <= set(grid)
    assert (grid[0], grid[-1]) == (0.10, 30.0)
    assert len(grid) == 24 + 8  # 24 log-spaced, none of which lands on a round figure


def _headline() -> tuple[dict, list[dict]]:
    """The claim the demo follows, and its slice read straight from the artifact."""
    claim = next(c for c in json.loads(CLAIMS.read_text())["claims"] if c["id"] == export.HEADLINE)
    rows = [json.loads(line) for line in (ROOT / claim["artifact"]).read_text().splitlines()
            if line.strip()]
    return claim, audit_claims.select(rows, claim["select"])


def test_every_curve_row_is_the_engines_verdict_on_the_headline_slice(site):
    claim, rows = _headline()
    ev = audit_claims.evidence(rows)
    a = Assumptions(**{k: v for k, v in claim["verdict"].items() if k != "api_spend"})
    demo_verdict = site["verdict_demo"]
    assert (demo_verdict["claim"], demo_verdict["select"]) == (claim["id"], claim["select"])
    assert demo_verdict["evidence"] == asdict(ev)
    assert demo_verdict["assumptions"] == {**asdict(a), "lines": a.lines()}
    # Written out here, not imported: local cost of an attempt, and of triaging a failed one.
    local = a.watts / 1000 * ev.minutes_per_attempt / 60 * a.usd_per_kwh
    triage = a.triage_minutes / 60 * a.hourly_usd
    for row in demo_verdict["curve"]:
        x = row["cloud_usd_per_task"]
        v = decide(ev, a, Billing.api(x * a.tasks_per_month))
        assert (row["call"], row["confident"], row["reason"]) == (v.call, v.confident, v.reason)
        assert tuple(row["monthly_usd"].values()) == pytest.approx(v.monthly_usd)
        assert row["breakeven"] == pytest.approx((local + triage) / (x + triage))
        assert f"≥ {row['breakeven']:.0%}" in v.sensitivity[0]  # the verdict quotes the same rate


def test_the_curve_breakeven_falls_as_cloud_tasks_get_dearer(site):
    rates = [row["breakeven"] for row in site["verdict_demo"]["curve"]]
    assert rates == sorted(rates, reverse=True) and 0 < rates[-1] < rates[0] < 1


def test_the_curve_at_the_claims_own_price_is_the_verdict_the_claim_publishes(site):
    """The calculator opens on the numbers the log and the report print."""
    claim, _ = _headline()
    price = claim["verdict"]["api_spend"] / claim["verdict"]["tasks_per_month"]
    row = next(r for r in site["verdict_demo"]["curve"] if r["cloud_usd_per_task"] == price)
    published = claim["metrics"]
    assert row["call"] == published["call"]["value"]
    assert round(row["monthly_usd"]["point"]) == published["net"]["value"]
    assert round(row["breakeven"], 2) == published["breakeven_rate"]["value"]


def test_every_point_carries_the_bar_a_split_must_clear(site):
    """5% of the month's cloud bill, never under $5, written out here."""
    tasks = site["verdict_demo"]["assumptions"]["tasks_per_month"]
    for row in site["verdict_demo"]["curve"]:
        assert row["bar"] == pytest.approx(max(5.0, 0.05 * row["cloud_usd_per_task"] * tasks))


def test_each_triage_curve_is_the_headline_verdict_with_only_triage_changed(site):
    claim, rows = _headline()
    ev = audit_claims.evidence(rows)
    a = Assumptions(**{k: v for k, v in claim["verdict"].items() if k != "api_spend"})
    demo_verdict = site["verdict_demo"]
    offered = [t["triage_minutes"] for t in demo_verdict["by_triage"]]
    assert sorted({*offered, a.triage_minutes}) == sorted(export.TRIAGE_MINUTES)
    assert a.triage_minutes not in offered  # the claim's own curve is `curve`, once
    prices = [row["cloud_usd_per_task"] for row in demo_verdict["curve"]]
    for t in demo_verdict["by_triage"]:
        # The calculator keeps the slider's position when triage changes, so
        # every curve has to sit on the same prices, point for point.
        assert [row["cloud_usd_per_task"] for row in t["curve"]] == prices
        at = Assumptions(**{**asdict(a), "triage_minutes": t["triage_minutes"]})
        assert t["triage_usd"] == pytest.approx(t["triage_minutes"] / 60 * a.hourly_usd)
        for row in t["curve"]:
            v = decide(ev, at, Billing.api(row["cloud_usd_per_task"] * a.tasks_per_month))
            assert (row["call"], row["confident"], row["reason"]) == (v.call, v.confident, v.reason)
    # More minutes per failure can only raise the rate local needs to pay.
    by_minutes = {t["triage_minutes"]: t["curve"] for t in demo_verdict["by_triage"]}
    by_minutes[a.triage_minutes] = demo_verdict["curve"]
    at_price = [by_minutes[m][10]["breakeven"] for m in sorted(by_minutes)]
    assert at_price == sorted(at_price)


def test_the_costs_are_an_attempts_electricity_and_a_failures_triage(site):
    claim, rows = _headline()
    ev = audit_claims.evidence(rows)
    a = Assumptions(**{k: v for k, v in claim["verdict"].items() if k != "api_spend"})
    costs = site["verdict_demo"]["costs"]
    assert costs["attempt_usd"] == pytest.approx(a.watts / 1000 * ev.minutes_per_attempt / 60
                                                 * a.usd_per_kwh)
    assert costs["triage_usd"] == pytest.approx(a.triage_minutes / 60 * a.hourly_usd)
    assert costs["triage_usd"] > 100 * costs["attempt_usd"]  # the report's "several hundred to one"


# ── The settings exhibit ─────────────────────────────────────────────────────


def _claim(cid: str) -> dict:
    return next(c for c in json.loads(CLAIMS.read_text())["claims"] if c["id"] == cid)


def _slice(cid: str) -> list[dict]:
    c = _claim(cid)
    rows = [json.loads(line) for line in (ROOT / c["artifact"]).read_text().splitlines()
            if line.strip()]
    return audit_claims.select(rows, c["select"])


def test_each_settings_squares_count_to_its_claims_published_figures(site):
    settings = site["settings"]
    assert [a["key"] for a in settings["arms"]] == [k for k, _ in export.SETTINGS]
    for arm, (key, cid) in zip(settings["arms"], export.SETTINGS):
        published = _claim(cid)["metrics"]
        lit = sum(t["resolved"][key] for t in settings["tasks"])
        assert lit == arm["resolved"] == published["resolved"]["value"]
        assert len(settings["tasks"]) == published["tasks"]["value"]
        assert arm["rate"]["point"] == pytest.approx(lit / len(settings["tasks"]))


def test_a_square_is_one_commit_read_from_every_settings_slice(site):
    tasks = site["settings"]["tasks"]
    for key, cid in export.SETTINGS:
        by_task = {(r["repo"], r["sha"]): r["resolved"] for r in _slice(cid)}
        assert {(t["repo"], t["sha"]) for t in tasks} == set(by_task)
        assert all(t["resolved"][key] == by_task[t["repo"], t["sha"]] for t in tasks)
    # Ordered by repository and commit date, so no outcome decides where a square sits.
    assert [(t["repo"], t["committed"]) for t in tasks] == sorted(
        (t["repo"], t["committed"]) for t in tasks)
    assert all(t["url"] == f"{export.UPSTREAM[t['repo']]}/commit/{t['sha']}" for t in tasks)


def test_the_settings_pairs_are_the_paired_test_the_report_publishes(site):
    pairs = {(p["from"], p["to"]): p for p in site["settings"]["pairs"]}
    assert list(pairs) == [("32k", "131k"), ("131k", "35b")]
    published = _claim("report_2026_10_qwen35_vs_qwen36_131k_outside")["metrics"]
    p = pairs["131k", "35b"]
    for k in ("pairs", "pair_resolved", "versus_resolved", "only_this", "only_versus"):
        assert p[k] == published[k]["value"]
    assert round(p["mcnemar_p"], 2) == published["mcnemar_p"]["value"]
    net = pairs["32k", "131k"]
    assert net["versus_resolved"] - net["pair_resolved"] == net["only_versus"] - net["only_this"]


def test_a_repository_with_no_public_upstream_is_refused(monkeypatch):
    monkeypatch.setattr(export, "UPSTREAM", {k: v for k, v in export.UPSTREAM.items()
                                             if k != "click"})
    with pytest.raises(export.ExportError, match="no public upstream for click"):
        export.build_site(commit=COMMIT)


# ── The specimen ─────────────────────────────────────────────────────────────


def test_the_specimen_is_its_artifact_verbatim(site):
    assert site["specimen"] == json.loads(export.SPECIMEN.read_text())


def test_a_specimen_whose_counts_contradict_its_outcome_is_refused(tmp_path):
    s = json.loads(export.SPECIMEN.read_text())
    s["attempt"]["regressions"] = 1
    bad = tmp_path / "specimen.json"
    bad.write_text(json.dumps(s))
    with pytest.raises(export.ExportError, match="contradict"):
        export._specimen(bad)


def test_the_export_refuses_a_headline_claims_json_does_not_hold(monkeypatch):
    monkeypatch.setattr(export, "HEADLINE", "no_such_claim")
    with pytest.raises(export.ExportError, match="no_such_claim"):
        export.build_site(commit=COMMIT)


def test_a_task_cannot_have_two_attempts_in_one_cell():
    attempt = load_attempts(demo.open_bench())[0]
    with pytest.raises(export.ExportError, match="more than one attempt"):
        export._cells([attempt, attempt])
