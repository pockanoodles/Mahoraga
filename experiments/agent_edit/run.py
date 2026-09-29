"""Run aider + a local Ollama model on each task and score it.

Conditions:
  blind    — task text only; aider finds the files itself via its repo map.
  feedback — same, plus --auto-test on the task's test files, so aider sees
             failing output and gets its built-in retries (the cascade would
             run those tests anyway before deciding to escalate).

Scoring (tests are restored from the base before scoring, so an agent can't
pass by editing them — but doing so is flagged):
  resolved   = every F2P test passes AND no P2P test regressed
  regressions = P2P tests that no longer pass (the "did it break other code" check)

usage: run.py <model> <cond> [sha ...]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time

from common import PY, ROOT, WORK, git, load_tasks, run_pytest, sh

RESULTS = ROOT / "results"
SETTINGS = ROOT / "model-settings.yml"
AIDER_TIMEOUT = 20 * 60


def prompt(t: dict) -> str:
    return (
        f"Make the following change to this repository.\n\n{t['subject']}\n\n{t['body']}\n\n"
        f"Tests covering this change already exist in: {', '.join(t['test_files'])}. "
        "Do not modify any test files; change the source code so those tests pass "
        "without breaking anything else."
    )


def reset(d, base):
    git("reset", "-q", "--hard", base, cwd=d)
    git("clean", "-qfdx", "-e", ".aider.tags.cache.v4", cwd=d)


def run_one(t: dict, model: str, cond: str) -> dict:
    d = WORK / t["sha"]
    reset(d, t["base"])
    cmd = ["aider", "--model", f"ollama_chat/{model}", "--edit-format", "diff",
           "--model-settings-file", str(SETTINGS), "--map-tokens", "2048",
           "--yes-always", "--no-auto-commits", "--no-gitignore", "--no-pretty",
           "--no-stream", "--no-show-model-warnings", "--no-check-update",
           "--no-analytics", "--no-show-release-notes", "--message", prompt(t)]
    if cond == "feedback":
        cmd += ["--auto-test", "--test-cmd",
                f"{PY} -m pytest -q -x -p no:cacheprovider {' '.join(t['test_files'])}"]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=d, capture_output=True, text=True,
                           timeout=AIDER_TIMEOUT, env={**__import__("os").environ,
                                                       "OLLAMA_API_BASE": "http://127.0.0.1:11434"})
        log, timed_out = r.stdout + r.stderr, False
    except subprocess.TimeoutExpired as e:
        log, timed_out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), True
    secs = round(time.time() - t0, 1)

    changed = [l for l in git("status", "--porcelain", cwd=d).splitlines()
               if ".aider" not in l and ".probe-junit" not in l]
    files = sorted({l[3:].strip() for l in changed})
    touched_tests = [f for f in files if f.startswith("tests/") or f.endswith("conftest.py")]
    patch = git("diff", cwd=d)

    # Restore the real tests before scoring.
    git("checkout", "-q", t["base"], "--", "tests", cwd=d)
    git("clean", "-qfd", "tests", cwd=d)

    tgt = run_pytest(d, t["test_files"])
    f2p_pass = [n for n in t["f2p"] if tgt.get(n) == "pass"]
    suite = run_pytest(d, None, timeout=900)
    regressions = sorted(n for n in t["p2p"] if suite.get(n) != "pass")
    crashed = "__crash__" in suite

    tokens = [tuple(map(_num, m)) for m in
              re.findall(r"Tokens: ([\d.]+k?) sent, ([\d.]+k?) received", log)]
    res = {
        "sha": t["sha"], "model": model, "cond": cond, "secs": secs,
        "timed_out": timed_out,
        "resolved": len(f2p_pass) == len(t["f2p"]) and not regressions and not crashed,
        "f2p_pass": len(f2p_pass), "f2p_total": len(t["f2p"]),
        "regressions": len(regressions), "regression_ids": regressions[:20],
        "suite_crashed": crashed, "edited_files": files, "touched_tests": touched_tests,
        "gold_src": t["gold_src"],
        "hit_gold_file": bool(set(files) & set(t["gold_src"])),
        "no_edit": not [f for f in files if f not in touched_tests],
        "tokens_sent": sum(a for a, _ in tokens), "tokens_recv": sum(b for _, b in tokens),
        "llm_calls": len(tokens),
        "edit_errors": log.count("SearchReplaceNoExactMatch") + log.count("failed to match"),
    }
    stem = f"{model.replace(':', '_')}__{cond}__{t['sha']}"
    (RESULTS / f"{stem}.patch").write_text(patch)
    (RESULTS / f"{stem}.log").write_text(log)
    hist = d / ".aider.chat.history.md"
    if hist.exists():
        (RESULTS / f"{stem}.chat.md").write_text(hist.read_text())
    (RESULTS / f"{stem}.json").write_text(json.dumps(res, indent=1))
    reset(d, t["base"])
    return res


def _num(s: str) -> float:
    return float(s[:-1]) * 1000 if s.endswith("k") else float(s)


def main():
    model, cond, *shas = sys.argv[1:]
    RESULTS.mkdir(exist_ok=True)
    for t in load_tasks():
        if shas and t["sha"] not in shas:
            continue
        stem = f"{model.replace(':', '_')}__{cond}__{t['sha']}"
        if (RESULTS / f"{stem}.json").exists():
            continue
        r = run_one(t, model, cond)
        print(f"{t['sha']} {model} {cond}: resolved={r['resolved']} "
              f"f2p={r['f2p_pass']}/{r['f2p_total']} regress={r['regressions']} "
              f"files={r['edited_files']} {r['secs']}s", flush=True)


if __name__ == "__main__":
    main()
