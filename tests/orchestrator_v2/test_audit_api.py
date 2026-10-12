"""Tests for the audit over HTTP, and for finding benches on disk.

A temp bench root holds three benches written through the real dataclasses:
`calc` (two arms, both conditions, repeats, one run manifest, one degraded
attempt), `widgets` (one arm, no manifest, attempts that predate manifests) and
`fresh` (mined, never run). `agent-home` sits beside them as a directory that
is not a bench. Every endpoint is read against it, and the numbers are checked
against the engine's own functions on the same data, not against constants.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest

from backend.orchestrator.agentbench import manifest
from backend.orchestrator.agentbench.bank import (
    Bench, SuiteConfig, Task, bench_root, find_benches,
)
from backend.orchestrator.agentbench.runner import Attempt

QWEN, GRANITE = "aider:qwen3.5:latest", "aider:granite4.1:8b"
RUN_ID = "20261001T220000-ab12"
T0 = 1_790_000_000  # a fixed mtime base, so "last activity" is not a race with the test run


def _task(sha: str, *, files: tuple[str, ...] = ("pkg/a.py",), f2p: int = 2, p2p: int = 5) -> Task:
    return Task(sha=sha, base=f"base-{sha}", subject=f"fix {sha}", body=f"why {sha}",
                gold_src=list(files), gold_lines=len(files) * 10,
                test_files=[f"tests/test_{sha}.py"],
                f2p=[f"t{i}" for i in range(f2p)], p2p=[f"u{i}" for i in range(p2p)])


def _attempt(sha: str, arm: str, cond: str, resolved: bool, *, rep: int = 0, secs: float = 600.0,
             run_id: str = "", **over) -> Attempt:
    fields = dict(
        sha=sha, arm=arm, cond=cond, resolved=resolved,
        f2p_pass=2 if resolved else 0, f2p_total=2, regressions=0, regression_ids=[],
        suite_crashed=False, edited_files=["pkg/a.py"], touched_tests=[], hit_gold_file=True,
        secs=secs, timed_out=False, llm_calls=3, tokens_sent=1200.0, tokens_recv=300.0,
        extra={}, rep=rep, run_id=run_id)
    fields.update(over)
    return Attempt(**fields)


def _write(bench: Bench, a: Attempt, *, degraded: bool = False, mtime: float | None = None) -> None:
    folder = bench.attempts / "degraded" if degraded else bench.attempts
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{a.arm.replace(':', '_')}__{a.cond}__{a.sha}__r{a.rep}.json"
    path.write_text(json.dumps(asdict(a)))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _manifest(repo: Path, run_id: str = RUN_ID, **over) -> dict:
    """What manifest.build writes, without the probes that need a real machine."""
    m = {
        "run_id": run_id, "started_at": "2026-10-01T22:00:00-0400", "ended_at": None,
        "repo": str(repo), "arms": [QWEN, GRANITE],
        "settings": {"conds": ["blind", "feedback"], "repeats": 2},
        "suite": {"python": "/usr/bin/python3", "pytest_args": []},
        "tasks": {"n": 6, "tasks_sha256": "00", "prompts_sha256": "11", "repo_head": "abc"},
        "machine": {"os": "macOS-15.1-arm64-arm-64bit", "python": "3.12.7",
                    "power": {"on_ac": True, "percent": 100, "watts": 61.0},
                    "model": "Mac16,1", "cpu": "Apple M4 Pro", "memory_gb": 24},
        "harness": {"commit": "a1b2c3d4e5f6", "dirty": False},
        "agents": {"aider": "aider 0.86.1"},
        "models": {"ollama": "0.12.5", "digests": {"qwen3.5": "sha256:aaa", "granite4.1": "sha256:bbb"}},
        "speed_refs": {},
    }
    m.update(over)
    return m


@dataclass
class Root:
    path: Path
    calc: Bench
    widgets: Bench
    fresh: Bench
    repos: dict[str, Path]


@pytest.fixture
def root(tmp_path, monkeypatch) -> Root:
    path = tmp_path / "benches"
    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(path))
    repos = {n: tmp_path / "src" / n for n in ("calc", "widgets", "fresh")}

    calc = Bench(repos["calc"])
    calc.save_config(SuiteConfig(python="/usr/bin/python3", pytest_args=["-o", "pythonpath=src"]))
    shas = [f"c{i}" for i in range(1, 7)]
    calc.save_tasks([_task(s, files=("pkg/a.py", "pkg/b.py") if s in ("c2", "c5") else ("pkg/a.py",))
                     for s in shas])
    calc.dropped_path.write_text(json.dumps({"d1": "gate B: the tests already pass without the fix",
                                             "d2": "no source change"}))
    for s, ok in zip(shas, [True, False, True, False, True, False]):
        _write(calc, _attempt(s, QWEN, "blind", ok, run_id=RUN_ID), mtime=T0 + 100)
    for s, reps in zip(shas, [[True, True], [False, True], [True, True], [False, False],
                              [True, False], [False, False]]):
        for rep, ok in enumerate(reps):
            _write(calc, _attempt(s, QWEN, "feedback", ok, rep=rep, secs=720.0, run_id=RUN_ID),
                   mtime=T0 + 200 + rep)
    failures = [dict(edited_files=[], hit_gold_file=False), dict(hit_gold_file=False),
                dict(regressions=2), dict(f2p_pass=1), dict(), dict(edited_files=[], hit_gold_file=False)]
    for s, extra in zip(shas, failures):
        _write(calc, _attempt(s, GRANITE, "blind", False, secs=480.0, run_id=RUN_ID, **extra),
               mtime=T0 + 300)
    _write(calc, _attempt("c2", QWEN, "blind", False, run_id=RUN_ID,
                          degraded=["contended: a manual smoke test shared the GPU"]),
           degraded=True, mtime=T0 + 250)
    manifest.write(calc, _manifest(repos["calc"], ended_at="2026-10-02T06:10:00-0400",
                                   attempts=34, stopped="completed"))

    widgets = Bench(repos["widgets"])
    widgets.save_config(SuiteConfig(python="/usr/bin/python3"))
    wshas = ["w1", "w2", "w3", "w4"]
    widgets.save_tasks([_task(s, f2p=1, p2p=9) for s in wshas])
    for s, ok in zip(wshas, [True, False, True, True]):
        _write(widgets, _attempt(s, QWEN, "blind", ok, secs=300.0), mtime=T0 + 50)

    fresh = Bench(repos["fresh"])
    fresh.save_config(SuiteConfig(python="/usr/bin/python3"))
    fresh.save_tasks([_task("f1")])

    (path / "agent-home" / "aider").mkdir(parents=True)
    (path / "ops.log").write_text("not a bench")
    return Root(path, calc, widgets, fresh, repos)


# ── Finding benches on disk ───────────────────────────────────────────────────


def test_bench_root_follows_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(tmp_path / "elsewhere"))
    assert bench_root() == tmp_path / "elsewhere"
    monkeypatch.delenv("MAHORAGA_AGENTBENCH_ROOT")
    assert bench_root() == Path.home() / ".mahoraga-v2" / "agentbench"


def test_found_benches_are_the_directories_holding_a_config_or_tasks(root):
    found = find_benches()
    assert [b.dir.name for b in found] == sorted(
        [root.calc.dir.name, root.widgets.dir.name, root.fresh.dir.name])
    assert [b.name for b in found] == ["calc", "fresh", "widgets"]  # no agent-home, no ops.log
    assert all(b.repo is None for b in found)


def test_a_found_bench_reads_what_the_bench_it_was_made_from_wrote(root):
    found = Bench.from_dir(root.calc.dir)
    assert found.dir == root.calc.dir and found.name == "calc"
    assert found.load_tasks() == root.calc.load_tasks()
    assert found.load_config() == root.calc.load_config()
    assert manifest.load_all(found) == manifest.load_all(root.calc)


def test_a_bench_named_without_a_hash_keeps_its_whole_name(tmp_path):
    d = tmp_path / "handmade"
    d.mkdir()
    (d / "tasks.jsonl").write_text("")
    assert Bench.from_dir(d).name == "handmade"
    assert find_benches(tmp_path)[0].dir == d


def test_a_missing_root_has_no_benches_and_is_not_created(tmp_path, monkeypatch):
    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(tmp_path / "nothing-here"))
    assert find_benches() == []
    assert not (tmp_path / "nothing-here").exists()
