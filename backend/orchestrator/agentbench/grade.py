"""Grading: what an attempt's edit is worth, and how much to trust that.

Running an attempt and grading it are separate steps, because not every task
has tests that can decide it. Each grader has a *tier*, and results from
different tiers are never added together. A test-verified resolve and a
judge's opinion are different kinds of evidence, and a report that sums them
hides that.

  test-verified       the task's tests pass with the edit, nothing that passed
                      before has regressed (this module)
  reference-matched   no test decides it; compared against the change the user
                      kept (v2, transcript tasks)
  judge-graded        a model's verdict, opt-in, counted only once calibrated
                      against hand labels (v2)

Only the first tier exists yet. The tier names are fixed here so the ledger,
the report and the verdict agree on them before the other graders exist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .bank import SuiteConfig, Task
from .proc import CRASH, run_pytest

TEST_VERIFIED = "test-verified"
REFERENCE_MATCHED = "reference-matched"
JUDGE_GRADED = "judge-graded"
TIERS = (TEST_VERIFIED, REFERENCE_MATCHED, JUDGE_GRADED)


@dataclass
class Grade:
    tier: str
    resolved: bool
    f2p_pass: int = 0
    f2p_total: int = 0
    regressions: int = 0
    regression_ids: list[str] = field(default_factory=list)
    suite_crashed: bool = False


class Grader(Protocol):
    tier: str

    def grade(self, clone: Path, task: Task, config: SuiteConfig, home: Path) -> Grade: ...


class TestGrader:
    """Resolved = every fail-to-pass test passes AND no pass-to-pass test
    regressed across the full suite. The full-suite run is what catches an
    edit that fixes the target by breaking something else.

    Grades the tree as it stands: the caller restores any test files the
    agent touched before calling, so an agent can't pass by editing them.
    """

    tier = TEST_VERIFIED
    __test__ = False  # not a pytest test class, despite the name

    def grade(self, clone: Path, task: Task, config: SuiteConfig, home: Path) -> Grade:
        def pytest(targets=None):
            return run_pytest(clone, config.python, home, targets, config.pytest_args)

        targets = pytest(task.test_files)
        f2p_pass = sum(targets.get(t) == "pass" for t in task.f2p)
        suite = pytest()
        crashed = CRASH in suite
        regressions = sorted(t for t in task.p2p if suite.get(t) != "pass")
        return Grade(
            tier=self.tier,
            resolved=f2p_pass == len(task.f2p) and not regressions and not crashed,
            f2p_pass=f2p_pass, f2p_total=len(task.f2p),
            regressions=len(regressions), regression_ids=regressions[:20],
            suite_crashed=crashed,
        )
