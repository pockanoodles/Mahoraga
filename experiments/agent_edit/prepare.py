"""Build agent-edit tasks from real Mahoraga commits.

Task for commit C = parent(C) + C's test changes. Everything else C touched is
reverted, so the tree holds the tests but not the solution.

Gates (a candidate that fails either is dropped, with the reason logged):
  A. at C, the commit's own test files pass     (the tests are satisfiable)
  B. at the task base, >=1 of those tests fails (the task discriminates)
F2P = tests passing at C and failing at base (must pass after the agent).
P2P = every test passing at base across the full suite (must stay passing).
"""
from __future__ import annotations

import json
import shutil
import sys

from common import REPO, TASKS, WORK, failing, git, run_pytest, sh

CANDIDATES = """
9102955 a7d91e2 3d72cc2 54f8b08 01a9a3a 557da52 fb53190 512b8e8 9f1e0c6
74a2e41 85d6a46 b3d1ff3 d8126d9 aaa64fd bfb0233 5ad756f 6fb6560 727c859
46d702c dec00d3 781385b b44da35
""".split()


def is_test(path: str) -> bool:
    return path.startswith("tests/") or path.endswith("conftest.py")


def build(sha: str) -> dict:
    d = WORK / sha
    if d.exists():
        shutil.rmtree(d)
    sh(["git", "clone", "-q", "--shared", "--no-checkout", str(REPO), str(d)])
    git("checkout", "-q", sha, cwd=d)
    full = git("rev-parse", sha, cwd=d).strip()
    subject = git("log", "-1", "--format=%s", sha, cwd=d).strip()
    body = git("log", "-1", "--format=%b", sha, cwd=d).strip()
    changes = [l.split("\t") for l in
               git("diff", "--name-status", "--no-renames", f"{sha}^", sha, cwd=d).splitlines()]

    tests = [p for st, p in changes if is_test(p) and st != "D" and p.endswith(".py")
             and p.startswith("tests/")]
    src = [(st, p) for st, p in changes if not is_test(p)]
    if not tests:
        return {"sha": sha, "drop": "no test files"}

    at_c = run_pytest(d, tests)
    if "__crash__" in at_c:
        return {"sha": sha, "drop": f"gate A crash: {at_c['__crash__'][-300:]}"}
    passing_c = {k for k, v in at_c.items() if v == "pass"}
    if failing(at_c):
        return {"sha": sha, "drop": f"gate A: {len(failing(at_c))} tests fail at C"}

    # Revert every non-test change back to the parent.
    for st, p in src:
        if st == "A":
            git("rm", "-q", p, cwd=d)
        else:
            git("checkout", "-q", f"{sha}^", "--", p, cwd=d)
    git("-c", "user.name=probe", "-c", "user.email=probe@local", "commit", "-q",
        "--allow-empty", "-m", "task base", cwd=d)
    base = git("rev-parse", "HEAD", cwd=d).strip()

    at_base = run_pytest(d, tests)
    f2p = sorted(passing_c - {k for k, v in at_base.items() if v == "pass"})
    if "__crash__" in at_base:
        f2p = sorted(passing_c)  # collection error = every target test fails
    if not f2p:
        return {"sha": sha, "drop": "gate B: no fail-to-pass tests"}

    suite = run_pytest(d, None, timeout=900)
    p2p = sorted(k for k, v in suite.items() if v == "pass")
    if not p2p:
        return {"sha": sha, "drop": "no pass-to-pass baseline (suite crashed at base)"}
    py_src = [p for _, p in src if p.endswith(".py")]

    return {
        "sha": sha, "full_sha": full, "base": base, "subject": subject, "body": body,
        "gold_src": [p for _, p in src if p.endswith(".py")],
        "gold_src_all": [p for _, p in src], "test_files": tests,
        "f2p": f2p, "p2p": p2p,
        "gold_lines": sum(int(a) + int(b) for a, b, *_ in
                          (l.split("\t") for l in git("diff", "--numstat", f"{sha}^", sha,
                                                      "--", *py_src,
                                                      cwd=d).splitlines()) if a != "-"),
    }


def main():
    shas = sys.argv[1:] or CANDIDATES
    WORK.mkdir(parents=True, exist_ok=True)
    kept, dropped = [], []
    for sha in shas:
        try:
            t = build(sha)
        except Exception as e:  # noqa: BLE001 — log and move on
            t = {"sha": sha, "drop": f"error: {e}"[:400]}
        (dropped if "drop" in t else kept).append(t)
        print(sha, t.get("drop") or f"OK f2p={len(t['f2p'])} p2p={len(t['p2p'])} "
              f"src={len(t['gold_src'])} lines={t['gold_lines']}", flush=True)
    # Merge by sha so rebuilding a subset doesn't discard the other tasks.
    prior = {t["sha"]: t for t in (json.loads(l) for l in TASKS.read_text().splitlines()
                                   if l.strip())} if TASKS.exists() else {}
    for t in dropped:
        prior.pop(t["sha"], None)
    prior.update({t["sha"]: t for t in kept})
    TASKS.write_text("".join(json.dumps(t) + "\n" for t in prior.values()))
    old = json.loads((TASKS.parent / "dropped.json").read_text()) \
        if (TASKS.parent / "dropped.json").exists() else []
    merged = {d["sha"]: d for d in old if d["sha"] not in prior}
    merged.update({d["sha"]: d for d in dropped})
    (TASKS.parent / "dropped.json").write_text(json.dumps(list(merged.values()), indent=1))
    print(f"kept {len(kept)}, dropped {len(dropped)}")


if __name__ == "__main__":
    main()
