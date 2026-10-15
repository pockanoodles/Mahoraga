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
    AgentRun, AiderAgent, OpenCodeAgent, _launch, aider_crash, aider_request_errors,
    aider_tool_calls, parse_arm,
    parse_opencode_events,
)
from backend.orchestrator.agentbench.bank import Bench, SuiteConfig
from backend.orchestrator.agentbench.mine import (
    find_candidates, is_test_module, is_test_path, layout_pytest_args, leaks, mine, reseal,
)
from backend.orchestrator.agentbench.grade import TEST_VERIFIED, Grade
from backend.orchestrator.agentbench.guard import Guard
from backend.orchestrator.agentbench.stats import plan, task_rate, tasks_needed, wilson
from backend.orchestrator.agentbench.host import Power, parse_idle, parse_power
from backend.orchestrator.agentbench.report import outcome, render, summarise
from backend.orchestrator.agentbench.runner import (
    arm_name, load_attempts, load_degraded, prompt_for, run_attempt, run_matrix,
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
    FakeAgent.source = r
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
    """Edits from what the test knows. The answer comes from the source repo,
    since a sealed task clone doesn't have it."""
    name = "fake"
    source: Path | None = None

    def __init__(self, model: str, edit=lambda clone, task_sha: None, crashed: str = "",
                 timed_out: bool = False, request_errors: int = 0, unreadable: str = ""):
        self.model, self._edit, self._crashed = model, edit, crashed
        self._timed_out, self._request_errors = timed_out, request_errors
        self._unreadable = unreadable
        self.calls: list[dict] = []

    def attempt(self, workdir, prompt, *, test_cmd, timeout, hidden=(), readable=()):
        self.calls.append({"prompt": prompt, "test_cmd": test_cmd, "hidden": hidden})
        sha = _git(FakeAgent.source, "log", "--format=%h", "-1", "--grep=feat: add mul")
        self._edit(workdir, sha)
        return AgentRun(log="", secs=1.0, timed_out=self._timed_out, crashed=self._crashed,
                        request_errors=self._request_errors, unreadable=self._unreadable)


def apply_gold(clone: Path, sha: str) -> None:
    diff = _git(FakeAgent.source, "diff", f"{sha}^", sha, "--", "calc/ops.py")
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


def test_a_task_clone_holds_no_trace_of_the_answer(mined, repo):
    sha = repo["shas"]["mul"]
    clone = mined.clone(sha)
    assert leaks(clone, sha) == []
    # The history an agent can read ends at the commit's parent.
    log = _git(clone, "log", "--all", "--format=%s")
    assert log.splitlines() == ["agentbench task base", "init"]
    assert "def mul" not in (clone / "calc/ops.py").read_text()
    assert (clone / "tests/test_mul.py").exists()


def _unsealed(bench, repo, sha):
    """A clone the way `mine` built them before 2026-10-13: shared objects, and
    the base committed on top of the answer."""
    d = bench.clone(sha)
    import shutil
    shutil.rmtree(d)
    _git(d.parent, "clone", "-q", "--shared", str(repo["path"]), str(d))
    _git(d, "checkout", "-q", sha)
    _git(d, "checkout", "-q", f"{sha}^", "--", "calc/ops.py")
    _git(d, "-c", "user.name=a", "-c", "user.email=a@a", "commit", "-q", "-m", "agentbench task base")
    return _git(d, "rev-parse", "HEAD")


def test_reseal_seals_old_clones_and_keeps_their_attempts(mined, repo):
    sha = repo["shas"]["mul"]
    [task] = mined.load_tasks()
    task.base = _unsealed(mined, repo, sha)
    mined.save_tasks([task])
    run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"])
    assert leaks(mined.clone(sha), sha) != []
    tree = _git(mined.clone(sha), "rev-parse", f"{task.base}^{{tree}}")

    assert reseal(mined) == 1
    [after] = mined.load_tasks()
    assert after.base != task.base and leaks(mined.clone(sha), sha) == []
    assert _git(mined.clone(sha), "rev-parse", f"{after.base}^{{tree}}") == tree
    assert len(load_attempts(mined)) == 1  # mine would have deleted it
    assert reseal(mined) == 0  # already sealed
    # And the sealed clone still grades: the gold fix resolves.
    a = run_attempt(mined, mined.load_config(), after, FakeAgent("g", apply_gold), "blind")
    assert a.resolved


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


def test_doctor_estimate_borrows_minutes_from_the_same_arm_on_other_repos(mined):
    from backend.orchestrator.agentbench.doctor import ASSUMED, minutes_per_attempt

    agent = FakeAgent("m", apply_gold)
    run_matrix(mined, [agent], ["blind"])
    elsewhere = Bench.from_dir(mined.dir.parent / "other-00000000")
    minutes, source = minutes_per_attempt(elsewhere, {"fake:m"})
    assert minutes == pytest.approx(1 / 60) and source == "measured on 1 attempt in other repos"
    # Another arm's minutes say nothing about this one (a 32k run about a 131k one).
    assert minutes_per_attempt(elsewhere, {"fake:m@ctx=131072"}) == (12.0, ASSUMED)


def test_doctor_estimate_counts_nights_after_the_work_queued_ahead(mined):
    from backend.orchestrator.agentbench.doctor import estimate

    est = estimate(mined, [FakeAgent("m")], ["blind"], night_hours=8.0,
                   ahead=40, ahead_minutes=40 * 12.0)
    assert est.pending == 1 and est.nights == 2  # 8 h ahead fills the first night
    assert "behind 40 attempts already queued (~8.0 h) ≈ 2 night(s)" in est.fmt()


# ── Agents ────────────────────────────────────────────────────────────────────


def test_an_arm_names_its_context_and_reads_back():
    big = parse_arm("aider:gpt-oss:20b@ctx=131072")
    assert big.model == "gpt-oss:20b" and big.num_ctx == 131072
    assert "num_ctx: 131072" in big._settings()
    assert arm_name(big) == "aider:gpt-oss:20b@ctx=131072"
    assert parse_arm(arm_name(big)).num_ctx == 131072
    # The default is unnamed, so attempts recorded before contexts keep their cells.
    assert arm_name(parse_arm("aider:gpt-oss:20b")) == "aider:gpt-oss:20b"
    assert parse_arm("opencode:qwen3.5:latest@ctx=65536").num_ctx == 65536
    for bad in ("aider:m@ctx", "aider:m@ctx=", "aider:m@ctx=big", "aider:m@ctx=512",
                "aider:m@num_ctx=65536"):
        with pytest.raises(ValueError, match="ctx="):
            parse_arm(bad)


def test_two_contexts_of_one_model_are_two_cells(mined, repo):
    small, big = FakeAgent("m", apply_gold), FakeAgent("m", apply_gold)
    big.num_ctx = 131072
    run_matrix(mined, [small, big], ["blind"])
    assert sorted(a.arm for a in load_attempts(mined)) == ["fake:m", "fake:m@ctx=131072"]


def test_speed_is_kept_per_context(tmp_path):
    m = FakeMachine(speeds=(50.0, 20.0))
    g, small, big = _guard(tmp_path, m), FakeAgent("m"), FakeAgent("m")
    big.num_ctx = 131072
    g.measure(small)
    g.measure(big)
    assert g.reference("m") == 50.0 and g.reference("m", 131072) == 20.0


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


def test_aider_crash_is_read_from_its_crash_report():
    # Trimmed from the Studio log: scipy 1.15.3 would not load under macOS 27.
    log = ("Repo-map: using 2048 tokens, auto refresh\n\n"
           "# Uncaught ImportError in _svdp.py line 23\n\nAider version: 0.86.2\n\n"
           "An uncaught exception occurred:\n```\nTraceback ...\n```\n"
           "Open a GitHub Issue pre-filled with the above error in your browser? (Y/n)")
    assert aider_crash(log) == "ImportError in _svdp.py line 23"
    assert aider_crash("An uncaught exception occurred:\n```\n...") == "uncaught exception"
    assert aider_crash("Applied edit to m.py\nTokens: 2.6k sent, 206 received.") == ""


def test_aider_tool_calls_are_counted_from_the_chat():
    # Trimmed from ornith-1.5:9b on the Studio, 2026-10-13.
    log = ("► **ANSWER**\n\nI'll start by exploring the agentbench module.\n\n"
           "<tool_call>\n<function bash>\n<parameter name=\"command\">ls backend/\n"
           "</parameter>\n</bash>\n\nTokens: 15k sent, 305 received.\n")
    assert aider_tool_calls(log) == 1
    assert aider_tool_calls("Applied edit to m.py\nTokens: 2.6k sent, 206 received.") == 0


def test_aider_reads_cap_hits_and_tool_calls_from_its_log(tmp_path, monkeypatch):
    log = ("Tokens: 7.2k sent, 8.2k received.\n<tool_call>\n"
           "Tokens: 9.1k sent, 1.6k received.\n")
    monkeypatch.setattr("backend.orchestrator.agentbench.agents._launch",
                        lambda *a, **k: (log, False, 12.0))
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    run = AiderAgent("m:9b", home=tmp_path).attempt(tmp_path, "p", test_cmd=None, timeout=1)
    assert run.extra["capped_calls"] == 1
    assert run.unreadable == "1 tool calls, which aider can't run"


def test_aider_request_errors_count_one_per_failed_request():
    # From the throttled 2026-09-29 run: one failure names two litellm errors.
    log = ("tests/test_slack_client.py\n"
           "litellm.APIConnectionError: Ollama_chatException - litellm.Timeout: Connection \n"
           "timed out after 600.0 seconds.\nRetrying in 0.2 seconds...\n"
           "litellm.InternalServerError: Ollama_chatException - model runner stopped\n")
    assert aider_request_errors(log) == 2
    assert aider_request_errors("Applied edit to m.py\nTokens: 2.6k sent, 206 received.") == 0


def test_an_agent_that_asks_a_question_gets_eof_not_a_hang(tmp_path):
    ask = [sys.executable, "-c", "import sys; print('answer=' + repr(sys.stdin.readline()))"]
    out, timed_out, _ = _launch(ask, tmp_path, {"PATH": "/usr/bin:/bin"}, timeout=10)
    assert not timed_out and "answer=''" in out


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


def test_reference_is_the_median_idle_speed_and_persists(tmp_path):
    m = FakeMachine(speeds=(20.0, 30.0, 25.0))
    g = _guard(tmp_path, m)
    for _ in range(3):
        g.measure(FakeAgent("m"))
    assert _guard(tmp_path, m).reference("m") == 25.0   # keyed by digest, on disk


def test_one_fast_outlier_does_not_set_the_bar(tmp_path):
    # gpt-oss:20b on the Studio: one 115.6 among readings near 50. The best
    # would put every typical reading under half of it.
    m = FakeMachine(speeds=(50.0, 52.0, 115.6, 48.0, 51.0))
    g, agent = _guard(tmp_path, m), FakeAgent("m")
    for _ in range(5):
        g.measure(agent)
    assert g.reference("m") == 51.0
    m.speeds = itertools.cycle([47.0])
    assert g.problems(agent) == []


def test_a_reading_is_never_judged_against_itself(tmp_path):
    m = FakeMachine(speeds=(30.0, 10.0))
    g, agent = _guard(tmp_path, m), FakeAgent("m")
    assert g.measure(agent) == (30.0, None)             # first: nothing to judge against
    assert g.measure(agent) == (10.0, 30.0)             # judged before it joins the median


def test_only_the_last_readings_are_kept(tmp_path):
    from backend.orchestrator.agentbench.guard import READINGS_KEPT
    m = FakeMachine(speeds=tuple([100.0] * 3 + [40.0] * READINGS_KEPT))
    g = _guard(tmp_path, m)
    for _ in range(3 + READINGS_KEPT):
        g.measure(FakeAgent("m"))
    assert g.reference("m") == 40.0                     # the old fast ones aged out


def test_a_speed_file_from_before_medians_still_loads(tmp_path):
    g = _guard(tmp_path, FakeMachine())
    g.path.parent.mkdir(parents=True)
    g.path.write_text(json.dumps({"m:latest@sha256:01234": 115.6}))
    assert g.reference("m") == 115.6


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
    # gate 30 (reference) -> attempt with failed requests -> after 10 (slow):
    # degraded; then healthy.
    m = FakeMachine(speeds=(30.0, 10.0, 30.0, 30.0))
    run_matrix(mined, [FakeAgent("m", apply_gold, request_errors=2)], ["blind"],
               guard=_guard(tmp_path, m))
    counted, degraded = load_attempts(mined), load_degraded(mined)
    assert [a.resolved for a in counted] == [True] and not counted[0].degraded
    assert counted[0].conditions["tok_s_after"] == 30.0
    assert degraded[0].degraded == [
        "slow: 10.0 tok/s, under 50% of 30.0 after the attempt, and the attempt had 2 failed requests"]
    out = render(mined.load_tasks(), counted, degraded)
    assert "1 degraded attempts excluded (did not measure the agent): slow x1" in out
    assert "1/1" in out                                 # the degraded one isn't in the count


def test_slow_and_timed_out_is_degraded(mined, repo, tmp_path):
    m = FakeMachine(speeds=(30.0, 10.0))
    run_matrix(mined, [FakeAgent("m", timed_out=True)], ["blind"],
               guard=_guard(tmp_path, m), max_degraded=0)
    assert load_degraded(mined)[0].degraded[0].endswith("and the attempt timed out")


def test_slow_but_clean_attempt_counts(mined, repo, tmp_path):
    # The Studio case: resolved in minutes, no timeout, no failed requests, and
    # a slow reading after. Slowness didn't reach this outcome, so it counts.
    m = FakeMachine(speeds=(30.0, 10.0))
    done = run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"], guard=_guard(tmp_path, m))
    assert [a.resolved for a in done] == [True] and load_degraded(mined) == []
    assert done[0].conditions["slow_after"].startswith("slow: 10.0 tok/s")


def test_degraded_retries_are_capped_and_the_cell_left_open(mined, repo, tmp_path):
    m = FakeMachine(speeds=(30.0, 10.0))                # every attempt ends slow
    done = run_matrix(mined, [FakeAgent("m", apply_gold, request_errors=1)], ["blind"],
                      guard=_guard(tmp_path, m), max_degraded=1)
    assert done == [] and load_attempts(mined) == [] and len(load_degraded(mined)) == 2


def test_a_crashed_agent_is_degraded_not_scored_as_no_edit(mined, repo):
    # No guard: a crash is the agent's harness failing, whatever the machine.
    crash = FakeAgent("m", crashed="ImportError in _svdp.py line 23")
    done = run_matrix(mined, [crash], ["blind"], max_degraded=1)
    degraded = load_degraded(mined)
    assert done == [] and load_attempts(mined) == [] and len(degraded) == 2
    assert degraded[0].degraded == ["agent crashed: ImportError in _svdp.py line 23"]
    out = render(mined.load_tasks(), [], degraded)
    assert "2 degraded attempts excluded (did not measure the agent): agent crashed x2" in out


def test_an_unreadable_reply_with_no_edit_is_degraded(mined, repo):
    agent = FakeAgent("m", unreadable="3 tool calls, which aider can't run")
    done = run_matrix(mined, [agent], ["blind"], max_degraded=0)
    degraded = load_degraded(mined)
    assert done == [] and load_attempts(mined) == [] and len(degraded) == 1
    assert degraded[0].degraded == [
        "agent couldn't read the reply: 3 tool calls, which aider can't run"]


def test_an_unreadable_reply_that_still_edited_counts(mined, repo):
    # Some replies were tool calls, but the model edited anyway: that measured it.
    agent = FakeAgent("m", apply_gold, unreadable="1 tool calls, which aider can't run")
    done = run_matrix(mined, [agent], ["blind"], only={repo["shas"]["mul"]})
    assert len(done) == 1 and done[0].resolved and load_degraded(mined) == []


def test_an_arm_the_agent_keeps_failing_is_dropped(mined, repo):
    dropped = []
    broken = FakeAgent("broken", crashed="ImportError in _svdp.py line 23")
    fine = FakeAgent("fine", apply_gold)
    done = run_matrix(mined, [broken, fine], ["blind", "feedback"], max_degraded=0,
                      max_harness_failures=1,
                      dropped=lambda arm, why: dropped.append((arm, why)))
    # The blind cell failed, so the arm never reached feedback; the next arm ran in full.
    assert len(broken.calls) == 1 and len(fine.calls) == 2 and len(done) == 2
    assert dropped == [("fake:broken", "1 tasks in a row where the agent failed, not the "
                        "model (last: agent crashed: ImportError in _svdp.py line 23)")]


def test_a_counted_attempt_resets_the_harness_failure_streak(mined, repo):
    flaky = FakeAgent("m", apply_gold)
    flaky._crashed = "uncaught exception"
    dropped = []
    # crash, count, crash, count: two crashes, never two in a row.
    calls = iter(["uncaught exception", ""] * 2)
    orig = flaky.attempt

    def attempt(*a, **k):
        flaky._crashed = next(calls)
        return orig(*a, **k)

    flaky.attempt = attempt
    run_matrix(mined, [flaky], ["blind", "feedback"], repeats=2, max_degraded=0,
               max_harness_failures=2, dropped=lambda arm, why: dropped.append(why))
    assert dropped == [] and len(load_attempts(mined)) == 2


def test_the_report_says_how_many_failures_ran_out_of_budget(mined, repo):
    run_matrix(mined, [FakeAgent("m")], ["blind"])
    [a] = load_attempts(mined)
    a.extra["capped_calls"] = 2
    assert summarise(mined.load_tasks(), [a])[0].capped == 1
    assert "(1 of the failures hit the generation cap)" in render(mined.load_tasks(), [a])


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


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS-only")
def test_the_agent_cannot_read_the_source_repo(tmp_path):
    from backend.orchestrator.agentbench.agents import _launch
    src, venv, work = tmp_path / "src", tmp_path / "src" / ".venv", tmp_path / "work"
    for d in (venv, work):
        d.mkdir(parents=True)
    (src / "answer.py").write_text("SOLUTION\n")
    (venv / "tool.txt").write_text("tool\n")
    script = f"cat {src}/answer.py; ls {src}; cat {venv}/tool.txt; echo mine > own.txt; cat own.txt"
    out, _, _ = _launch(["sh", "-c", script], work, {"PATH": "/bin:/usr/bin"}, 30,
                        hidden=[src], readable=[venv])
    assert "SOLUTION" not in out and out.count("Operation not permitted") == 2  # cat, ls
    assert "tool" in out and "mine" in out


def test_confinement_hides_the_repo_and_bench_but_not_the_test_venv(mined, repo, tmp_path):
    from backend.orchestrator.agentbench.runner import confinement
    inside = SuiteConfig(python=str(repo["path"] / ".venv" / "bin" / "python"))
    hidden, readable = confinement(mined, inside)
    assert hidden == [repo["path"].resolve(), mined.dir.parent.resolve()]
    assert readable == [(repo["path"] / ".venv").resolve()]
    assert confinement(mined, SuiteConfig(python=sys.executable))[1] == []


# ── The queue ─────────────────────────────────────────────────────────────────

from backend.orchestrator.agentbench.queue import (  # noqa: E402
    DONE, FAILED, PENDING, Queue, drain, parse_window, pin_problems, window_deadline,
)
from backend.orchestrator.agentbench.worker import run_job  # noqa: E402

PIN = {"m": "sha256:0123456789abcdef"}


def _at(hhmm: str) -> float:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, 13, h, m).timestamp()


def test_a_window_through_midnight_opens_and_closes_on_time():
    assert window_deadline("21:00-08:00", _at("20:59")) is None
    assert window_deadline("21:00-08:00", _at("21:00")) == datetime(2026, 10, 14, 8, 0).timestamp()
    assert window_deadline("21:00-08:00", _at("03:00")) == datetime(2026, 10, 13, 8, 0).timestamp()
    assert window_deadline("21:00-08:00", _at("08:00")) is None
    assert window_deadline("09:00-17:00", _at("12:00")) == datetime(2026, 10, 13, 17, 0).timestamp()
    assert window_deadline(None, _at("12:00")) == float("inf")
    with pytest.raises(ValueError):
        parse_window("9pm-8am")


def test_a_moved_tag_fails_the_pin():
    assert pin_problems(PIN, {"m:latest": PIN["m"]}) == []
    assert pin_problems(PIN, {}) == ["m is not pulled"]
    assert pin_problems(PIN, {"m:latest": "sha256:ffffffffffffffff"}) == [
        "m changed since the job was added (pinned 0123456789ab, now ffffffffffff)"]


def _job_runner(tmp_path, machine, agent, queue, sleep=lambda s: None):
    def run(job, deadline):
        return run_job(queue, job, deadline, pulled=lambda: {"m:latest": PIN["m"]},
                       guard=lambda root, **kw: _guard(tmp_path, machine, sleep=sleep, **kw),
                       agents_for=lambda arms: [agent], which=lambda name: "/bin/" + name,
                       isolated=lambda bench, config: [], log=lambda msg: None)
    return run


def test_the_worker_runs_a_job_to_done(mined, repo, tmp_path):
    q = Queue(mined.dir.parent)
    job = q.add(str(mined.repo), ["fake:m"], "both", pinned=PIN)
    agent = FakeAgent("m", apply_gold)
    drain(q, _job_runner(tmp_path, FakeMachine(), agent, q), once=True, log=lambda m: None)
    [done] = q.jobs()
    assert done.status == DONE and done.why == "all 2 cells counted" and len(done.runs) == 1
    assert len(load_attempts(mined)) == 2


def test_a_job_whose_model_moved_fails_and_says_so(mined, tmp_path):
    q = Queue(mined.dir.parent)
    q.add(str(mined.repo), ["fake:m"], "blind", pinned={"m": "sha256:old"})
    agent = FakeAgent("m", apply_gold)
    drain(q, _job_runner(tmp_path, FakeMachine(), agent, q), once=True, log=lambda m: None)
    [job] = q.jobs()
    assert job.status == FAILED and "changed since the job was added" in job.why
    assert agent.calls == []


def test_a_pass_that_counts_nothing_ends_the_job(mined, tmp_path):
    q = Queue(mined.dir.parent)
    q.add(str(mined.repo), ["fake:m"], "blind", pinned=PIN)
    crash = FakeAgent("m", crashed="uncaught exception")
    drain(q, _job_runner(tmp_path, FakeMachine(), crash, q), once=True, log=lambda m: None)
    [job] = q.jobs()
    # One task, three tries, all degraded; the worker doesn't retry it all night.
    assert job.status == DONE and job.why == "1 of 1 cells left open, every try degraded"
    assert len(crash.calls) == 3


def test_a_job_out_of_window_waits_and_says_nothing_ran(mined, tmp_path):
    q = Queue(mined.dir.parent)
    q.add(str(mined.repo), ["fake:m"], "blind", pinned=PIN, window="21:00-08:00")
    assert q.next_open(_at("12:00")) is None
    job, deadline = q.next_open(_at("22:00"))
    assert deadline == datetime(2026, 10, 14, 8, 0).timestamp()


def test_a_job_that_waited_all_window_says_what_for(mined, tmp_path):
    q = Queue(mined.dir.parent)
    job = q.add(str(mined.repo), ["fake:m"], "blind", pinned=PIN)
    m = FakeMachine()
    m.on_ac = False
    agent = FakeAgent("m", apply_gold)
    # On battery from the start; the window closes during its first wait.
    deadline = time.time() + 3600
    clock = [time.time()]
    real = time.time
    time.time = lambda: clock[0]
    try:
        _job_runner(tmp_path, m, agent, q,
                    sleep=lambda s: clock.__setitem__(0, deadline + 1))(job, deadline)
    finally:
        time.time = real
    [after] = q.jobs()
    assert after.status == PENDING and agent.calls == []
    assert "last wait: on battery (80%)" in after.why


def test_a_shared_ollama_holding_someone_elses_model_pauses_the_run(tmp_path):
    loaded = ["gpt-oss:20b", "m:latest"]
    g = _guard(tmp_path, FakeMachine(), wait_quiet=True, loaded=lambda: loaded)
    assert g.problems(FakeAgent("m")) == ["busy: gpt-oss:20b loaded"]
    loaded[:] = ["m:latest", "agentbench/m:latest-num_ctx32768-num_predict8192"]
    assert g.problems(FakeAgent("m")) == []  # its own model, and its own derived tag
    assert _guard(tmp_path, FakeMachine(), loaded=lambda: ["gpt-oss:20b"]).problems(
        FakeAgent("m")) == []  # off unless asked


# ── Isolation canary ──────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS-only")
def test_the_canary_passes_on_a_sealed_sandboxed_bench(mined, config):
    from backend.orchestrator.agentbench import isolation
    checks = isolation.check(mined, config)
    assert [c.name for c in checks] == ["isolation: history", "isolation: source",
                                        "isolation: bench", "isolation: log"]
    assert all(c.ok for c in checks), checks


def test_the_canary_fails_when_isolation_is_broken(mined, repo, config):
    from backend.orchestrator.agentbench import isolation
    from backend.orchestrator.agentbench.agents import _launch

    def unsandboxed(cmd, cwd, env, timeout, *writable, hidden=(), readable=()):
        return _launch(cmd, cwd, env, timeout)  # hides nothing

    [task] = mined.load_tasks()
    task.base = _unsealed(mined, repo, repo["shas"]["mul"])
    mined.save_tasks([task])
    failed = {c.name for c in isolation.check(mined, config, launch=unsandboxed) if not c.ok}
    assert failed == {"isolation: history", "isolation: source", "isolation: bench",
                      "isolation: log"}


def test_the_worker_refuses_a_bench_that_isnt_isolated(mined, tmp_path):
    q = Queue(mined.dir.parent)
    job = q.add(str(mined.repo), ["fake:m"], "blind", pinned=PIN)
    agent = FakeAgent("m", apply_gold)
    run_job(q, job, None, pulled=lambda: {"m:latest": PIN["m"]},
            guard=lambda root, **kw: _guard(tmp_path, FakeMachine(), **kw),
            agents_for=lambda arms: [agent], which=lambda n: "/bin/" + n,
            isolated=lambda bench, config: ["isolation: source: readable"], log=lambda m: None)
    [after] = q.jobs()
    assert after.status == FAILED and after.why == "not isolated: isolation: source: readable"
    assert agent.calls == []


# ── Receipts ──────────────────────────────────────────────────────────────────


def _manifest(run_id, digest="sha256:aaaabbbbcccc1111", dropped=None):
    return {"run_id": run_id, "models": {"ollama": "0.35.1", "digests": {"m": digest}},
            "agents": {"fake": "fake 1.0"}, "harness": {"version": "2.1.0", "commit": "c1bf98130ab",
                                                      "dirty": False},
            "machine": {"model": "Mac17,14", "memory_gb": 64},
            "tasks": {"prompts_sha256": "feedfacecafe0000"}, "dropped": dropped or {}}


def test_a_ledger_carries_receipts_exclusions_and_drops(mined, repo):
    from backend.orchestrator.agentbench.report import ledgers, render_ledgers

    run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"], run_id="r1")
    run_matrix(mined, [FakeAgent("m", crashed="uncaught exception")], ["feedback"],
               max_degraded=0, run_id="r2")
    ms = [_manifest("r1"), _manifest("r2", dropped={"fake:m": "3 tasks in a row ..."})]
    [blind, feedback] = ledgers(load_attempts(mined), load_degraded(mined), ms)
    assert blind.counted == 1 and blind.excluded == {} and blind.warnings == []
    assert blind.receipts == ["1 attempts: m @ aaaabbbbcccc, fake 1.0, ollama 0.35.1, "
                              "mahoraga 2.1.0 c1bf98130, Mac17,14 64 GB, prompts feedfacecafe"]
    assert feedback.counted == 0 and feedback.excluded == {"agent crashed": 1}
    assert feedback.dropped == ["3 tasks in a row ..."]
    out = "\n".join(render_ledgers([blind, feedback]))
    assert "excluded, did not measure the model: agent crashed x1" in out


def test_a_ledger_warns_when_the_weights_changed_between_runs(mined, repo):
    from backend.orchestrator.agentbench.report import ledgers

    run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"], run_id="r1")
    run_matrix(mined, [FakeAgent("m", apply_gold)], ["feedback"], run_id="r2")
    ms = [_manifest("r1"), _manifest("r2", digest="sha256:ffff00000000ffff")]
    attempts = load_attempts(mined)
    for a in attempts:
        a.cond = "blind"
    [g] = ledgers(attempts, [], ms)
    assert g.warnings == ["the model's weights changed between runs (2 digests)"]


# ── Following a repo ──────────────────────────────────────────────────────────


def test_mined_tasks_carry_their_commit_date(mined, repo):
    [task] = mined.load_tasks()
    assert task.committed == _git(repo["path"], "log", "-1", "--format=%cI", repo["shas"]["mul"])


def test_mine_new_takes_only_commits_it_has_never_seen(mined, repo, config):
    from backend.orchestrator.agentbench.mine import mine_new

    run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"])
    assert mine_new(mined, config) == []  # mul is known, refactor was dropped
    div = _commit(repo["path"], "feat: add div", {
        "calc/ops.py": OPS + "\n\ndef mul(a, b):\n    return a * b\n\n\ndef div(a, b):\n    return a / b\n",
        "tests/test_div.py": "from calc.ops import div\n\n\ndef test_div():\n    assert div(6, 3) == 2\n"})
    [new] = mine_new(mined, config)
    assert new.sha == div and len(mined.load_tasks()) == 2
    assert len(load_attempts(mined)) == 1  # the known task's attempt survived


def test_mine_new_knows_a_commit_by_its_full_sha(mined, repo, config):
    """Short shas lengthen as a repo grows: 'bc1c708' and 'bc1c7081' are one commit."""
    from backend.orchestrator.agentbench.mine import mine_new

    [task] = mined.load_tasks()
    task.sha = _git(repo["path"], "rev-parse", "--short=10", task.sha)
    mined.save_tasks([task])
    assert mine_new(mined, config) == []


def test_reseal_backfills_dates(mined, repo):
    [task] = mined.load_tasks()
    task.committed = ""
    mined.save_tasks([task])
    reseal(mined)
    assert mined.load_tasks()[0].committed.startswith("20")


def test_a_follow_job_mines_runs_and_rests(mined, repo, tmp_path):
    q = Queue(mined.dir.parent)
    job = q.add(str(mined.repo), ["fake:m"], "blind", pinned=PIN, follow=True)
    agent = FakeAgent("m", apply_gold)
    run = _job_runner(tmp_path, FakeMachine(), agent, q)
    drain(q, run, once=True, log=lambda m: None)
    [after] = q.jobs()
    assert after.status == PENDING and after.rest_until > time.time()
    assert after.why.startswith("up to date, all 1 cells counted")
    assert q.next_open(time.time()) is None  # resting
    _commit(repo["path"], "feat: add div", {
        "calc/ops.py": OPS + "\n\ndef mul(a, b):\n    return a * b\n\n\ndef div(a, b):\n    return a / b\n",
        "tests/test_div.py": "from calc.ops import div\n\n\ndef test_div():\n    assert div(6, 3) == 2\n"})
    q.update(job.id, rest_until=0.0)  # the next window
    agent._edit = lambda clone, sha: None  # attempting the new task is what's tested
    run(q.jobs()[0], None)
    assert len(mined.load_tasks()) == 2 and len(agent.calls) == 2


def test_a_follow_job_cant_name_tasks(mined):
    with pytest.raises(ValueError, match="follow"):
        Queue(mined.dir.parent).add(str(mined.repo), ["fake:m"], "blind", pinned=PIN,
                                    follow=True, tasks=["abc"])


def test_following_a_pinned_clone_fetches_and_mines_upstream(repo, tmp_path, config):
    from backend.orchestrator.agentbench.worker import follow

    pinned = tmp_path / "pinned"
    _git(tmp_path, "clone", "-q", str(repo["path"]), str(pinned))
    _git(pinned, "checkout", "-q", "--detach", repo["shas"]["docs"])
    bench = Bench(pinned, root=tmp_path / "state2")
    mine(bench, config, [repo["shas"]["mul"]])
    div = _commit(repo["path"], "feat: add div", {
        "calc/ops.py": OPS + "\n\ndef mul(a, b):\n    return a * b\n\n\ndef div(a, b):\n    return a / b\n",
        "tests/test_div.py": "from calc.ops import div\n\n\ndef test_div():\n    assert div(6, 3) == 2\n"})
    new = follow(bench, fetch=True, log=lambda m: None)
    assert [t.sha for t in new] == [div]
    assert _git(pinned, "rev-parse", "--short", "HEAD") == repo["shas"]["docs"]  # still pinned


def test_a_bench_written_by_a_newer_version_still_loads(mined, repo, tmp_path):
    run_matrix(mined, [FakeAgent("m", apply_gold)], ["blind"])
    line = json.loads(mined.tasks_path.read_text().splitlines()[0])
    mined.tasks_path.write_text(json.dumps({**line, "from_the_future": 1}) + "\n")
    [att] = list(mined.attempts.glob("*.json"))
    att.write_text(json.dumps({**json.loads(att.read_text()), "from_the_future": 1}))
    assert len(mined.load_tasks()) == 1 and len(load_attempts(mined)) == 1
    q = Queue(tmp_path / "q")
    q.add(str(mined.repo), ["fake:m"], "blind", pinned=PIN)
    q.path.write_text(json.dumps([{**json.loads(q.path.read_text())[0], "from_the_future": 1}]))
    assert len(q.jobs()) == 1


# ── Tonight: a repo to an answer by morning ──────────────────────────────────

from backend.orchestrator.agentbench import tonight  # noqa: E402
from backend.orchestrator.agentbench.guard import Check  # noqa: E402
from backend.orchestrator.cli.commands import agentbench as ab_cli  # noqa: E402


def test_a_window_through_midnight_is_its_real_length():
    assert tonight.window_hours("21:00-08:00") == 11
    assert tonight.window_hours("09:00-17:30") == 8.5
    assert tonight.window_hours("08:00-08:00") == 24


def test_only_a_large_machine_hears_about_a_larger_model():
    assert tonight.larger_model_hint(16) is None
    assert tonight.larger_model_hint("unknown") is None
    assert "64 GB" in tonight.larger_model_hint(64)


def _run_notified(q, job, tmp_path, agent, seen):
    run_job(q, job, None, pulled=lambda: {"m:latest": PIN["m"]},
            guard=lambda root, **kw: _guard(tmp_path, FakeMachine(), **kw),
            agents_for=lambda arms: [agent], which=lambda n: "/bin/" + n,
            isolated=lambda bench, config: [], log=lambda m: None,
            wrap_up=lambda bench, j: tonight.wrap_up(bench, j, notify=lambda *a: seen.append(a)))


def test_a_tonight_job_leaves_a_morning_report_and_says_so(mined, tmp_path):
    q = Queue(mined.dir.parent)
    job = q.add(str(mined.repo), ["fake:m"], "blind", pinned=PIN, notify=True)
    seen: list = []
    _run_notified(q, job, tmp_path, FakeAgent("m", apply_gold), seen)
    text = tonight.morning_path(mined).read_text()
    assert "the queue: done, all 1 cells counted" in text
    assert "mahoraga audit verdict" in text and "--arm fake:m --cond blind" in text
    assert "--tasks-per-month <how many" in text and "VERDICT" not in text
    [(title, message)] = seen
    assert title == f"Mahoraga: {mined.repo.name}" and message.startswith("m resolved 1 of 1")


def test_with_usage_saved_the_morning_carries_the_verdict(mined, tmp_path):
    from backend.orchestrator.audit import usage

    usage.save(mined.root, usage.Usage(tasks_per_month=200, api_spend=150))
    q = Queue(mined.dir.parent)
    job = q.add(str(mined.repo), ["fake:m"], "both", pinned=PIN, notify=True)
    seen: list = []
    _run_notified(q, job, tmp_path, FakeAgent("m", apply_gold), seen)
    text = tonight.morning_path(mined).read_text()
    assert "for 200 tasks a month, $150 a month of API:" in text
    assert text.count("VERDICT:") == 2 and "--tasks-per-month <how many" not in text
    [(_, message)] = seen
    assert message.count("m resolved 1 of 1, ") == 2


def test_a_tonight_job_that_cannot_start_still_says_why_by_morning(mined, tmp_path):
    q = Queue(mined.dir.parent)
    job = q.add(str(mined.repo), ["fake:m"], "blind", pinned={"m": "sha256:" + "f" * 64},
                notify=True)
    seen: list = []
    _run_notified(q, job, tmp_path, FakeAgent("m", apply_gold), seen)
    text = tonight.morning_path(mined).read_text()
    assert "the night didn't run:" in text and "mahoraga audit tonight" in text
    [(_, message)] = seen
    assert message.startswith("the night didn't run:") and "audit report" not in message


def test_a_job_nobody_asked_about_writes_nothing(mined, tmp_path):
    q = Queue(mined.dir.parent)
    job = q.add(str(mined.repo), ["fake:m"], "blind", pinned=PIN)
    seen: list = []
    _run_notified(q, job, tmp_path, FakeAgent("m", apply_gold), seen)
    assert not tonight.morning_path(mined).exists() and seen == []


@pytest.fixture
def tonight_env(tmp_path, monkeypatch):
    """`tonight` on a machine that passes every check, with the worker faked."""
    monkeypatch.setenv("MAHORAGA_AGENTBENCH_ROOT", str(tmp_path / "cli-state"))
    checks = [Check("power", True, "AC"), Check("aider", True, "/bin/aider"),
              Check("ollama", True, "1 models")]
    monkeypatch.setattr(Guard, "preflight", lambda self, agents: list(checks))
    monkeypatch.setattr("backend.orchestrator.agentbench.isolation.check",
                        lambda bench, config: [Check("isolation: source", True, "hidden")])
    monkeypatch.setattr("backend.orchestrator.agentbench.ollama.pulled_models",
                        lambda: {"qwen3.5:latest": "sha256:0123456789abcdef"})
    monkeypatch.setattr(manifest, "machine", lambda: {"memory_gb": 16})
    workers: list = []
    monkeypatch.setattr(ab_cli, "_ensure_worker", lambda: workers.append(1))
    return {"checks": checks, "workers": workers, "root": tmp_path / "cli-state"}


def test_tonight_mines_queues_and_starts_the_worker_once(repo, tonight_env):
    runner = CliRunner()
    args = ["tonight", str(repo["path"]), "--python", sys.executable]
    r = runner.invoke(agentbench_app, args)
    assert r.exit_code == 0, r.output
    assert "1 tasks in the bench" in r.output and "in the morning: mahoraga audit report" in r.output
    [job] = Queue(tonight_env["root"]).jobs()
    assert job.notify and job.window == "21:00-08:00" and job.arms == ["aider:qwen3.5:latest@ctx=131072"]
    assert job.pinned == {"qwen3.5:latest": "sha256:0123456789abcdef"}
    r = runner.invoke(agentbench_app, args)
    assert r.exit_code == 0 and f"already queued as {job.id}" in r.output
    assert len(Queue(tonight_env["root"]).jobs()) == 1 and len(tonight_env["workers"]) == 2


def test_tonight_counts_the_jobs_queued_ahead_of_it(repo, tonight_env):
    Queue(tonight_env["root"]).add(str(repo["path"]), ["aider:other:1b"], "blind", pinned={},
                                   window="21:00-08:00")
    args = ["tonight", str(repo["path"]), "--python", sys.executable]
    r = CliRunner().invoke(agentbench_app, args)
    assert r.exit_code == 0, r.output
    assert "behind 1 attempt already queued (~0.2 h)" in r.output
    # Run again, it's the same job, and only what is queued before it counts.
    r = CliRunner().invoke(agentbench_app, args)
    assert "already queued as" in r.output and "behind 1 attempt" in r.output


def test_tonight_says_when_the_commands_it_prints_wont_run(tmp_path):
    assert tonight.cli_missing(which=lambda name: "/usr/local/bin/mahoraga") is None
    gone = lambda name: None  # noqa: E731
    assert "uv tool install mahoraga-audit" in tonight.cli_missing(which=gone, here=tmp_path)
    (tmp_path / "mahoraga").touch()
    note = tonight.cli_missing(which=gone, here=tmp_path)
    assert f"Add {tmp_path} to your PATH" in note and str(tmp_path / "mahoraga") in note


def test_tonight_queues_nothing_when_something_needs_fixing(repo, tonight_env):
    tonight_env["checks"][1] = Check("aider", False, "aider not on PATH")
    r = CliRunner().invoke(agentbench_app, ["tonight", str(repo["path"]), "--python", sys.executable])
    assert r.exit_code == 1 and "install aider: uv tool install" in r.output
    assert Queue(tonight_env["root"]).jobs() == [] and tonight_env["workers"] == []


def test_tonight_waits_out_a_machine_on_battery(repo, tonight_env):
    tonight_env["checks"][0] = Check("power", False, "on battery (40%)", waits=True)
    r = CliRunner().invoke(agentbench_app, ["tonight", str(repo["path"]), "--python", sys.executable])
    assert r.exit_code == 0, r.output
    assert "the run waits these out" in r.output and len(Queue(tonight_env["root"]).jobs()) == 1


def test_tonight_saves_how_you_use_the_cloud_once(repo, tonight_env):
    from backend.orchestrator.audit import usage

    runner = CliRunner()
    base = ["tonight", str(repo["path"]), "--python", sys.executable]
    r = runner.invoke(agentbench_app, base)
    assert r.exit_code == 0 and "skipped: pass --tasks-per-month" in r.output
    assert usage.load(tonight_env["root"]) is None
    r = runner.invoke(agentbench_app, base + ["--tasks-per-month", "120", "--plan", "claude-pro"])
    assert r.exit_code == 0, r.output
    assert "saved: 120 tasks a month, claude-pro" in r.output
    assert "--cond blind\n" in r.output and "--tasks-per-month <how many" not in r.output
    r = runner.invoke(agentbench_app, base)
    assert "120 tasks a month, claude-pro, as saved" in r.output
    r = runner.invoke(agentbench_app, base + ["--plan", "claude-max-20x"])
    assert r.exit_code != 0 and usage.load(tonight_env["root"]).plan == "claude-pro"


def test_tonight_asks_in_a_terminal_and_asks_again_on_a_bad_answer(repo, tonight_env, monkeypatch):
    from backend.orchestrator.audit import usage

    monkeypatch.setattr(ab_cli, "_interactive", lambda: True)
    r = CliRunner().invoke(agentbench_app, ["tonight", str(repo["path"]), "--python", sys.executable],
                           input="lots\n80\nclaude-max-20x\nclaude-max-20x 200\n")
    assert r.exit_code == 0, r.output
    assert "a count of tasks, like 120" in r.output and "pass --plan-price" in r.output
    assert usage.load(tonight_env["root"]) == usage.Usage(80, plan="claude-max-20x", plan_price=200.0)
    skipped = CliRunner().invoke(agentbench_app, ["tonight", str(repo["path"]), "--python",
                                                  sys.executable, "--window", "22:00-07:00"])
    assert "as saved" in skipped.output
