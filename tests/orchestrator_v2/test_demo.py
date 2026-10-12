"""Tests for the demo bench: the agent-edit probe converted into a bench the engine reads.

The test that matters is the first: read through the engine's own report, the
converted bench gives the probe README's results table exactly. The rest pin
that the conversion invents nothing (no manifest, no machine data), that the
packaged files are what the build script makes, and that installing a copy
never replaces a directory that is not a demo. `mahoraga demo` itself is in
test_demo_command.py.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from backend.orchestrator import demo
from backend.orchestrator.agentbench.report import summarise
from backend.orchestrator.agentbench.runner import load_attempts
from backend.orchestrator.audit.evidence import from_benches

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "experiments" / "agent_edit"
README = (PROBE / "README.md").read_text()


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_script("build_demo_data")

ARMS = {"qwen3.5": "aider:qwen3.5:latest", "granite4.1-8b": "aider:granite4.1:8b"}
TABLE_ROW = re.compile(
    r"^\| (?P<model>[\w.-]+), (?P<cond>blind|feedback) \| \**(?P<resolved>\d+) / (?P<n>\d+)"
    r"(?: \((?P<pct>\d+)%\))?\** \| (?P<multi>\d+) / (?P<multi_n>\d+) \| (?P<broke>\d+) \| "
    r"(?P<minutes>[\d.]+) min \|$", re.M)


def _summaries() -> dict[tuple[str, str], object]:
    bench = demo.open_bench()
    return {(s.arm, s.cond): s for s in summarise(bench.load_tasks(), load_attempts(bench))}


def _probe_tasks() -> list[dict]:
    return [json.loads(line) for line in (PROBE / "tasks.jsonl").read_text().splitlines()
            if line.strip()]


# ── The conversion reproduces the probe's own table ──────────────────────────


def test_the_converted_bench_reproduces_the_probe_readme_table():
    expected = {(ARMS[m["model"]], m["cond"]): m for m in TABLE_ROW.finditer(README)}
    assert len(expected) == 4, "the README results table was not found"
    got = _summaries()
    assert set(got) == set(expected)
    for key, row in expected.items():
        s = got[key]
        assert (s.resolved, s.n) == (int(row["resolved"]), int(row["n"])), key
        assert (s.multi_file_resolved, s.multi_file_n) == (int(row["multi"]), int(row["multi_n"])), key
        assert s.broke_other_code == int(row["broke"]), key
        assert s.median_minutes == float(row["minutes"]), key
        if row["pct"]:
            assert f"{s.rate:.0%}" == f"{row['pct']}%", key
    assert sum(s.touched_tests for s in got.values()) == 0  # "No attempt in any arm edited a test file."


def test_the_readme_edit_error_counts_survive_the_conversion():
    # "In 27 of its 36 attempts it wrote search blocks quoting code that is not
    # in the file (qwen3.5: 4 of 36)": granite and qwen, blind and feedback pooled.
    said = re.search(r"In\s+(\d+)\s+of\s+its\s+36\s+attempts.*?\(qwen3\.5:\s+(\d+)\s+of\s+36\)",
                     README, re.S)
    assert said, "the README sentence about rejected edits was not found"
    granite, qwen = (int(x) for x in said.groups())
    bench = demo.open_bench()
    rejected = {"aider:granite4.1:8b": 0, "aider:qwen3.5:latest": 0}
    for a in load_attempts(bench):
        rejected[a.arm] += a.extra["edit_errors"] > 0
    assert (rejected["aider:granite4.1:8b"], rejected["aider:qwen3.5:latest"]) == (granite, qwen)


def test_each_task_keeps_the_probes_fields_and_whole_regression_baseline():
    probe = _probe_tasks()
    tasks = demo.open_bench().load_tasks()
    assert [t.sha for t in tasks] == [r["sha"] for r in probe]  # the probe's own order
    for t, r in zip(tasks, probe):
        for field in ("base", "subject", "body", "gold_src", "gold_lines", "test_files",
                      "f2p", "p2p"):
            assert getattr(t, field) == r[field], (t.sha, field)
    assert min(len(t.p2p) for t in tasks) >= 400  # the API reports len(p2p): never a truncation


def test_every_probe_result_became_one_unchanged_attempt():
    attempts = {(a.arm, a.cond, a.sha): a for a in load_attempts(demo.open_bench())}
    results = [json.loads(p.read_text()) for p in sorted((PROBE / "results").glob("*.json"))]
    assert len(attempts) == len(results) == 72
    for r in results:
        a = attempts[(f"aider:{r['model']}", r["cond"], r["sha"])]
        for field in ("resolved", "secs", "timed_out", "f2p_pass", "f2p_total", "regressions",
                      "regression_ids", "suite_crashed", "edited_files", "touched_tests",
                      "hit_gold_file", "llm_calls", "tokens_sent", "tokens_recv"):
            assert getattr(a, field) == r[field], (a.arm, a.cond, a.sha, field)
        assert a.extra == {"edit_errors": r["edit_errors"]}
        assert a.no_edit == r["no_edit"]


def test_dropped_commits_keep_the_probes_reasons():
    probe = json.loads((PROBE / "dropped.json").read_text())
    dropped = json.loads(demo.open_bench().dropped_path.read_text())
    assert dropped == {d["sha"]: d["drop"] for d in probe} and len(dropped) == 4


# ── It invents nothing ───────────────────────────────────────────────────────


def test_the_demo_predates_manifests_and_the_engine_says_so():
    bench = demo.open_bench()
    assert not (bench.dir / "runs").exists()
    attempts = load_attempts(bench)
    assert {a.run_id for a in attempts} == {""}
    assert not any(a.conditions or a.degraded for a in attempts)
    ev = from_benches([bench], "aider:qwen3.5:latest", "blind")
    assert "unrecorded machine" in ev.hardware
    assert ev.label.endswith("1 repo (Mahoraga)")


def test_the_label_is_the_readmes_own_words():
    info = demo.meta()
    assert f"## Results ({info['dates']}, {info['machine']})" in README
    assert info["source"] == "experiments/agent_edit" and info["note"]


# ── The packaged data is what the script makes ───────────────────────────────


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_the_packaged_data_is_what_the_build_script_makes(tmp_path):
    build.convert(tmp_path)
    made, packaged = _tree(tmp_path), _tree(demo.DATA_DIR)
    assert made.keys() == packaged.keys()
    stale = sorted(name for name in made if made[name] != packaged[name])
    assert not stale, f"stale demo data, run `python scripts/build_demo_data.py`: {stale[:3]}"


def test_the_build_fails_loudly_without_the_readme_heading():
    with pytest.raises(ValueError, match="Results"):
        build._labels("# Agent-edit probe\n\nNo results here.\n")


def test_open_bench_is_not_redirected_by_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(tmp_path))
    bench = demo.open_bench()
    assert bench.dir.parent == demo.DATA_DIR
    assert bench.dir.name == next(demo.DATA_DIR.glob("Mahoraga-*")).name


# ── Installing and serving ───────────────────────────────────────────────────


def test_install_copies_the_bench_fresh_each_time(tmp_path):
    dest = tmp_path / "demo"
    demo.install(dest)
    bench = demo.open_bench(dest)
    assert len(bench.load_tasks()) == 18
    bench.tasks_path.write_text("")  # a session that damaged its copy
    (dest / "stray.txt").write_text("left behind")
    demo.install(dest)
    assert len(demo.open_bench(dest).load_tasks()) == 18
    assert not (dest / "stray.txt").exists()
    assert len(demo.open_bench().load_tasks()) == 18  # the packaged data was never the one edited


def test_install_refuses_a_directory_that_is_not_a_demo(tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "notes.txt").write_text("keep")
    with pytest.raises(FileExistsError, match="not a Mahoraga demo"):
        demo.install(mine)
    assert (mine / "notes.txt").read_text() == "keep"
    empty = tmp_path / "empty"
    empty.mkdir()
    demo.install(empty)  # nothing there to lose
    assert (empty / demo.META).exists()
