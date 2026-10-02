"""Tests for the agent bench: mining, gating, grading, resume, and the CLI.

Everything runs against a tiny synthetic git repo with a real pytest suite,
and fake agents that stand in for the behaviours that matter: apply the real
fix, do nothing, cheat by editing the tests, and fix the target while
breaking something else. No Ollama, no models: the machine the guard
watches is faked too.
"""
from __future__ import annotations

import itertools
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from backend.orchestrator.agentbench import manifest
from backend.orchestrator.agentbench.agents import (
    AgentRun, AiderAgent, OpenCodeAgent, parse_arm, parse_opencode_events,
)
from backend.orchestrator.agentbench.bank import Bench, SuiteConfig
from backend.orchestrator.agentbench.mine import (
    find_candidates, is_test_module, is_test_path, layout_pytest_args, mine,
)
from backend.orchestrator.agentbench.grade import TEST_VERIFIED, Grade
from backend.orchestrator.agentbench.guard import Guard
from backend.orchestrator.agentbench.stats import plan, task_rate, tasks_needed, wilson
from backend.orchestrator.agentbench.host import Power, parse_idle, parse_power
from backend.orchestrator.agentbench.report import outcome, render, summarise
from backend.orchestrator.agentbench.runner import (
    load_attempts, load_degraded, prompt_for, run_attempt, run_matrix,
)
from backend.orchestrator.cli.commands.agentbench import app as agentbench_app, parse_until


# ── A repo with history ───────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "--short", "HEAD")


OPS = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
TESTS = ("from calc.ops import add, sub\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
         "def test_sub():\n    assert sub(5, 3) == 2\n")


@pytest.fixture
def repo(tmp_path: Path) -> dict:
    r = tmp_path / "calc-repo"
    r.mkdir()
    _git(r, "init", "-q")
    shas = {"init": _commit(r, "init", {"calc/__init__.py": "", "calc/ops.py": OPS,
                                        "tests/test_ops.py": TESTS})}
    # A real task: new source + a test that fails without it.
    shas["mul"] = _commit(r, "feat: add mul", {
        "calc/ops.py": OPS + "\n\ndef mul(a, b):\n    return a * b\n",
        "tests/test_mul.py": "from calc.ops import mul\n\n\ndef test_mul():\n"
                             "    assert mul(3, 4) == 12\n"})
    # Gate B drop: the new test already passes without the source change.
    shas["refactor"] = _commit(r, "refactor: comment add", {
        "calc/ops.py": "# arithmetic\n" + OPS + "\n\ndef mul(a, b):\n    return a * b\n",
        "tests/test_add_more.py": "from calc.ops import add\n\n\ndef test_add_zero():\n"
                                  "    assert add(0, 0) == 0\n"})
    shas["docs"] = _commit(r, "docs", {"README.md": "calc\n"})
    return {"path": r, "shas": shas}


@pytest.fixture
def bench(repo, tmp_path) -> Bench:
    return Bench(repo["path"], root=tmp_path / "state")


@pytest.fixture
def config() -> SuiteConfig:
    return SuiteConfig(python=sys.executable)


@pytest.fixture
def mined(bench, config, repo) -> Bench:
    mine(bench, config, [repo["shas"]["mul"], repo["shas"]["refactor"]])
    return bench


# ── Fake agents ───────────────────────────────────────────────────────────────


class FakeAgent:
    name = "fake"

    def __init__(self, model: str, edit=lambda clone, task_sha: None):
        self.model, self._edit = model, edit
        self.calls: list[dict] = []

    def attempt(self, workdir, prompt, *, test_cmd, timeout):
        self.calls.append({"prompt": prompt, "test_cmd": test_cmd})
        sha = _git(workdir, "log", "--all", "--format=%h", "-1", "--grep=feat: add mul")
        self._edit(workdir, sha)
        return AgentRun(log="", secs=1.0, timed_out=False)


def apply_gold(clone: Path, sha: str) -> None:
    diff = _git(clone, "diff", f"{sha}^", sha, "--", "calc/ops.py")
    subprocess.run(["git", "apply"], cwd=clone, input=diff + "\n", text=True, check=True)


def edit_tests(clone: Path, sha: str) -> None:
    (clone / "tests/test_mul.py").write_text("def test_mul():\n    pass\n")


def fix_and_break(clone: Path, sha: str) -> None:
    apply_gold(clone, sha)
    ops = clone / "calc/ops.py"
    ops.write_text(ops.read_text().replace("return a - b", "return a + b"))


# ── Mining ────────────────────────────────────────────────────────────────────


def test_test_path_rules():
    assert is_test_path("tests/helpers.py") and not is_test_module("tests/helpers.py")
    assert is_test_module("pkg/test_x.py") and is_test_module("pkg/x_test.py")
    assert is_test_path("conftest.py") and not is_test_module("conftest.py")
    assert is_test_module("tests/tests_tqdm.py")  # tqdm's naming
    assert not is_test_path("calc/ops.py")


def test_find_candidates_needs_source_and_a_test_module(repo):
    found = {c.sha for c in find_candidates(repo["path"])}
    # init is a root commit (no parent to base on); docs changes no source.
    assert found == {repo["shas"]["mul"], repo["shas"]["refactor"]}
    assert all(c.src_lines > 0 for c in find_candidates(repo["path"]))


def test_find_candidates_respects_limit_and_size(repo):
    assert len(find_candidates(repo["path"], limit=1)) == 1
    # mul changes 4 source lines, refactor changes 1.
    assert [c.sha for c in find_candidates(repo["path"], max_src_lines=1)] == [
        repo["shas"]["refactor"]]


def test_mine_gates_and_baselines(mined, repo):
    tasks = {t.sha: t for t in mined.load_tasks()}
    mul = tasks[repo["shas"]["mul"]]
    assert mul.f2p == ["tests.test_mul::test_mul"]
    assert set(mul.p2p) == {"tests.test_ops::test_add", "tests.test_ops::test_sub"}
    assert mul.gold_src == ["calc/ops.py"] and mul.gold_lines == 4
    dropped = json.loads(mined.dropped_path.read_text())
    assert dropped[repo["shas"]["refactor"]].startswith("gate B")
    assert repo["shas"]["refactor"] not in tasks


def test_task_base_has_tests_but_not_the_fix(mined, repo):
    clone = mined.clone(repo["shas"]["mul"])
    assert (clone / "tests/test_mul.py").exists()
    assert "def mul" not in (clone / "calc/ops.py").read_text()


def test_mining_never_writes_to_the_repo(mined, repo):
    assert _git(repo["path"], "status", "--porcelain") == ""


# ── Grading ───────────────────────────────────────────────────────────────────


def _task(bench, repo):
    return next(t for t in bench.load_tasks() if t.sha == repo["shas"]["mul"])


def test_gold_fix_resolves(mined, config, repo):
    a = run_attempt(mined, config, _task(mined, repo), FakeAgent("gold", apply_gold), "blind")
    assert a.resolved and outcome(a) == "resolved"
    assert a.edited_files == ["calc/ops.py"] and a.hit_gold_file and a.regressions == 0


def test_no_edit(mined, config, repo):
    a = run_attempt(mined, config, _task(mined, repo), FakeAgent("noop"), "blind")
    assert not a.resolved and outcome(a) == "no-edit"


def test_editing_tests_is_flagged_and_does_not_pass(mined, config, repo):
    a = run_attempt(mined, config, _task(mined, repo), FakeAgent("cheat", edit_tests), "blind")
    assert a.touched_tests == ["tests/test_mul.py"]
    assert not a.resolved  # graded against the restored, real test


def test_breaking_other_code_is_a_regression(mined, config, repo):
    a = run_attempt(mined, config, _task(mined, repo), FakeAgent("oops", fix_and_break), "blind")
    assert a.f2p_pass == 1 and not a.resolved
    assert a.regression_ids == ["tests.test_ops::test_sub"]
    assert outcome(a) == "broke-other"


def test_clone_is_reset_after_an_attempt(mined, config, repo):
    task = _task(mined, repo)
    run_attempt(mined, config, task, FakeAgent("oops", fix_and_break), "blind")
    assert _git(mined.clone(task.sha), "status", "--porcelain") == ""


def test_feedback_condition_passes_the_target_test_command(mined, config, repo):
    agent = FakeAgent("m")
    run_attempt(mined, config, _task(mined, repo), agent, "blind")
    run_attempt(mined, config, _task(mined, repo), agent, "feedback")
    assert agent.calls[0]["test_cmd"] is None
    assert agent.calls[1]["test_cmd"][-1] == "tests/test_mul.py"


def test_prompt_carries_message_and_test_files_but_no_source_paths(mined, repo):
    p = prompt_for(_task(mined, repo))
    assert "feat: add mul" in p and "tests/test_mul.py" in p and "calc/ops.py" not in p


# ── Resume, artifacts, report ─────────────────────────────────────────────────


def test_run_matrix_resumes(mined, repo):
    agent = FakeAgent("qwen3.5:latest", apply_gold)  # dotted name: artifact naming
    first = run_matrix(mined, [agent], ["blind"])
    assert len(first) == 1 and first[0].resolved
    assert run_matrix(mined, [agent], ["blind"]) == []
    names = sorted(p.name for p in mined.attempts.iterdir())
    assert names == [f"fake_qwen3.5_latest__blind__{repo['shas']['mul']}__r0.{ext}"
                     for ext in ("json", "log", "patch")]


def test_repeats_are_separate_attempts_and_report_stability(mined, repo):
    flaky = iter([apply_gold, lambda clone, sha: None, apply_gold])
    agent = FakeAgent("flaky", lambda clone, sha: next(flaky)(clone, sha))
    done = run_matrix(mined, [agent], ["blind"], repeats=3)
    assert [a.rep for a in done] == [0, 1, 2]
    assert [a.resolved for a in done] == [True, False, True]
    assert run_matrix(mined, [agent], ["blind"], repeats=3) == []  # resumes per repeat
    (s,) = summarise(mined.load_tasks(), load_attempts(mined))
    assert (s.n, s.resolved, s.repeats) == (3, 2, 3)
    assert (s.tasks_solved_ever, s.tasks_solved_always) == (1, 0)
    text = render(mined.load_tasks(), load_attempts(mined))
    assert "2/3 resolved" in text and "1/1 ever, 0/1 always" in text


def test_rebuilding_a_task_discards_its_attempts(mined, config, repo):
    run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"])
    mine(mined, config, [repo["shas"]["mul"]])
    assert load_attempts(mined) == []


def test_report(mined, repo):
    run_matrix(mined, [FakeAgent("good", apply_gold), FakeAgent("bad", fix_and_break)], ["blind"])
    by_arm = {s.arm: s for s in summarise(mined.load_tasks(), load_attempts(mined))}
    assert by_arm["fake:good"].resolved == 1
    assert by_arm["fake:bad"].broke_other_code == 1
    assert by_arm["fake:bad"].outcomes == {"broke-other": 1}
    text = render(mined.load_tasks(), load_attempts(mined))
    assert "fake:good" in text and "feat: add mul" in text


def test_layout_args_detect_a_src_layout(tmp_path):
    flat = tmp_path / "flat"
    (flat / "pkg").mkdir(parents=True)
    assert layout_pytest_args(flat) == []
    (tmp_path / "srcl" / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "srcl" / "src" / "pkg" / "__init__.py").write_text("")
    assert layout_pytest_args(tmp_path / "srcl") == ["-o", "pythonpath=src"]


def test_cli_mines_a_src_layout_repo_against_the_clone(tmp_path, monkeypatch):
    # Without pythonpath=src the tests can't import the clone's package at all,
    # and every candidate is dropped at gate A, which is what click and attrs did.
    r = tmp_path / "srcrepo"
    r.mkdir()
    _git(r, "init", "-q")
    _commit(r, "init", {"src/calc/__init__.py": "", "src/calc/ops.py": OPS,
                        "tests/test_ops.py": TESTS})
    _commit(r, "feat: add mul", {
        "src/calc/ops.py": OPS + "\n\ndef mul(a, b):\n    return a * b\n",
        "tests/test_mul.py": "from calc.ops import mul\n\n\ndef test_mul():\n"
                             "    assert mul(3, 4) == 12\n"})
    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(tmp_path / "state"))
    out = CliRunner().invoke(agentbench_app, ["mine", str(r), "--python", sys.executable])
    assert out.exit_code == 0, out.output
    assert "src layout" in out.output and "1 tasks in the bench" in out.output


# ── Grading tiers, run manifests, intervals ───────────────────────────────────


def test_attempts_record_their_tier_and_run(mined):
    (a,) = run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"], run_id="R1")
    assert (a.tier, a.run_id) == (TEST_VERIFIED, "R1")
    assert load_attempts(mined)[0].run_id == "R1"


def test_attempts_recorded_before_tiers_existed_still_load(mined):
    run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"])
    (p,) = mined.attempts.glob("*.json")
    old = json.loads(p.read_text())
    del old["tier"], old["run_id"]
    p.write_text(json.dumps(old))
    (a,) = load_attempts(mined)
    assert (a.tier, a.run_id) == (TEST_VERIFIED, "")


def test_a_custom_grader_decides_the_attempt(mined, config):
    class Never:
        tier = "judge-graded"

        def grade(self, clone, task, config, home):
            return Grade(tier=self.tier, resolved=False)

    (task,) = [t for t in mined.load_tasks()]
    a = run_attempt(mined, config, task, FakeAgent("m", apply_gold), "blind", grader=Never())
    assert (a.resolved, a.tier) == (False, "judge-graded")


def test_the_report_refuses_to_blend_tiers(mined):
    run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"], repeats=2)
    attempts = load_attempts(mined)
    attempts[1].tier = "judge-graded"
    with pytest.raises(ValueError, match="mixes grading tiers"):
        summarise(mined.load_tasks(), attempts)


def test_wilson_matches_known_values():
    lo, hi = wilson(1, 9)
    assert (round(lo, 3), round(hi, 3)) == (0.020, 0.435)
    lo, hi = wilson(0, 18)
    assert lo == 0 and round(hi, 3) == 0.176  # granite's 0/18 is not "0%, done"
    assert wilson(0, 0) == (0.0, 1.0)


def test_repeats_do_not_narrow_the_interval_tasks_do():
    once = task_rate({f"t{i}": [i < 3] for i in range(9)})
    thrice = task_rate({f"t{i}": [i < 3] * 3 for i in range(9)})
    assert (once.lo, once.hi) == (thrice.lo, thrice.hi)
    assert thrice.attempts == 27 and thrice.tasks == 9
    more = task_rate({f"t{i}": [i % 3 == 0] for i in range(36)})
    assert more.half_width < once.half_width


def test_rate_is_task_weighted():
    r = task_rate({"a": [True, True, True, True], "b": [False]})
    assert r.point == 0.5  # not 4/5: one task solved, one not


def test_tasks_needed_and_the_plan():
    assert 85 <= tasks_needed(0.5, 0.10) <= 100
    assert tasks_needed(0.1, 0.10) < tasks_needed(0.5, 0.10)
    r = task_rate({f"t{i}": [i == 0] for i in range(9)})
    p = plan(r, 0.10, minutes_per_attempt=10, cells=2)
    assert p.tasks_have == 9 and p.tasks_needed > 9
    assert p.attempts_needed == (p.tasks_needed - 9) * 2
    assert p.nights == -(-p.attempts_needed // 48)  # 8 h / 10 min
    assert "will not get there" in p.fmt()
    enough = task_rate({f"t{i}": [i % 10 == 0] for i in range(200)})
    assert plan(enough, 0.10, 10, 1).nights == 0


def test_the_report_shows_intervals_and_precision(mined):
    run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"])
    text = render(mined.load_tasks(), load_attempts(mined))
    assert "100% [" in text and "intervals count tasks" in text and "precision" in text


def test_a_manifest_survives_a_dead_ollama_and_is_stamped_at_the_end(mined, monkeypatch):
    def down(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("backend.orchestrator.agentbench.ollama.pulled_models", down)
    agent = FakeAgent("qwen3.5:latest")
    m = manifest.build(mined, [agent], mined.load_tasks(), run_id="R1",
                       settings={"repeats": 1})
    assert "refused" in m["models"]["error"]
    assert m["arms"] == ["fake:qwen3.5:latest"]
    assert m["tasks"]["n"] == 1 and len(m["tasks"]["prompts_sha256"]) == 64
    assert m["agents"]["fake"].startswith("error")  # no `fake` binary
    manifest.write(mined, m)
    manifest.finish(mined, "R1", attempts=3, stopped="deadline")
    (saved,) = manifest.load_all(mined)
    assert (saved["attempts"], saved["stopped"]) == (3, "deadline") and saved["ended_at"]


def test_the_prompt_hash_changes_when_the_prompts_do(mined):
    tasks = mined.load_tasks()
    a = manifest.tasks_fingerprint(mined, tasks)["prompts_sha256"]
    tasks[0].subject += " (edited)"
    assert manifest.tasks_fingerprint(mined, tasks)["prompts_sha256"] != a


def test_doctor_checks_the_bench(mined, config):
    from backend.orchestrator.agentbench.doctor import bench_checks

    checks = {c.name: c for c in bench_checks(mined)}
    assert checks["tasks"].ok and checks["test interpreter"].ok and checks["task clones"].ok
    (task,) = mined.load_tasks()
    import shutil as _sh
    _sh.rmtree(mined.clone(task.sha))
    assert not {c.name: c for c in bench_checks(mined)}["task clones"].ok


def test_doctor_flags_a_missing_test_interpreter(mined, config):
    from backend.orchestrator.agentbench.doctor import bench_checks

    mined.save_config(SuiteConfig(python="/nonexistent/python"))
    assert not {c.name: c for c in bench_checks(mined)}["test interpreter"].ok


def test_doctor_estimate_uses_measured_minutes_once_there_are_attempts(mined):
    from backend.orchestrator.agentbench.doctor import DEFAULT_MINUTES, estimate

    agent = FakeAgent("m", apply_gold)
    before = estimate(mined, [agent], ["blind", "feedback"], repeats=2)
    assert before.pending == 4 and not before.measured_minutes
    assert before.minutes_per_attempt == DEFAULT_MINUTES
    run_matrix(mined, [agent], ["blind"])
    after = estimate(mined, [agent], ["blind", "feedback"], repeats=2)
    assert after.pending == 3 and after.measured_minutes
    assert after.minutes_per_attempt == pytest.approx(1 / 60)  # FakeAgent: 1 s
    assert "3 attempts left" in after.fmt()


# ── Agents ────────────────────────────────────────────────────────────────────


def test_parse_arm():
    agent = parse_arm("aider:qwen3.5:latest")
    assert isinstance(agent, AiderAgent) and agent.model == "qwen3.5:latest"
    assert parse_arm("aider:someuser/model:7b").model == "someuser/model:7b"
    for bad in ("aider", "nope:qwen3.5", "aider:"):
        with pytest.raises(ValueError):
            parse_arm(bad)


@pytest.mark.parametrize("model", ["gpt-oss:120b-cloud", "qwen3-coder:cloud"])
def test_ollama_cloud_models_are_refused(model):
    with pytest.raises(ValueError, match="cloud"):
        AiderAgent(model)


def test_aider_settings_cap_generation():
    s = AiderAgent("qwen3.5:latest")._settings()
    assert "ollama_chat/qwen3.5:latest" in s and "num_predict: 8192" in s


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_mine_and_report(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(tmp_path / "cli-state"))
    runner = CliRunner()
    r = runner.invoke(agentbench_app, ["mine", str(repo["path"]), "--python", sys.executable])
    assert r.exit_code == 0, r.output
    assert "1 tasks in the bench, 1 dropped" in r.output
    r = runner.invoke(agentbench_app, ["report", str(repo["path"]), "--json"])
    assert r.exit_code == 0 and json.loads(r.output)["tasks"] == 1


def test_cli_run_rejects_a_bad_condition(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(tmp_path / "cli-state"))
    r = CliRunner().invoke(agentbench_app, ["run", str(repo["path"]),
                                            "--arm", "aider:qwen3.5:latest", "--cond", "sideways"])
    assert r.exit_code != 0


# ── Guard: only attempts on a healthy machine count ───────────────────────────

_AC = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=22937699)\t100%; charged; 0:00 remaining present: true\n"
_BATT = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=22937699)\t53%; discharging; 3:10 remaining present: true\n"


def test_parse_power():
    assert parse_power(_AC, " Wattage = 65W\n Current = 3240mA\n") == Power(True, 100, 65)
    assert parse_power(_BATT, "No adapter attached.\n") == Power(False, 53, None)
    assert parse_power("", "") == Power(None, None, None)


def test_parse_idle():
    assert parse_idle('    | | |   "HIDIdleTime" = 40958666000\n') == pytest.approx(40.958666)
    assert parse_idle("") is None


class FakeMachine:
    def __init__(self, speeds=(30.0,)):
        self.on_ac, self.watts, self.idle = True, 65, 3600.0
        self.speeds = itertools.cycle(speeds)

    def power(self) -> Power:
        return Power(self.on_ac, 80, self.watts if self.on_ac else None)

    def speed(self, model, num_ctx) -> float:
        return next(self.speeds)


def _guard(tmp_path, machine, **kw) -> Guard:
    kw.setdefault("sleep", lambda s: None)
    return Guard(tmp_path / "guard", power=machine.power, idle=lambda: machine.idle,
                 speed=machine.speed, pulled=lambda: {"m:latest": "sha256:0123456789abcdef"},
                 sample_every=0.01, **kw)


def test_gate_names_what_is_wrong(tmp_path):
    m = FakeMachine(speeds=(30.0, 10.0))
    g, agent = _guard(tmp_path, m, wait_idle=300), FakeAgent("m")
    m.on_ac = False
    assert g.problems(agent) == ["on battery (80%)"]
    m.on_ac, m.watts = True, 30
    assert "30W charger" in g.problems(agent)[0]
    m.watts, m.idle = 65, 10
    assert g.problems(agent)[0].startswith("in use")
    m.idle = 3600
    assert g.problems(agent) == []                      # 30 tok/s sets the reference
    assert g.problems(agent)[0].startswith("slow: 10.0 tok/s")


def test_reference_is_the_best_speed_seen_and_persists(tmp_path):
    m = FakeMachine(speeds=(20.0, 30.0, 25.0))
    g = _guard(tmp_path, m)
    for _ in range(3):
        g.measure(FakeAgent("m"))
    assert _guard(tmp_path, m).reference("m") == 30.0   # keyed by digest, on disk


def test_readings_taken_in_use_never_set_the_bar(tmp_path):
    m = FakeMachine(speeds=(11.0, 30.0, 10.0))
    g, agent = _guard(tmp_path, m), FakeAgent("m")
    m.idle = 20                                         # someone is working
    assert g.problems(agent) == [] and g.reference("m") is None   # 11 recorded, not judged
    m.idle = 3600
    assert g.problems(agent) == [] and g.reference("m") == 30.0   # the idle reading sets it
    assert g.problems(agent)[0].startswith("slow: 10.0 tok/s")


def test_wait_ready_pauses_until_the_machine_recovers(tmp_path):
    m, logs = FakeMachine(), []
    m.on_ac = False
    g = _guard(tmp_path, m, sleep=lambda s: setattr(m, "on_ac", True), log=logs.append)
    assert g.wait_ready(FakeAgent("m")) is True
    assert logs == ["paused: on battery (80%)", "resumed"]


def test_an_erroring_ollama_pauses_instead_of_crashing(tmp_path):
    import httpx
    m = FakeMachine()

    def boom(model, num_ctx):
        raise httpx.ConnectError("refused")

    g = _guard(tmp_path, m)
    g._speed = boom
    assert g.problems(FakeAgent("m")) == ["ollama error: refused"]


def test_wait_ready_gives_up_at_the_deadline(tmp_path):
    m = FakeMachine()
    m.on_ac = False
    assert _guard(tmp_path, m).wait_ready(FakeAgent("m"), deadline=time.time() - 1) is False


def test_losing_power_mid_attempt_degrades_it(mined, config, repo, tmp_path):
    m = FakeMachine()

    def unplug(clone, sha):
        m.on_ac = False
        apply_gold(clone, sha)

    a = run_attempt(mined, config, _task(mined, repo), FakeAgent("m", unplug), "blind",
                    guard=_guard(tmp_path, m))
    assert a.resolved and a.degraded == ["lost AC power during the attempt"]
    assert load_attempts(mined) == [] and len(load_degraded(mined)) == 1


def test_a_degraded_attempt_is_kept_apart_and_the_cell_retried(mined, repo, tmp_path):
    # gate 30 (reference) -> attempt -> after 10 (slow): degraded; then healthy.
    m = FakeMachine(speeds=(30.0, 10.0, 30.0, 30.0))
    run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"], guard=_guard(tmp_path, m))
    counted, degraded = load_attempts(mined), load_degraded(mined)
    assert [a.resolved for a in counted] == [True] and not counted[0].degraded
    assert counted[0].conditions["tok_s_after"] == 30.0
    assert degraded[0].degraded[0].startswith("slow: 10.0 tok/s")
    out = render(mined.load_tasks(), counted, degraded)
    assert "1 degraded attempts excluded (machine unhealthy): slow x1" in out
    assert "1/1" in out                                 # the degraded one isn't in the count


def test_degraded_retries_are_capped_and_the_cell_left_open(mined, repo, tmp_path):
    m = FakeMachine(speeds=(30.0, 10.0))                # every attempt ends slow
    done = run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"],
                      guard=_guard(tmp_path, m), max_degraded=1)
    assert done == [] and load_attempts(mined) == [] and len(load_degraded(mined)) == 2


def test_no_attempt_starts_after_the_deadline(mined, repo):
    agent = FakeAgent("m", apply_gold)
    assert run_matrix(mined, [agent], ["blind"], deadline=time.time() - 1) == []
    assert agent.calls == []


def test_preflight(tmp_path):
    m = FakeMachine()
    m.on_ac = False
    checks = {c.name: c for c in _guard(tmp_path, m).preflight(
        [FakeAgent("m"), FakeAgent("absent")])}
    assert not checks["power"].ok and "on battery" in checks["power"].detail
    assert not checks["fake"].ok                        # agent binary not on PATH
    assert checks["m"].ok and "30.0 tok/s" in checks["m"].detail
    assert not checks["absent"].ok and "ollama pull absent" in checks["absent"].detail


def test_parse_until_is_the_next_occurrence():
    late, early = datetime(2026, 9, 29, 23, 0), datetime(2026, 9, 29, 6, 0)
    assert datetime.fromtimestamp(parse_until("07:30", late)) == datetime(2026, 9, 30, 7, 30)
    assert datetime.fromtimestamp(parse_until("07:30", early)) == datetime(2026, 9, 29, 7, 30)


# ── opencode ──────────────────────────────────────────────────────────────────

# Shaped like a real `opencode run --format json` transcript (1.18): read,
# read, edit, then a final answer, three model calls.
_OPENCODE_EVENTS = "\n".join(json.dumps(e) for e in [
    {"type": "step_start", "part": {"type": "step-start"}},
    {"type": "tool_use", "part": {"type": "tool", "tool": "read", "state": {"status": "completed"}}},
    {"type": "tool_use", "part": {"type": "tool", "tool": "bash", "state": {"status": "error"}}},
    {"type": "step_finish", "part": {"reason": "tool-calls", "tokens": {
        "total": 7391, "input": 7175, "output": 216, "reasoning": 0, "cache": {"write": 0, "read": 0}}}},
    {"type": "tool_use", "part": {"type": "tool", "tool": "edit", "state": {"status": "completed"}}},
    {"type": "step_finish", "part": {"reason": "tool-calls", "tokens": {
        "input": 448, "output": 190, "reasoning": 0, "cache": {"read": 7171}}}},
    {"type": "text", "part": {"type": "text", "text": "Done"}},
    {"type": "step_finish", "part": {"reason": "stop", "tokens": {
        "input": 163, "output": 62, "reasoning": 5, "cache": {"read": 7615}}}},
]) + "\nnot json\n"


def test_opencode_events_are_counted():
    run = parse_opencode_events(_OPENCODE_EVENTS)
    assert run.llm_calls == 3
    assert run.tokens_sent == 7175 + 448 + 7171 + 163 + 7615   # cache reads were sent too
    assert run.tokens_recv == 216 + 190 + 62 + 5
    assert run.extra == {"tool_calls": 3, "tool_errors": 1, "bash_calls": 1}


def test_opencode_is_isolated_from_the_users_setup(tmp_path):
    agent = OpenCodeAgent("qwen3.5:latest", home=tmp_path / "oc")
    env = agent._env("agentbench/qwen3.5:latest-num_ctx32768-num_predict8192")
    assert env["HOME"] == str(tmp_path / "oc")               # no user credentials
    assert all(env[k].startswith(str(tmp_path)) for k in env if k.startswith("XDG_"))
    cfg = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert cfg["enabled_providers"] == ["ollama"]
    assert cfg["provider"]["ollama"]["options"]["baseURL"].startswith("http://127.0.0.1")
    assert cfg["permission"]["webfetch"] == "deny"
    assert cfg["permission"]["external_directory"] == "deny"
    assert cfg["share"] == "disabled" and cfg["autoupdate"] is False
    assert cfg["model"] == cfg["small_model"]               # titles don't go elsewhere


def test_opencode_arm():
    agent = parse_arm("opencode:granite4.1:8b")
    assert isinstance(agent, OpenCodeAgent) and agent.model == "granite4.1:8b"
    assert agent.num_ctx == AiderAgent("x").num_ctx          # same generation budget
    assert agent.num_predict == AiderAgent("x").num_predict
    with pytest.raises(ValueError, match="cloud"):
        parse_arm("opencode:qwen3-coder:480b-cloud")


def test_an_agent_timeout_kills_its_children(tmp_path):
    from backend.orchestrator.agentbench.agents import _launch
    pidfile = tmp_path / "child.pid"
    script = f"sleep 60 & echo $! > {pidfile}; wait"
    out, timed_out, secs = _launch(["sh", "-c", script], tmp_path, {"PATH": "/bin:/usr/bin"}, 1)
    assert timed_out and secs < 10
    child = int(pidfile.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        import os
        os.kill(child, 0)


_macos_sandbox = pytest.mark.skipif(
    sys.platform != "darwin" or not __import__("shutil").which("sandbox-exec"),
    reason="sandbox-exec is macOS only")


def test_the_agent_sees_its_checkout_as_pwd(tmp_path):
    from backend.orchestrator.agentbench.agents import _launch
    out, _, _ = _launch(["sh", "-c", "echo $PWD"], tmp_path,
                        {"PATH": "/bin:/usr/bin", "PWD": "/somewhere/else"}, 30)
    assert out.strip() == str(tmp_path)


@_macos_sandbox
def test_the_sandbox_confines_writes_to_the_checkout(tmp_path):
    from backend.orchestrator.agentbench.agents import _launch
    # The temp area is writable by design, so stand in for the user's repo
    # with this source tree, which is not under it.
    checkout, repo = tmp_path / "checkout", Path(__file__).parent
    checkout.mkdir()
    script = (f"echo ok > {checkout}/edit.py; echo x > {repo}/pwned.py; "
              f"echo x > {Path.home()}/.agentbench-sandbox-probe")
    _launch(["sh", "-c", script], checkout, {"PATH": "/bin:/usr/bin"}, 30)
    assert (checkout / "edit.py").read_text() == "ok\n"
    assert not (repo / "pwned.py").exists()
    assert not (Path.home() / ".agentbench-sandbox-probe").exists()


@_macos_sandbox
def test_the_sandbox_allows_localhost_only(tmp_path):
    from backend.orchestrator.agentbench.agents import _launch
    probe = ("import socket\n"
             "def dial(host, port):\n"
             "    s = socket.socket(); s.settimeout(3)\n"
             "    try: s.connect((host, port)); return 'open'\n"
             "    except PermissionError: return 'blocked'\n"
             "    except OSError: return 'refused'\n"
             "print(dial('127.0.0.1', 1), dial('1.1.1.1', 443))\n")
    out, _, _ = _launch([sys.executable, "-c", probe], tmp_path, {"PATH": "/bin:/usr/bin"}, 30)
    local, external = out.split()
    assert local == "refused"      # reached the loopback stack (nothing listens on :1)
    assert external == "blocked"   # denied before it left the machine
