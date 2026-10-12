"""Tests for the audit over HTTP, and for finding benches on disk.

A temp bench root holds three benches written through the real dataclasses:
`calc` (two arms, both conditions, repeats, one run manifest, one degraded
attempt), `widgets` (two arms, no manifest, attempts that predate manifests) and
`fresh` (mined, never run). `agent-home` sits beside them as a directory that
is not a bench. Every endpoint is read against it, and the numbers are checked
against the engine's own functions on the same data, not against constants.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.orchestrator.agentbench import manifest, ollama
from backend.orchestrator.agentbench.bank import (
    Bench, SuiteConfig, Task, bench_root, find_benches,
)
from backend.orchestrator.agentbench.report import precision_plan, summarise
from backend.orchestrator.agentbench.runner import Attempt, load_attempts
from backend.orchestrator.agentbench.stats import task_rate
from backend.orchestrator.audit import pricing
from backend.orchestrator.audit.evidence import from_benches
from backend.orchestrator.audit.verdict import Assumptions, Billing, breakeven_rate, decide, render
from backend.orchestrator.service import ui

QWEN, GRANITE = "aider:qwen3.5:latest", "aider:granite4.1:8b"
RUN_ID = "20261001T220000-ab12"
T0 = 1_790_000_000  # a fixed mtime base, so "last activity" is not a race with the test run
BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


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


# What each arm did on each task, as written below: the expected rates come from
# these lists through stats.task_rate, not from the engine's own report.
CALC_QWEN_BLIND = [True, False, True, False, True, False]
CALC_QWEN_FEEDBACK = [[True, True], [False, True], [True, True], [False, False],
                      [True, False], [False, False]]
WIDGETS_QWEN_BLIND = [True, False, True, True]
WIDGETS_GRANITE_BLIND = [False, False, True, False]
# Why granite failed each calc task, in the order the attempts' fields say it.
GRANITE_FAILURES = [dict(edited_files=[], hit_gold_file=False), dict(hit_gold_file=False),
                    dict(regressions=2), dict(f2p_pass=1), dict(),
                    dict(edited_files=[], hit_gold_file=False)]


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
    for s, ok in zip(shas, CALC_QWEN_BLIND):
        _write(calc, _attempt(s, QWEN, "blind", ok, run_id=RUN_ID), mtime=T0 + 100)
    for s, reps in zip(shas, CALC_QWEN_FEEDBACK):
        for rep, ok in enumerate(reps):
            _write(calc, _attempt(s, QWEN, "feedback", ok, rep=rep, secs=720.0, run_id=RUN_ID),
                   mtime=T0 + 200 + rep)
    for s, why in zip(shas, GRANITE_FAILURES):
        _write(calc, _attempt(s, GRANITE, "blind", False, secs=480.0, run_id=RUN_ID, **why),
               mtime=T0 + 300)
    _write(calc, _attempt("c2", QWEN, "blind", False, run_id=RUN_ID,
                          degraded=["contended: a manual smoke test shared the GPU"]),
           degraded=True, mtime=T0 + 250)
    manifest.write(calc, _manifest(repos["calc"], ended_at="2026-10-02T06:10:00-0400",
                                   attempts=24, stopped="completed"))
    os.utime(manifest.path(calc, RUN_ID), (T0 + 400, T0 + 400))

    widgets = Bench(repos["widgets"])
    widgets.save_config(SuiteConfig(python="/usr/bin/python3"))
    wshas = ["w1", "w2", "w3", "w4"]
    widgets.save_tasks([_task(s, f2p=1, p2p=9) for s in wshas])
    for s, ok in zip(wshas, WIDGETS_QWEN_BLIND):
        _write(widgets, _attempt(s, QWEN, "blind", ok, secs=300.0), mtime=T0 + 50)
    for s, ok in zip(wshas, WIDGETS_GRANITE_BLIND):
        _write(widgets, _attempt(s, GRANITE, "blind", ok, secs=400.0), mtime=T0 + 50)

    fresh = Bench(repos["fresh"])
    fresh.save_config(SuiteConfig(python="/usr/bin/python3"))
    fresh.save_tasks([_task("f1")])

    (path / "agent-home" / "aider").mkdir(parents=True)
    (path / "ops.log").write_text("not a bench")
    return Root(path, calc, widgets, fresh, repos)


def _fake_ollama(monkeypatch, *, up: bool = True) -> list[float]:
    """Stand in for Ollama. Returns the timeouts it was probed with."""
    timeouts: list[float] = []

    def version(base_url=None, timeout=10):
        timeouts.append(timeout)
        if not up:
            raise httpx.ConnectError("refused")
        return "0.12.5"

    def pulled(base_url=None, timeout=10):
        timeouts.append(timeout)
        if not up:
            raise httpx.ConnectError("refused")
        return {"qwen3.5:latest": "sha256:aaa", "granite4.1:8b": "sha256:bbb",
                "agentbench/qwen3.5:latest-num_ctx32768": "sha256:ccc"}  # a tag the bench derived

    monkeypatch.setattr(ollama, "version", version)
    monkeypatch.setattr(ollama, "pulled_models", pulled)
    return timeouts


@pytest.fixture
def api(root, monkeypatch) -> TestClient:
    """The real app over the temp root, with Ollama stood in for. No lifespan:
    the audit needs none of the app's stores."""
    from backend.orchestrator.service.app import app

    monkeypatch.delenv("MAHORAGA_HEADLESS", raising=False)
    monkeypatch.delenv("MAHORAGA_DEMO", raising=False)
    _fake_ollama(monkeypatch)
    return TestClient(app)


def _id(bench: Bench) -> str:
    return bench.dir.name


def _detail(api: TestClient, bench: Bench) -> dict:
    return api.get(f"/api/audit/benches/{_id(bench)}").json()


def _iso_of(seconds: float) -> str:
    return datetime.fromtimestamp(seconds).astimezone().isoformat(timespec="seconds")


def _tree(path: Path) -> dict[str, tuple[int, int]]:
    """Every file and folder under `path` with its size and mtime, to show a call wrote nothing."""
    return {str(p.relative_to(path)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(path.rglob("*"))}


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


# ── The Ollama probes ─────────────────────────────────────────────────────────


def _ollama_says(monkeypatch, status: int = 200, **body):
    """Stand in for Ollama's HTTP server; returns the (url, timeout) calls made."""
    calls: list[tuple[str, float]] = []

    def get(url, timeout=None, **_):
        calls.append((url, timeout))
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    return calls


def test_the_version_probe_asks_ollama_and_keeps_to_its_timeout(monkeypatch):
    calls = _ollama_says(monkeypatch, version="0.12.5")
    assert ollama.version("http://ollama:11434", timeout=1.5) == "0.12.5"
    assert calls == [("http://ollama:11434/api/version", 1.5)]


def test_the_version_probe_fails_loudly_when_ollama_is_not_what_answered(monkeypatch):
    _ollama_says(monkeypatch, status=500)
    with pytest.raises(httpx.HTTPStatusError):
        ollama.version("http://x")
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None, **_: httpx.Response(
        200, text="<html>not ollama</html>", request=httpx.Request("GET", url)))
    with pytest.raises(ValueError):
        ollama.version("http://x")


def test_a_derived_tag_is_recognised_by_the_name_derive_gives_it(monkeypatch):
    class Everything(dict):
        def __contains__(self, name):  # as if Ollama already has the tag, so nothing is created
            return True

    monkeypatch.setattr(ollama, "pulled_models", lambda base_url=None, timeout=10: Everything())
    name = ollama.derive("qwen3.5:latest", {"num_ctx": 32768})
    assert ollama.is_derived(name) and name == "agentbench/qwen3.5:latest-num_ctx32768"
    assert not ollama.is_derived("qwen3.5:latest") and not ollama.is_derived("library/agentbench")


def test_the_model_list_takes_a_timeout_too(monkeypatch):
    calls = _ollama_says(monkeypatch, models=[{"name": "qwen3.5:latest", "digest": "sha256:aaa"}])
    assert ollama.pulled_models("http://x", timeout=1.5) == {"qwen3.5:latest": "sha256:aaa"}
    assert calls == [("http://x/api/tags", 1.5)]


# ── GET /api/engine ───────────────────────────────────────────────────────────


def test_engine_says_what_it_is_and_where_it_runs(api, root):
    body = api.get("/api/engine").json()
    assert set(body) == {"name", "version", "api", "headless", "ui", "demo", "bench_root",
                         "data_dir", "python", "platform", "ollama"}
    assert body["name"] == "mahoraga" and body["api"] == 1
    assert body["version"] == importlib.metadata.version("mahoraga")
    assert body["bench_root"] == str(root.path)
    assert body["data_dir"] == str(Path.home() / ".mahoraga-v2")
    assert body["python"] == platform.python_version()
    assert body["platform"] == platform.platform()
    assert body["headless"] is False and body["demo"] is False
    assert body["ollama"] == {"url": ollama.BASE_URL, "up": True, "version": "0.12.5"}


def test_the_version_is_what_the_install_says_and_unknown_without_one(monkeypatch):
    from backend.orchestrator import version

    assert version.package_version() == importlib.metadata.version("mahoraga")

    def not_installed(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(version, "version", not_installed)
    assert version.package_version() == "unknown"


def test_engine_reports_demo_and_headless_from_the_environment(api, monkeypatch):
    monkeypatch.setenv("MAHORAGA_DEMO", "1")
    assert api.get("/api/engine").json()["demo"] is True
    monkeypatch.setenv("MAHORAGA_HEADLESS", "1")
    body = api.get("/api/engine").json()
    assert body["headless"] is True and body["ui"] is False


def test_engine_says_whether_a_ui_is_served(api, tmp_path, monkeypatch):
    missing = Path("/nonexistent-ui")
    monkeypatch.setattr(ui, "_PACKAGE_UI", missing)
    monkeypatch.setattr(ui, "_DEV_UI", missing)
    monkeypatch.setattr(ui, "_LEGACY_UI", missing)
    assert api.get("/api/engine").json()["ui"] is False
    built = tmp_path / "ui"
    built.mkdir()
    (built / "index.html").write_text("<title>app</title>")
    monkeypatch.setattr(ui, "_PACKAGE_UI", built)
    assert api.get("/api/engine").json()["ui"] is True


def test_engine_still_answers_when_ollama_is_down_and_does_not_wait_for_it(api, monkeypatch):
    timeouts = _fake_ollama(monkeypatch, up=False)
    body = api.get("/api/engine").json()
    assert body["ollama"] == {"url": ollama.BASE_URL, "up": False, "version": None}
    assert timeouts == [1.5]


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("slow"), ValueError("not ollama"),
                                     httpx.InvalidURL("bad url")])
def test_engine_treats_any_probe_failure_as_ollama_being_down(api, monkeypatch, failure):
    def version(base_url=None, timeout=10):
        raise failure

    monkeypatch.setattr(ollama, "version", version)
    assert api.get("/api/engine").json()["ollama"]["up"] is False


def test_engine_starts_no_process_of_its_own(api, monkeypatch):
    api.get("/api/engine")  # the first call lets platform.platform() ask `uname -p` once

    def boom(*a, **k):
        raise AssertionError("the engine endpoint started a process")

    import subprocess
    monkeypatch.setattr(subprocess, "Popen", boom)
    assert api.get("/api/engine").status_code == 200


# ── GET /api/audit/benches ────────────────────────────────────────────────────


def test_the_bench_list_says_what_each_bench_holds(api, root):
    body = api.get("/api/audit/benches").json()
    assert list(body) == ["benches"]
    by_name = {b["name"]: b for b in body["benches"]}
    assert set(by_name) == {"calc", "widgets", "fresh"}  # not agent-home, not a stray file
    assert all(set(b) == {"id", "name", "repo", "tasks", "dropped", "attempts", "degraded",
                          "arms", "conds", "runs", "last_activity"} for b in body["benches"])
    assert by_name["calc"] == {
        "id": _id(root.calc), "name": "calc", "repo": str(root.repos["calc"]),
        "tasks": 6, "dropped": 2, "attempts": 24, "degraded": 1,
        "arms": [GRANITE, QWEN], "conds": ["blind", "feedback"], "runs": 1,
        "last_activity": _iso_of(T0 + 400)}
    assert by_name["widgets"] == {
        "id": _id(root.widgets), "name": "widgets", "repo": None,  # never run: no manifest says where
        "tasks": 4, "dropped": 0, "attempts": 8, "degraded": 0,
        "arms": [GRANITE, QWEN], "conds": ["blind"], "runs": 0,
        "last_activity": _iso_of(T0 + 50)}
    assert by_name["fresh"] == {
        "id": _id(root.fresh), "name": "fresh", "repo": None, "tasks": 1, "dropped": 0,
        "attempts": 0, "degraded": 0, "arms": [], "conds": [], "runs": 0, "last_activity": None}


def test_the_bench_list_is_newest_activity_first_and_never_run_last(api):
    assert [b["name"] for b in api.get("/api/audit/benches").json()["benches"]] == [
        "calc", "widgets", "fresh"]


def test_a_task_that_left_the_bench_takes_its_attempts_out_of_the_count(api, root):
    _write(root.widgets, _attempt("w-gone", QWEN, "blind", True, secs=300.0))
    widgets = next(b for b in api.get("/api/audit/benches").json()["benches"] if b["name"] == "widgets")
    assert widgets["attempts"] == 8  # the same rows the arm tables count
    detail = api.get(f"/api/audit/benches/{_id(root.widgets)}").json()
    assert sum(a["n"] for a in detail["arms"]) == 8 and "w-gone" not in detail["matrix"]


def test_an_unreadable_dropped_file_is_no_drops_not_an_error(api, root):
    root.calc.dropped_path.write_text('{"half": "writ')
    calc = next(b for b in api.get("/api/audit/benches").json()["benches"] if b["name"] == "calc")
    assert calc["dropped"] == 0
    assert api.get(f"/api/audit/benches/{_id(root.calc)}").json()["dropped"] == []


def test_with_no_bench_root_the_audit_is_empty_and_creates_nothing(api, tmp_path, monkeypatch):
    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(tmp_path / "not-yet"))
    assert api.get("/api/audit/benches").json() == {"benches": []}
    assert api.get("/api/audit/runs").json() == {"runs": []}
    assert api.get("/api/audit/arms").json()["arms"] == []
    assert not (tmp_path / "not-yet").exists()


# ── GET /api/audit/benches/{id} ───────────────────────────────────────────────


@pytest.fixture
def calc(api, root) -> dict:
    return _detail(api, root.calc)


def _arm(detail: dict, arm: str, cond: str) -> dict:
    return next(a for a in detail["arms"] if (a["arm"], a["cond"]) == (arm, cond))


def test_a_bench_page_has_the_contracts_fields(calc, root):
    assert set(calc) == {"id", "name", "repo", "config", "tasks", "dropped", "arms", "matrix",
                         "degraded_note", "runs"}
    assert (calc["id"], calc["name"], calc["repo"]) == (_id(root.calc), "calc", str(root.repos["calc"]))
    assert calc["config"] == {"python": "/usr/bin/python3", "pytest_args": ["-o", "pythonpath=src"]}
    assert [(a["arm"], a["cond"]) for a in calc["arms"]] == [
        (GRANITE, "blind"), (QWEN, "blind"), (QWEN, "feedback")]
    assert set(calc["arms"][0]) == {
        "arm", "cond", "n", "resolved", "tasks", "tasks_solved_ever", "tasks_solved_always",
        "repeats", "rate", "median_minutes", "outcomes", "broke_other_code", "touched_tests",
        "multi_file_resolved", "multi_file_n", "tier", "plan"}
    assert set(calc["arms"][0]["rate"]) == {"point", "lo", "hi", "tasks", "attempts"}
    assert set(calc["arms"][0]["plan"]) == {"target_half_width", "tasks_needed", "tasks_have",
                                            "attempts_needed", "nights", "text"}


def test_tasks_carry_counts_of_their_tests_not_the_test_ids(calc):
    assert len(calc["tasks"]) == 6
    c2 = next(t for t in calc["tasks"] if t["sha"] == "c2")
    assert c2 == {"sha": "c2", "subject": "fix c2", "body": "why c2",
                  "gold_src": ["pkg/a.py", "pkg/b.py"], "gold_lines": 20,
                  "test_files": ["tests/test_c2.py"], "f2p": 2, "p2p": 5}


def test_drops_come_from_dropped_json_as_sha_and_reason(calc):
    assert calc["dropped"] == [
        {"sha": "d1", "reason": "gate B: the tests already pass without the fix"},
        {"sha": "d2", "reason": "no source change"}]


def test_an_arms_rate_is_task_rate_on_the_same_attempts(calc):
    feedback = _arm(calc, QWEN, "feedback")
    assert feedback["rate"] == asdict(task_rate(
        {f"c{i + 1}": reps for i, reps in enumerate(CALC_QWEN_FEEDBACK)}))
    assert feedback["rate"]["point"] == 0.5 and feedback["rate"]["tasks"] == 6
    assert feedback["rate"]["attempts"] == 12  # repeats make tasks firmer, not the interval narrower
    blind = _arm(calc, QWEN, "blind")
    assert blind["rate"] == asdict(task_rate({f"c{i + 1}": [ok] for i, ok in enumerate(CALC_QWEN_BLIND)}))


def test_an_arms_tallies(calc):
    feedback = _arm(calc, QWEN, "feedback")
    assert (feedback["n"], feedback["resolved"], feedback["tasks"]) == (12, 6, 6)
    assert (feedback["tasks_solved_ever"], feedback["tasks_solved_always"], feedback["repeats"]) == (4, 2, 2)
    assert (feedback["multi_file_resolved"], feedback["multi_file_n"]) == (2, 4)  # c2 and c5
    assert feedback["median_minutes"] == 12.0 and feedback["tier"] == "test-verified"
    assert feedback["outcomes"] == {"resolved": 6, "wrong-fix": 6}
    granite = _arm(calc, GRANITE, "blind")
    assert granite["resolved"] == 0 and granite["broke_other_code"] == 1  # c3 regressed the suite
    assert granite["outcomes"] == {"no-edit": 2, "wrong-file": 1, "partial+broke": 1,
                                   "partial": 1, "wrong-fix": 1}
    assert granite["median_minutes"] == 8.0


def test_the_reports_own_tables_are_what_the_page_shows(calc, root):
    mine = summarise(root.calc.load_tasks(), load_attempts(root.calc))
    assert len(mine) == len(calc["arms"])
    for expected, got in zip(mine, calc["arms"]):
        assert got["rate"] == asdict(expected.interval)
        assert got["plan"] == {**asdict(precision_plan(expected)), "text": precision_plan(expected).fmt()}
        assert (got["n"], got["resolved"], got["outcomes"]) == (
            expected.n, expected.resolved, expected.outcomes)
    plan = _arm(calc, QWEN, "feedback")["plan"]
    assert plan["target_half_width"] == 0.10 and plan["tasks_have"] == 6
    assert plan["tasks_needed"] > 6 and "tasks" in plan["text"]


def test_the_matrix_has_one_outcome_per_repeat_and_a_row_for_every_task(api, calc, root):
    assert set(calc["matrix"]) == {f"c{i}" for i in range(1, 7)}
    cell = calc["matrix"]
    assert cell["c1"][f"{QWEN} / feedback"] == ["resolved", "resolved"]
    assert cell["c2"][f"{QWEN} / feedback"] == ["wrong-fix", "resolved"]  # in repeat order
    assert cell["c2"][f"{QWEN} / blind"] == ["wrong-fix"]  # the degraded try is not here
    assert cell["c3"][f"{GRANITE} / blind"] == ["partial+broke"]
    assert cell["c1"][f"{GRANITE} / blind"] == ["no-edit"]
    fresh = _detail(api, root.fresh)
    assert fresh["matrix"] == {"f1": {}} and fresh["arms"] == []


def test_degraded_attempts_are_noted_and_in_no_count(calc):
    assert "1 degraded attempts excluded" in calc["degraded_note"] and "contended" in calc["degraded_note"]
    assert _arm(calc, QWEN, "blind")["n"] == 6


def test_a_bench_with_no_degraded_attempts_has_no_note(api, root):
    assert _detail(api, root.widgets)["degraded_note"] is None


def test_a_bench_page_lists_its_runs(api, calc, root):
    assert [r["run_id"] for r in calc["runs"]] == [RUN_ID]
    assert _detail(api, root.widgets)["runs"] == []


def test_a_bench_without_a_config_has_null_for_it(api, root):
    root.widgets.config_path.unlink()
    assert _detail(api, root.widgets)["config"] is None


def test_an_unknown_bench_is_a_404_and_an_id_is_never_a_path(api, root):
    for bad in ("nope", "calc", "agent-home", "..", "%2e%2e", "%2e%2e%2fbenches", _id(root.calc) + "x"):
        r = api.get(f"/api/audit/benches/{bad}")
        assert r.status_code == 404 and r.headers["content-type"] == "application/json", bad
    assert api.get("/api/audit/benches/nope").json() == {"detail": "unknown bench 'nope'"}


# ── GET /api/audit/runs ───────────────────────────────────────────────────────


RUN_CALC = {
    "run_id": RUN_ID, "bench_id": None, "repo_name": "calc",
    "started_at": "2026-10-01T22:00:00-04:00", "ended_at": "2026-10-02T06:10:00-04:00",
    "stopped": "completed", "attempts": 24, "arms": [QWEN, GRANITE],
    "machine": {"model": "Mac16,1", "memory_gb": 24, "os": "macOS-15.1-arm64-arm-64bit"},
    "harness": {"commit": "a1b2c3d4e5f6", "dirty": False}, "ollama": "0.12.5",
}


def test_a_run_is_its_manifest_boiled_down(api, root):
    (run,) = api.get("/api/audit/runs").json()["runs"]
    assert run == {**RUN_CALC, "bench_id": _id(root.calc)}
    assert set(run) == {"run_id", "bench_id", "repo_name", "started_at", "ended_at", "stopped",
                        "attempts", "arms", "machine", "harness", "ollama"}


def test_runs_are_every_manifest_in_every_bench_newest_first(api, root):
    manifest.write(root.calc, _manifest(root.repos["calc"], "20260929T010000-0001",
                                        started_at="2026-09-29T01:00:00-0400"))
    manifest.write(root.widgets, _manifest(root.repos["widgets"], "20261003T230000-0002",
                                           started_at="2026-10-03T23:00:00-0400"))
    runs = api.get("/api/audit/runs").json()["runs"]
    assert [r["run_id"] for r in runs] == ["20261003T230000-0002", RUN_ID, "20260929T010000-0001"]
    assert [r["repo_name"] for r in runs] == ["widgets", "calc", "calc"]
    # the bench page shows the same runs for its own bench
    assert [r["run_id"] for r in _detail(api, root.calc)["runs"]] == [RUN_ID, "20260929T010000-0001"]
    # and a bench that has been run now says where its repo was
    widgets = next(b for b in api.get("/api/audit/benches").json()["benches"] if b["name"] == "widgets")
    assert widgets["repo"] == str(root.repos["widgets"]) and widgets["runs"] == 1


def test_the_newest_manifest_decides_where_the_repo_was(api, root):
    manifest.write(root.calc, _manifest(Path("/elsewhere/calc"), "20261009T010000-0003",
                                        started_at="2026-10-09T01:00:00-0400"))
    calc = next(b for b in api.get("/api/audit/benches").json()["benches"] if b["name"] == "calc")
    assert calc["repo"] == "/elsewhere/calc" and calc["runs"] == 2


def test_a_run_still_going_has_no_end_and_a_failed_probe_is_null(api, root):
    manifest.write(root.widgets, _manifest(
        root.repos["widgets"], "20261004T010000-0004", started_at="2026-10-04T01:00:00-0400",
        machine={"os": "macOS", "model": "error: sysctl failed", "memory_gb": "error: no sysctl"},
        harness={"commit": "error: not a git repository", "dirty": False},
        models={"error": "Connection refused"}))
    run = next(r for r in api.get("/api/audit/runs").json()["runs"] if r["run_id"] == "20261004T010000-0004")
    assert (run["ended_at"], run["stopped"], run["attempts"]) == (None, None, None)
    assert run["machine"] == {"model": None, "memory_gb": None, "os": "macOS"}
    assert run["harness"] == {"commit": None, "dirty": False}
    assert run["ollama"] is None


def test_manifest_times_have_a_colon_in_the_offset_so_every_browser_reads_them(api):
    (run,) = api.get("/api/audit/runs").json()["runs"]
    assert datetime.fromisoformat(run["started_at"]).utcoffset().total_seconds() == -4 * 3600
    assert run["started_at"].endswith("-04:00")


def test_a_file_in_runs_that_is_not_a_manifest_is_skipped(api, root):
    (root.calc.dir / "runs" / "notes.json").write_text('{"hello": "world"}')
    (root.calc.dir / "runs" / "list.json").write_text("[1, 2]")
    assert [r["run_id"] for r in api.get("/api/audit/runs").json()["runs"]] == [RUN_ID]


# ── GET /api/audit/runs/{id} ──────────────────────────────────────────────────


def test_a_run_page_has_the_manifest_and_the_attempts_that_run_made(api, root):
    body = api.get(f"/api/audit/runs/{RUN_ID}").json()
    assert set(body) == {"run", "manifest", "attempts"}
    assert body["run"] == {**RUN_CALC, "bench_id": _id(root.calc)}
    assert body["manifest"] == manifest.load_all(root.calc)[0]  # the file as written
    # 24 counted attempts: not the degraded one, not widgets' (which predate manifests)
    assert len(body["attempts"]) == 24 == body["run"]["attempts"]
    assert {a["run_id"] for a in body["attempts"]} == {RUN_ID}
    stored = {(a.sha, a.arm, a.cond, a.rep): a for a in load_attempts(root.calc)}
    for got in body["attempts"]:
        base = {k: v for k, v in got.items() if k not in ("outcome", "subject")}
        assert base == asdict(stored[(got["sha"], got["arm"], got["cond"], got["rep"])])
        assert got["subject"] == f"fix {got['sha']}"
    outcomes = {(a["arm"], a["cond"], a["sha"], a["rep"]): a["outcome"] for a in body["attempts"]}
    assert outcomes[(QWEN, "feedback", "c2", 1)] == "resolved"
    assert outcomes[(GRANITE, "blind", "c3", 0)] == "partial+broke"


def test_a_run_page_lists_only_its_own_attempts(api, root):
    other = "20260929T010000-0001"
    manifest.write(root.calc, _manifest(root.repos["calc"], other, started_at="2026-09-29T01:00:00-0400"))
    _write(root.calc, _attempt("c1", QWEN, "blind", True, rep=1, run_id=other))
    mine = api.get(f"/api/audit/runs/{RUN_ID}").json()["attempts"]
    theirs = api.get(f"/api/audit/runs/{other}").json()["attempts"]
    assert len(mine) == 24 and {a["run_id"] for a in mine} == {RUN_ID}
    assert [(a["sha"], a["rep"], a["run_id"]) for a in theirs] == [("c1", 1, other)]


def test_an_unknown_run_is_a_404(api):
    r = api.get("/api/audit/runs/20200101T000000-dead")
    assert r.status_code == 404 and r.json() == {"detail": "unknown run '20200101T000000-dead'"}


# ── GET /api/audit/arms ───────────────────────────────────────────────────────


def _arms(api) -> dict:
    return api.get("/api/audit/arms").json()


def test_arms_are_pooled_over_benches(api, root):
    body = _arms(api)
    assert set(body) == {"arms", "models", "agents"}
    rows = {a["arm"]: a for a in body["arms"]}
    assert set(rows) == {QWEN, GRANITE}
    assert set(rows[QWEN]) == {"arm", "agent", "model", "benches", "attempts", "conds", "rate"}
    assert (rows[QWEN]["agent"], rows[QWEN]["model"]) == ("aider", "qwen3.5:latest")
    assert (rows[GRANITE]["agent"], rows[GRANITE]["model"]) == ("aider", "granite4.1:8b")
    assert (rows[QWEN]["benches"], rows[QWEN]["attempts"]) == (2, 6 + 12 + 4)
    assert (rows[GRANITE]["benches"], rows[GRANITE]["attempts"]) == (2, 6 + 4)
    assert rows[QWEN]["conds"] == ["blind", "feedback"] and rows[GRANITE]["conds"] == ["blind"]


def test_a_rate_is_given_only_for_an_arm_under_one_condition(api):
    rows = {a["arm"]: a for a in _arms(api)["arms"]}
    assert rows[QWEN]["rate"] is None  # blind and feedback are not one rate
    # granite, blind only: each (bench, task) is one unit, whatever its repeats
    units = {f"calc/c{i + 1}": [False] for i in range(6)}
    units.update({f"widgets/w{i + 1}": [ok] for i, ok in enumerate(WIDGETS_GRANITE_BLIND)})
    assert rows[GRANITE]["rate"] == asdict(task_rate(units))
    assert rows[GRANITE]["rate"]["tasks"] == 10 and rows[GRANITE]["rate"]["point"] == 0.1


def test_arms_that_mix_grading_tiers_have_no_pooled_rate_rather_than_an_error(api, root):
    for sha, ok in zip(["w1", "w2", "w3", "w4"], WIDGETS_GRANITE_BLIND):
        _write(root.widgets, _attempt(sha, GRANITE, "blind", ok, secs=400.0, tier="judge-graded"))
    rows = {a["arm"]: a for a in _arms(api)["arms"]}
    assert rows[GRANITE]["rate"] is None and rows[QWEN]["benches"] == 2


def test_an_arm_names_its_agent_and_model_whatever_its_context(api, root):
    _write(root.widgets, _attempt("w1", "opencode:gpt-oss:20b@ctx=131072", "blind", True, secs=300.0))
    row = next(a for a in _arms(api)["arms"] if a["arm"] == "opencode:gpt-oss:20b@ctx=131072")
    assert (row["agent"], row["model"]) == ("opencode", "gpt-oss:20b")


def test_models_are_what_ollama_has_pulled_not_the_tags_the_bench_derived(api, monkeypatch):
    assert _arms(api)["models"] == [{"name": "granite4.1:8b", "digest": "sha256:bbb"},
                                    {"name": "qwen3.5:latest", "digest": "sha256:aaa"}]
    timeouts = _fake_ollama(monkeypatch, up=False)
    assert _arms(api)["models"] == [] and timeouts == [1.5]


def test_agents_are_the_ones_found_on_the_path(api, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/aider" if name == "aider" else None)
    assert _arms(api)["agents"] == [{"name": "aider", "on_path": True},
                                    {"name": "opencode", "on_path": False}]


# ── GET /api/audit/pricing ────────────────────────────────────────────────────


def test_pricing_is_the_dated_plans_in_pricing_json(api):
    plans = api.get("/api/audit/pricing").json()["plans"]
    assert [p["id"] for p in plans] == list(pricing.load())
    assert plans == [asdict(p) for p in pricing.load().values()]
    assert set(plans[0]) == {"id", "vendor", "name", "kind", "usd_per_month", "capacity_vs_pro",
                             "as_of", "source", "verified", "note"}
    unverified = next(p for p in plans if p["id"] == "claude-max-20x")
    assert unverified["usd_per_month"] is None and unverified["verified"] is False


# ── POST /api/audit/verdict ───────────────────────────────────────────────────


def _ask(api, *, arm=QWEN, cond="blind", billing=None, assumptions=None, benches=None, **extra):
    body = {"benches": benches, "arm": arm, "cond": cond,
            "billing": billing or {"kind": "api", "monthly_usd": 150},
            "assumptions": assumptions or {"tasks_per_month": 200}, **extra}
    return api.post("/api/audit/verdict", json=body)


def test_the_verdict_is_decide_on_the_evidence_pooled_from_the_benches(api, root):
    got = _ask(api).json()
    ev = from_benches(find_benches(), QWEN, "blind")
    a = Assumptions(tasks_per_month=200)
    v = decide(ev, a, Billing.api(150))
    assert set(got) == {"call", "confident", "reason", "evidence", "assumptions", "billing",
                        "monthly_usd", "breakeven", "sensitivity", "rendered"}
    assert (got["call"], got["confident"], got["reason"]) == (v.call, v.confident, v.reason)
    assert got["sensitivity"] == v.sensitivity and got["rendered"] == render(v)
    assert got["evidence"] == asdict(ev)
    assert got["monthly_usd"] == dict(zip(("lo", "point", "hi"), v.monthly_usd))
    assert got["billing"] == {"kind": "api", "monthly_usd": 150.0, "plan": None, "lower": None}
    assert got["assumptions"] == {**asdict(a), "lines": a.lines()}


def test_the_evidence_is_the_pooled_task_rate(api):
    ev = _ask(api).json()["evidence"]
    units = {f"calc/c{i + 1}": [ok] for i, ok in enumerate(CALC_QWEN_BLIND)}
    units.update({f"widgets/w{i + 1}": [ok] for i, ok in enumerate(WIDGETS_QWEN_BLIND)})
    assert ev["rate"] == asdict(task_rate(units))
    assert ev["rate"]["tasks"] == 10 and ev["rate"]["point"] == 0.6
    assert ev["minutes_per_attempt"] == 10.0 and ev["tier"] == "test-verified"
    assert ev["label"] == f"{QWEN} / blind, 2 repos (calc, widgets)"
    assert "Mac16,1, 24 GB" in ev["hardware"] and "predate run manifests" in ev["hardware"]


def test_breakeven_rate_is_where_the_net_crosses_zero():
    from backend.orchestrator.agentbench.stats import Rate
    from backend.orchestrator.audit.verdict import Evidence

    def evidence(point: float) -> Evidence:
        return Evidence(rate=Rate(point, point, point, 50, 50), minutes_per_attempt=10,
                        tier="test-verified", label="x")

    a = Assumptions(tasks_per_month=100, deferrable_share=1.0, triage_minutes=6, hourly_usd=60,
                    watts=40, usd_per_kwh=0.30)
    p_star = breakeven_rate(evidence(0.5), a, cloud_per_task=4.0)
    assert 0 < p_star < 1
    v = decide(evidence(p_star), a, Billing.api(400))  # $4 a cloud task
    assert v.monthly_usd == pytest.approx((0, 0, 0), abs=1e-9)
    assert f"≥ {p_star:.0%}" in v.sensitivity[0]  # the verdict prints this same number
    assert breakeven_rate(evidence(0.5), a, 8.0) < p_star  # dearer cloud tasks: local pays sooner
    hog = Assumptions(tasks_per_month=100, watts=400, usd_per_kwh=3.0)  # $0.20 of power an attempt
    assert breakeven_rate(evidence(0.5), hog, cloud_per_task=0.10) > 1  # no rate pays


def test_the_breakeven_is_where_local_pays_in_the_engines_own_formula(api):
    got = _ask(api, assumptions={"tasks_per_month": 200}).json()
    # $150 over 200 tasks is $0.75 a cloud task; 10 min at 40 W and $0.30/kWh is $0.002 of power;
    # 3 min of triage at $50/h is $2.50.
    assert got["breakeven"] == pytest.approx((0.002 + 2.5) / (0.75 + 2.5))
    assert f"{got['breakeven']:.0%}" in got["sensitivity"][0]
    ev = from_benches(find_benches(), QWEN, "blind")
    assert got["breakeven"] == breakeven_rate(ev, Assumptions(tasks_per_month=200), 150 / 200)


def test_assumptions_default_in_the_engine_and_every_field_comes_back(api):
    a = _ask(api).json()["assumptions"]
    assert a == {"tasks_per_month": 200, "deferrable_share": 0.25, "triage_minutes": 3.0,
                 "hourly_usd": 50.0, "watts": 40.0, "usd_per_kwh": 0.3, "usage_of_cap": 1.0,
                 "lines": a["lines"]}
    assert "200 cloud coding tasks a month" in a["lines"][0]
    sent = {"tasks_per_month": 100, "deferrable_share": 0.9, "triage_minutes": 6, "hourly_usd": 80,
            "watts": 120, "usd_per_kwh": 0.12, "usage_of_cap": 0.5}
    got = _ask(api, assumptions=sent).json()["assumptions"]
    assert {k: got[k] for k in sent} == sent
    assert got["lines"] == Assumptions(**sent).lines()


def test_an_explicit_null_is_the_default(api):
    sent = {"tasks_per_month": 200, "deferrable_share": None, "watts": None}
    assert _ask(api, assumptions=sent).json()["assumptions"]["watts"] == 40.0


def test_a_subscription_names_its_plan_and_the_tier_below(api):
    got = _ask(api, billing={"kind": "subscription", "plan": "claude-max-5x"}).json()
    assert got["breakeven"] is None and got["monthly_usd"] is None  # a flat plan has no per-task saving
    assert got["billing"]["kind"] == "subscription" and got["billing"]["monthly_usd"] == 100.0
    assert got["billing"]["plan"]["id"] == "claude-max-5x"
    assert got["billing"]["lower"]["id"] == "claude-pro"
    ev = from_benches(find_benches(), QWEN, "blind")
    p = pricing.plan("claude-max-5x")
    v = decide(ev, Assumptions(tasks_per_month=200), Billing.subscription(p, pricing.lower_tier(p)))
    assert (got["call"], got["confident"], got["reason"]) == (v.call, v.confident, v.reason)


def test_a_price_you_supply_is_used_and_marked_unverified(api):
    got = _ask(api, billing={"kind": "subscription", "plan": "claude-max-20x", "plan_price": 200}).json()
    assert got["billing"]["monthly_usd"] == 200.0
    assert got["billing"]["plan"]["verified"] is False
    assert "supplied by the user" in got["billing"]["plan"]["note"]


def test_one_bench_can_be_asked_alone_and_too_few_tasks_decide_nothing(api, root):
    got = _ask(api, benches=[_id(root.widgets)]).json()
    assert got["evidence"]["rate"]["tasks"] == 4
    assert got["evidence"]["label"] == f"{QWEN} / blind, 1 repo (widgets)"
    assert got["call"] == "stay" and got["confident"] is False and "too few" in got["reason"]
    assert got["monthly_usd"] is None
    assert got["breakeven"] is not None  # a property of the inputs, not of the evidence
    twice = _ask(api, benches=[_id(root.widgets), _id(root.widgets)]).json()
    assert twice["evidence"]["rate"]["attempts"] == 4  # named twice, counted once


def test_the_verdict_is_a_pure_computation(api, root):
    before = _tree(root.path)
    for billing in ({"kind": "api", "monthly_usd": 150}, {"kind": "subscription", "plan": "claude-pro"}):
        assert _ask(api, billing=billing).status_code == 200
    assert _ask(api, arm="aider:nope:1b").status_code == 409
    assert _tree(root.path) == before


def test_no_read_endpoint_writes_to_the_bench_store(api, root):
    before = _tree(root.path)
    for path in ("/api/engine", "/api/audit/benches", f"/api/audit/benches/{_id(root.calc)}",
                 "/api/audit/runs", f"/api/audit/runs/{RUN_ID}", "/api/audit/arms", "/api/audit/pricing"):
        assert api.get(path).status_code == 200, path
    assert _tree(root.path) == before


def test_errors_say_what_is_wrong(api, root):
    unknown_bench = _ask(api, benches=["nope-00000000"])
    assert unknown_bench.status_code == 404 and "unknown bench 'nope-00000000'" in unknown_bench.json()["detail"]
    no_attempts = _ask(api, arm="aider:nope:1b")
    assert no_attempts.status_code == 409 and "no attempts for aider:nope:1b / blind" in no_attempts.json()["detail"]
    assert _ask(api, arm=GRANITE, cond="feedback").status_code == 409  # granite never ran with feedback
    unverified = _ask(api, billing={"kind": "subscription", "plan": "claude-max-20x"})
    assert unverified.status_code == 400
    assert "billing.plan_price" in unverified.json()["detail"]  # a field here, not a CLI flag
    assert "--plan-price" not in unverified.json()["detail"]
    unknown_plan = _ask(api, billing={"kind": "subscription", "plan": "claude-gold"})
    assert unknown_plan.status_code == 400 and "unknown plan 'claude-gold'" in unknown_plan.json()["detail"]
    assert "claude-pro" in unknown_plan.json()["detail"]  # it lists the ones that exist


def test_evidence_the_verdict_cannot_use_is_a_409_not_a_crash(api, root):
    for sha, ok in zip(["w1", "w2", "w3", "w4"], WIDGETS_GRANITE_BLIND):
        _write(root.widgets, _attempt(sha, GRANITE, "blind", ok, secs=400.0, tier="judge-graded"))
    mixed = _ask(api, arm=GRANITE)  # both benches: test-verified and judge-graded
    assert mixed.status_code == 409 and "mixes grading tiers" in mixed.json()["detail"]
    judged = _ask(api, arm=GRANITE, benches=[_id(root.widgets)])
    assert judged.status_code == 409 and "test-verified evidence only" in judged.json()["detail"]


@pytest.mark.parametrize("change", [
    {"assumptions": {}},
    {"assumptions": {"tasks_per_month": 0}},
    {"assumptions": {"tasks_per_month": 200, "deferrable_share": 1.5}},
    {"assumptions": {"tasks_per_month": 200, "deferrable_share": 0}},
    {"assumptions": {"tasks_per_month": 200, "watts": -1}},
    {"assumptions": {"tasks_per_month": 200, "deferable_share": 0.5}},  # a typo is an error, not a default
    {"billing": {"kind": "api"}},
    {"billing": {"kind": "api", "monthly_usd": 0}},
    {"billing": {"kind": "api", "monthly_usd": 150, "plan": "claude-pro"}},
    {"billing": {"kind": "barter", "monthly_usd": 150}},
    {"billing": {"kind": "subscription"}},
    {"billing": {"kind": "subscription", "plan": "claude-pro", "plan_price": -5}},
    {"benches": []},
    {"arm": None},
    {"cond": 3},
    {"surprise": True},
])
def test_a_request_that_is_not_well_formed_is_a_422(api, change):
    body = {"benches": None, "arm": QWEN, "cond": "blind", "billing": {"kind": "api", "monthly_usd": 150},
            "assumptions": {"tasks_per_month": 200}, **change}
    r = api.post("/api/audit/verdict", json=body)
    assert r.status_code == 422, r.text


def test_a_request_with_no_arm_at_all_is_a_422(api):
    r = api.post("/api/audit/verdict", json={"billing": {"kind": "api", "monthly_usd": 1},
                                             "assumptions": {"tasks_per_month": 1}, "cond": "blind"})
    assert r.status_code == 422


# ── The contract as published, and the app around it ──────────────────────────


def test_every_endpoint_is_in_the_published_api_description(api):
    spec = api.get("/openapi.json").json()
    for path, verb in [("/api/engine", "get"), ("/api/audit/benches", "get"),
                       ("/api/audit/benches/{bench_id}", "get"), ("/api/audit/runs", "get"),
                       ("/api/audit/runs/{run_id}", "get"), ("/api/audit/arms", "get"),
                       ("/api/audit/pricing", "get"), ("/api/audit/verdict", "post")]:
        assert verb in spec["paths"][path], path
    assert {"Engine", "BenchDetail", "ArmSummary", "Verdict", "VerdictRequest", "Rate"} <= set(
        spec["components"]["schemas"])
    assert "/{full_path}" not in spec["paths"]  # the UI's catch-all is not part of the API


def test_a_missing_audit_path_is_a_json_404_even_to_a_browser(api):
    for path in ("/api/audit/nope", "/api/audit", "/api/audit/benches/a/b"):
        r = api.get(path, headers={"Accept": BROWSER})
        assert r.status_code == 404 and r.headers["content-type"] == "application/json", path


def test_the_audit_api_answers_a_browser_with_json_too(api):
    assert api.get("/api/audit/pricing", headers={"Accept": BROWSER}).json()["plans"]


def test_the_audit_api_is_open_to_the_vite_dev_server(api):
    r = api.get("/api/audit/benches", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    pre = api.options("/api/audit/verdict", headers={
        "Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
