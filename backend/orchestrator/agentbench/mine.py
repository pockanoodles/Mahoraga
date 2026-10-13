"""Mine a repo's history into gated agent-bench tasks.

Task for commit C = parent(C) + C's test changes. Every other change C made
(source, docs, config) is reverted, so the tree holds the tests but not the
solution.

Two gates drop a candidate, with the reason recorded:
  A. at C, the commit's own test modules pass   (the task is satisfiable)
  B. at the base, >=1 of those tests fails       (the task discriminates)
Then the full suite at the base fixes the pass-to-pass baseline that every
attempt is held to. A base with no passing tests at all can't detect a
regression, so it is dropped too.

A task clone holds no trace of the answer (`seal`): its base commit's parent
is C's parent, and it has its own objects, fetched up to that parent only.
Until 2026-10-13 the base sat on top of C in a `--shared` clone, so `git log`
showed the solution one commit back, and ornith under opencode ran
`git show` on it in its first attempt.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .bank import Bench, Task, SuiteConfig
from .proc import CRASH, git, passing, run_pytest


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (parts[0] in ("tests", "test") or name == "conftest.py"
            or name.startswith("test_") or name.endswith("_test.py"))


def is_test_module(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    # tests_*.py: tqdm's convention; pytest collects it only when configured to.
    return name.endswith(".py") and (name.startswith(("test_", "tests_"))
                                     or name.endswith("_test.py"))


def layout_pytest_args(repo: Path) -> list[str]:
    """Pytest args the repo's layout needs for tests to import the *task's*
    code.

    In a src layout, the package isn't at the repo root, so `python -m pytest`
    in a task clone can't find it there. It falls through to the editable
    install, which points at the original checkout, so every gate quietly tests
    the wrong tree (click and attrs mined 0 of 30 that way). Putting src/ on
    the path makes the clone's own copy win.
    """
    src = repo / "src"
    if src.is_dir() and any((d / "__init__.py").exists() for d in src.iterdir() if d.is_dir()):
        return ["-o", "pythonpath=src"]
    return []


@dataclass
class Candidate:
    sha: str
    subject: str
    src_lines: int
    src_files: int


def find_candidates(repo: Path, rev: str = "HEAD", limit: int = 25,
                    max_src_lines: int = 250) -> list[Candidate]:
    """Newest-first non-merge commits that change both source and a test module.

    Cheap: reads `git log --numstat` only. Whether a candidate is a usable
    task is decided by the gates in `build_task`.
    """
    out = git("log", "--no-merges", "--no-renames", "--numstat",
              "--format=%x00%h%x09%p%x09%s", rev, cwd=repo)
    found: list[Candidate] = []
    for record in out.split("\x00")[1:]:
        header, *stat_lines = record.strip("\n").split("\n")
        sha, parents, subject = (header.split("\t", 2) + ["", ""])[:3]
        if not parents:  # a root commit has no parent to be the task base
            continue
        src_lines = src_files = 0
        has_test = False
        for line in stat_lines:
            added, removed, path = (line.split("\t") + ["", "", ""])[:3]
            if not path or added == "-":
                continue
            if is_test_path(path):
                has_test = has_test or is_test_module(path)
            elif path.endswith(".py"):
                src_lines += int(added) + int(removed)
                src_files += 1
        if has_test and 0 < src_lines <= max_src_lines:
            found.append(Candidate(sha, subject, src_lines, src_files))
            if len(found) >= limit:
                break
    return found


class Drop(Exception):
    """A candidate that can't be a task; the message is the recorded reason."""


def build_task(bench: Bench, config: SuiteConfig, sha: str) -> Task:
    d = bench.clone(sha)
    if d.exists():
        shutil.rmtree(d)
    d.parent.mkdir(parents=True, exist_ok=True)
    git("clone", "-q", "--shared", "--no-checkout", str(bench.repo), str(d), cwd=d.parent)
    git("checkout", "-q", sha, cwd=d)
    if not git("rev-list", "--parents", "-n", "1", sha, cwd=d).split()[1:]:
        raise Drop("root commit: no parent to be the task base")

    def pytest(targets=None):
        return run_pytest(d, config.python, bench.home, targets, config.pytest_args)

    changes = [line.split("\t") for line in
               git("diff", "--name-status", "--no-renames", f"{sha}^", sha, cwd=d).splitlines()]
    tests = [p for status, p in changes if status != "D" and is_test_module(p)]
    src = [(status, p) for status, p in changes if not is_test_path(p)]
    gold_src = [p for _, p in src if p.endswith(".py")]
    if not tests:
        raise Drop("no test module added or modified")
    if not gold_src:
        raise Drop("no source change")

    at_c = pytest(tests)
    if CRASH in at_c:
        raise Drop(f"gate A: tests do not run at the commit ({at_c[CRASH][-200:]!r})")
    if any(v == "fail" for v in at_c.values()):
        raise Drop("gate A: the commit's own tests fail at the commit")
    passing_c = passing(at_c)
    if not passing_c:
        raise Drop("gate A: every target test skipped")

    for status, path in src:
        if status == "A":
            git("rm", "-q", "--", path, cwd=d)
        else:
            git("checkout", "-q", f"{sha}^", "--", path, cwd=d)
    git("-c", "user.name=agentbench", "-c", "user.email=agentbench@localhost",
        "commit", "-q", "--no-verify", "--allow-empty", "-m", "agentbench task base", cwd=d)
    numstat = git("diff", "--numstat", f"{sha}^", sha, "--", *gold_src, cwd=d)
    subject = git("log", "-1", "--format=%s", sha, cwd=d).strip()
    body = git("log", "-1", "--format=%b", sha, cwd=d).strip()
    base = seal(d, bench.repo, git("rev-parse", f"{sha}^", cwd=d).strip())

    at_base = pytest(tests)
    f2p = sorted(passing_c if CRASH in at_base else passing_c - passing(at_base))
    if not f2p:
        raise Drop("gate B: the tests already pass without the fix")

    p2p = sorted(passing(pytest()))
    if not p2p:
        raise Drop("no pass-to-pass baseline: the suite can't run at the base")

    gold_lines = sum(int(a) + int(r) for a, r, *_ in
                     (line.split("\t") for line in numstat.splitlines()) if a != "-")
    return Task(
        sha=sha, base=base, subject=subject, body=body,
        gold_src=gold_src, gold_lines=gold_lines, test_files=tests, f2p=f2p, p2p=p2p,
    )


def seal(d: Path, repo: Path, parent: str) -> str:
    """Rebuild the clone `d` around its HEAD tree with nothing of the answer
    in it, and return the new base commit.

    The new clone fetches `parent`'s history alone (by sha, which the source
    has to be told to allow), takes the HEAD tree's objects across without the
    commit that held them, and commits that tree onto `parent`. No remote, no
    alternates, no reflog of the old HEAD: what the agent can reach is the
    task's tree and the history before the change.
    """
    tree = git("rev-parse", "HEAD^{tree}", cwd=d).strip()
    sealed = d.with_name(d.name + ".sealing")
    if sealed.exists():
        shutil.rmtree(sealed)
    git("init", "-q", str(sealed), cwd=d.parent)
    git("fetch", "-q", "--no-tags",
        "--upload-pack", "git -c uploadpack.allowAnySHA1InWant=true upload-pack",
        str(repo), parent, cwd=sealed)
    objects = git("rev-list", "--objects", tree, cwd=d)
    pack = subprocess.run(["git", "pack-objects", "-q", "--stdout"], cwd=d, input=objects.encode(),
                          capture_output=True, check=True).stdout
    subprocess.run(["git", "unpack-objects", "-q"], cwd=sealed, input=pack, check=True)
    base = git("-c", "user.name=agentbench", "-c", "user.email=agentbench@localhost",
               "commit-tree", tree, "-p", parent, "-m", "agentbench task base", cwd=sealed).strip()
    git("update-ref", "HEAD", base, cwd=sealed)
    git("reset", "-q", "--hard", cwd=sealed)
    (sealed / ".git" / "FETCH_HEAD").unlink(missing_ok=True)
    git("reflog", "expire", "--expire=now", "--all", cwd=sealed)
    git("gc", "-q", "--prune=now", cwd=sealed)
    shutil.rmtree(d)
    sealed.rename(d)
    return base


def leaks(d: Path, sha: str) -> list[str]:
    """How the clone `d` could show an agent commit `sha`, the answer. Empty =
    sealed."""
    out = []
    if subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=d,
                      capture_output=True).returncode == 0:
        out.append(f"commit {sha} is readable")
    if (d / ".git" / "objects" / "info" / "alternates").exists():
        out.append("objects are shared with another repository")
    if git("remote", cwd=d).strip():
        out.append("it has a remote")
    return out


def reseal(bench: Bench, progress: Callable[[str, str], None] = lambda sha, msg: None) -> int:
    """Seal every task clone mined before `seal` existed, keeping its attempts.

    Rebuilding a task with `mine` would delete them, since a new base usually
    means a new task. Here it doesn't: the base tree is carried over object for
    object and checked equal, so every attempt ran on exactly the tree the
    sealed clone holds. Returns how many clones were sealed.
    """
    tasks = bench.load_tasks()
    n = 0
    for t in tasks:
        d = bench.clone(t.sha)
        if not d.exists():
            progress(t.sha, "no clone: rebuild it with `mahoraga audit mine --commit`")
            continue
        if not leaks(d, t.sha):
            continue
        tree = git("rev-parse", f"{t.base}^{{tree}}", cwd=d).strip()
        git("reset", "-q", "--hard", t.base, cwd=d)
        git("clean", "-qfdx", cwd=d)
        base = seal(d, bench.repo, git("rev-parse", f"{t.sha}^", cwd=bench.repo).strip())
        if git("rev-parse", f"{base}^{{tree}}", cwd=d).strip() != tree:
            raise RuntimeError(f"{t.sha}: the sealed base's tree differs from the mined one")
        t.base = base
        n += 1
        progress(t.sha, "sealed")
    bench.save_tasks(tasks)
    return n


def mine(bench: Bench, config: SuiteConfig, shas: list[str],
         progress: Callable[[str, Task | None, str | None], None] = lambda *a: None,
         ) -> tuple[list[Task], dict[str, str]]:
    """Build each sha into a task; merge into the bench by sha.

    Rebuilding a subset keeps the other tasks. A rebuilt task's base commit is
    new, so its old attempts no longer apply and are removed.
    """
    bench.save_config(config)
    tasks = {t.sha: t for t in bench.load_tasks()}
    dropped: dict[str, str] = (json.loads(bench.dropped_path.read_text())
                               if bench.dropped_path.exists() else {})
    for sha in shas:
        for old in bench.attempts.glob(f"*__{sha}__r*"):
            old.unlink()
        try:
            task = build_task(bench, config, sha)
        except Drop as e:
            tasks.pop(sha, None)
            dropped[sha] = str(e)
            progress(sha, None, str(e))
            continue
        except Exception as e:  # noqa: BLE001 — one bad commit must not end the run
            tasks.pop(sha, None)
            dropped[sha] = f"error: {e}"[:300]
            progress(sha, None, dropped[sha])
            continue
        tasks[sha] = task
        dropped.pop(sha, None)
        progress(sha, task, None)
        bench.save_tasks(list(tasks.values()))
    bench.save_tasks(list(tasks.values()))
    bench.dropped_path.write_text(json.dumps(dropped, indent=1))
    return list(tasks.values()), dropped
