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
from backend.orchestrator.audit.evidence import from_benches
from backend.orchestrator.audit.verdict import Assumptions, Billing, decide

ROOT = Path(__file__).resolve().parents[2]
CLAIMS = ROOT / "experiments" / "claims.json"
PROBE_TASKS = ROOT / "experiments" / "agent_edit" / "tasks.jsonl"
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


def test_the_private_ops_figures_are_not_exported(site):
    assert set(site) == {"schema", "source", "claims", "probe", "verdict_demo"}


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


def test_every_curve_row_is_the_engines_verdict_on_the_demo_evidence(site):
    ev = from_benches([demo.open_bench()], *QWEN_BLIND)
    a = Assumptions(tasks_per_month=200)
    demo_verdict = site["verdict_demo"]
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


def test_the_demo_evidence_says_the_machine_was_not_recorded(site):
    evidence = site["verdict_demo"]["evidence"]
    assert "unrecorded machine" in evidence["hardware"]
    assert evidence["label"].startswith("aider:qwen3.5:latest / blind")


def test_a_task_cannot_have_two_attempts_in_one_cell():
    attempt = load_attempts(demo.open_bench())[0]
    with pytest.raises(export.ExportError, match="more than one attempt"):
        export._cells([attempt, attempt])
