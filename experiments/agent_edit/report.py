"""Summarise results/ into a per-arm table and a per-task matrix."""
from __future__ import annotations

import json
from collections import defaultdict

from common import ROOT, load_tasks

tasks = {t["sha"]: t for t in load_tasks()}
rows = [json.loads(p.read_text()) for p in sorted((ROOT / "results").glob("*.json"))]
rows = [r for r in rows if r["sha"] in tasks]

by_arm = defaultdict(list)
for r in rows:
    by_arm[(r["model"], r["cond"])].append(r)


def outcome(r):
    if r["resolved"]:
        return "PASS"
    if r["no_edit"]:
        return "no-edit"
    if not r["hit_gold_file"]:
        return "wrong-file"
    if r["regressions"] or r["suite_crashed"]:
        return "broke-other" if r["f2p_pass"] == r["f2p_total"] else "partial+broke"
    return "partial" if r["f2p_pass"] else "wrong-fix"


print(f"{'arm':34} {'n':>3} {'resolved':>9} {'broke>=1':>9} {'found-file':>10} "
      f"{'med min':>8}")
for (m, c), rs in sorted(by_arm.items()):
    n = len(rs)
    res = sum(r["resolved"] for r in rs)
    broke = sum(bool(r["regressions"] or r["suite_crashed"]) for r in rs)
    found = sum(r["hit_gold_file"] for r in rs)
    med = sorted(r["secs"] for r in rs)[n // 2] / 60
    print(f"{m + ' / ' + c:34} {n:>3} {res:>4} ({res / n:4.0%}) {broke:>9} "
          f"{found:>10} {med:>8.1f}")

arms = sorted(by_arm)
print("\n" + f"{'task':9} {'files':>5} {'lines':>5}  " +
      "  ".join(f"{m.split(':')[0][:8]}/{c[:5]:5}" for m, c in arms) + "  subject")
for sha, t in tasks.items():
    cells = []
    for a in arms:
        r = next((r for r in by_arm[a] if r["sha"] == sha), None)
        cells.append(f"{(outcome(r) if r else '-'):14}")
    print(f"{sha:9} {len(t['gold_src']):>5} {t['gold_lines']:>5}  " + "".join(cells) +
          f"  {t['subject'][:60]}")
