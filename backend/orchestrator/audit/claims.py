"""Published audit numbers, recomputed from an export (agentbench/export.py).

`orch bench verify` checks every claim in experiments/claims.json against the
artifact it names. A claim of kind "agentbench" picks a slice of an export's
rows and lists what the report prints for it: the task count, the rate and its
interval, and, when it gives the verdict's assumptions, the verdict's call,
monthly net and break-even figures. Each is computed by the code `mahoraga
audit report` and `mahoraga audit verdict` use, so a claim passing means the
report's digits are the tool's digits on that data.

    "select": {"arm": "aider:qwen3.5:laptop-dec52a4", "cond": "blind",
               "repos": ["attrs", "click"], "machine": "Mac16,9, 64 GB",
               "committed_from": "2026-04-16", "committed_before": "...",
               "tasks_of": "aider:granite4.1:8b"}
    "verdict": {"tasks_per_month": 200, "api_spend": 150, ...Assumptions}

Every select key is optional except arm and cond: one arm and condition at a
time, for the reason evidence.py gives. `tasks_of` keeps only the tasks another
arm attempted too, for a comparison on the same tasks after a bench has grown:
a followed repo gains tasks every window, and the arm run on them gains rows.

`limit_s` counts the attempts that stand at that attempt limit: those run under
it, and those run under a shorter one that ended on their own. The limit is a
kill from outside that the agent never sees, so an attempt that finished in 7
minutes under a 20-minute limit is the attempt a 90-minute limit would have
produced. One that ran out of time says nothing about a longer limit, and one
run under a longer limit can't be cut shorter after the fact, so neither
counts.

    "versus": {"artifact": "experiments/agentbench/studio-2026-10.jsonl",
               "select": {"arm": "...", "cond": "blind"}}

compares the claim's slice with another task by task: the tasks both ran, how
many each solved, how many only one solved, and the exact McNemar p on those.
The other slice comes from the claim's own artifact unless `versus` names one,
since two machines' rows live in two files.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Optional

from backend.orchestrator.agentbench.stats import mcnemar_exact, task_rate

from .verdict import Assumptions, Billing, Evidence, breakeven_cloud_cost, breakeven_rate, decide

SELECT_KEYS = {"arm", "cond", "repos", "machine", "committed_from", "committed_before",
               "tasks_of", "limit_s"}


def stands_at(row: dict, limit: float) -> bool:
    """Whether an exported attempt counts at `limit` seconds (see `limit_s`)."""
    if row.get("limit_s") is None or "timed_out" not in row:
        raise ValueError(f"{row['repo']}/{row['sha']} doesn't say its limit or whether it ran "
                         "out of time; export it again")
    return row["limit_s"] == limit or (row["limit_s"] < limit and not row["timed_out"])


def select(rows: list[dict], spec: dict[str, Any]) -> list[dict]:
    unknown = set(spec) - SELECT_KEYS
    if unknown or not {"arm", "cond"} <= set(spec):
        raise ValueError(f"select needs arm and cond, and knows only {sorted(SELECT_KEYS)}"
                         + (f"; got {sorted(unknown)}" if unknown else ""))
    out = [r for r in rows if r["arm"] == spec["arm"] and r["cond"] == spec["cond"]]
    if "tasks_of" in spec:
        theirs = {(r["repo"], r["sha"]) for r in rows
                  if r["arm"] == spec["tasks_of"] and r["cond"] == spec["cond"]}
        if not theirs:
            raise ValueError(f"no rows for {spec['tasks_of']} / {spec['cond']} to take tasks from")
        out = [r for r in out if (r["repo"], r["sha"]) in theirs]
    if "repos" in spec:
        out = [r for r in out if r["repo"] in set(spec["repos"])]
    if "machine" in spec:
        out = [r for r in out if r["machine"] == spec["machine"]]
    if "committed_from" in spec or "committed_before" in spec:
        if any(not r.get("committed") for r in out):
            raise ValueError("a date split needs every task's commit date; "
                             "run `mahoraga audit reseal` and export again")
        lo, hi = spec.get("committed_from", ""), spec.get("committed_before", "9999")
        out = [r for r in out if lo <= r["committed"][:10] < hi]
    if "limit_s" in spec:
        out = [r for r in out if stands_at(r, float(spec["limit_s"]))]
    return out


def evidence(rows: list[dict]) -> Evidence:
    """What evidence.from_benches builds, from exported rows. Rows exported
    before exports said which attempts ran out of time count none as timed
    out, so a verdict read off them can't be flagged as the limit's."""
    per_task: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        per_task[f"{r['repo']}/{r['sha']}"].append(r["resolved"])
    machines, tiers = {r["machine"] for r in rows}, {r["tier"] for r in rows}
    if len(machines) > 1:
        raise ValueError(f"the rows come from {len(machines)} machines ({'; '.join(sorted(machines))})")
    if len(tiers) > 1:
        raise ValueError(f"the rows mix grading tiers {sorted(tiers)}")
    repos = sorted({r["repo"] for r in rows})
    return Evidence(rate=task_rate(per_task),
                    minutes_per_attempt=statistics.median(r["secs"] for r in rows) / 60,
                    tier=tiers.pop(), label=f"{rows[0]['arm']} / {rows[0]['cond']}, {', '.join(repos)}",
                    hardware=machines.pop(),
                    failed=sum(not r["resolved"] for r in rows),
                    timed_out=sum(bool(r.get("timed_out")) and not r["resolved"] for r in rows))


def _one_per_task(rows: list[dict]) -> dict[tuple[str, str], bool]:
    out: dict[tuple[str, str], bool] = {}
    for r in rows:
        key = (r["repo"], r["sha"])
        if key in out:
            raise ValueError(f"{r['arm']} has more than one attempt on {r['repo']}/{r['sha']}, "
                             "and a paired test takes one per task")
        out[key] = r["resolved"]
    return out


def _same_task(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """Short shas lengthen as a repo grows, so two machines can spell one
    commit with different lengths: one is then a prefix of the other."""
    return a[0] == b[0] and (a[1].startswith(b[1]) or b[1].startswith(a[1]))


def paired(this: list[dict], other: list[dict]) -> dict[str, float]:
    """Two slices task by task, on the tasks both ran."""
    a, b = _one_per_task(this), _one_per_task(other)
    pairs = []
    for ka in sorted(a):
        hits = [kb for kb in b if _same_task(ka, kb)]
        if len(hits) > 1:
            raise ValueError(f"{ka[0]}/{ka[1]} matches {len(hits)} tasks on the other side")
        if hits:
            pairs.append((a[ka], b[hits[0]]))
    if not pairs:
        raise ValueError("the two slices share no tasks")
    only_a = sum(x and not y for x, y in pairs)
    only_b = sum(y and not x for x, y in pairs)
    return {"pairs": float(len(pairs)), "pair_resolved": float(sum(x for x, _ in pairs)),
            "versus_resolved": float(sum(y for _, y in pairs)),
            "only_this": float(only_a), "only_versus": float(only_b),
            "mcnemar_p": mcnemar_exact(only_a, only_b)}


def metrics(rows: list[dict], claim: dict[str, Any],
            versus_rows: Optional[list[dict]] = None) -> dict[str, Optional[float | str]]:
    """`versus_rows` are the rows of the artifact `versus` names, when it names
    one; otherwise the other slice comes from `rows`."""
    picked = select(rows, claim.get("select") or {})
    if not picked:
        return {"tasks": 0.0}
    ev = evidence(picked)
    out: dict[str, Optional[float | str]] = {
        "tasks": float(ev.rate.tasks),
        "attempts": float(ev.rate.attempts),
        "resolved": ev.rate.point * ev.rate.tasks,  # tasks solved, repeats as fractions
        "rate": ev.rate.point, "rate_lo": ev.rate.lo, "rate_hi": ev.rate.hi,
        "failed": float(ev.failed), "timed_out": float(ev.timed_out),
    }
    spec = dict(claim.get("verdict") or {})
    if spec:
        billing = Billing.api(float(spec.pop("api_spend")))
        a = Assumptions(**spec)
        v = decide(ev, a, billing)
        out["call"] = v.call
        out["confident"] = "yes" if v.confident else "no"
        if v.monthly_usd:
            out["net_lo"], out["net"], out["net_hi"] = v.monthly_usd
        out["breakeven_rate"] = breakeven_rate(ev, a, billing.monthly_usd / a.tasks_per_month)
        out["breakeven_cloud_usd"] = breakeven_cloud_cost(ev, a)
    if "versus" in claim:
        theirs = select(rows if versus_rows is None else versus_rows,
                        claim["versus"].get("select") or {})
        if theirs:
            evidence(theirs)  # one machine and one grading tier, as for this slice
        out.update(paired(picked, theirs))
    return out
