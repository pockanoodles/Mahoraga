"""Published pass@1 numbers, recomputed from a graded bench run.

A claim of kind "pass_at_1" names a file `orch bench report grade` wrote: one
row per answer, each with the bank's `task`, the `agent` that answered and
whether the hidden tests `passed`. The tests ran once, when the file was
graded, so checking the claim runs no model code.

    "select": {"agent": "ollama:granite4.1-8b"}
    "versus": {"select": {"agent": "ollama:granite4.2-8b"},
               "artifact": "experiments/other_run_graded.jsonl"}

`select` picks one agent's answers. `versus` compares them task by task with
another agent's, from the claim's own file unless it names another: the tasks
both answered, how many only one got right, and the exact McNemar p on those.

Besides the rate, a claim can print what the run itself recorded: answers
that errored or ran out of time, answers the cascade sent to another model
(which would make the rate that model's), and answers that reasoned before
replying. A run that never recorded a field has no number for it.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.orchestrator.agentbench.stats import mcnemar_exact, wilson


def select(rows: list[dict], spec: dict[str, Any]) -> list[dict]:
    if set(spec) != {"agent"}:
        raise ValueError(f"select takes exactly one key, agent; got {sorted(spec)}")
    picked = [r for r in rows if r.get("agent") == spec["agent"]]
    if any("passed" not in r for r in picked):
        raise ValueError("the rows aren't graded; write them with `orch bench report grade`")
    return picked


def _one_per_task(rows: list[dict]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for r in rows:
        if r["task"] in out:
            raise ValueError(f"{r['agent']} answered {r['task']} more than once, "
                             "and pass@1 takes one answer per task")
        out[r["task"]] = bool(r["passed"])
    return out


def _count(rows: list[dict], key: str, hit) -> Optional[float]:
    """Rows where `hit(value)` holds, or None when no row recorded `key`. An
    answer that errored writes none of the reply's fields, so it counts as
    not escalated and not reasoned rather than as unknown."""
    if not any(key in r for r in rows):
        return None
    return float(sum(1 for r in rows if key in r and hit(r[key])))


def metrics(rows: list[dict], claim: dict[str, Any],
            versus_rows: Optional[list[dict]] = None) -> dict[str, Optional[float]]:
    """`versus_rows` are the rows of the file `versus` names, when it names
    one; otherwise the other agent's answers come from `rows`."""
    picked = select(rows, claim.get("select") or {})
    mine = _one_per_task(picked)
    if not mine:
        return {"n": 0.0}
    k, n = sum(mine.values()), len(mine)
    lo, hi = wilson(k, n)
    out: dict[str, Optional[float]] = {
        "n": float(n), "passed": float(k), "pass_at_1": k / n, "pass_lo": lo, "pass_hi": hi,
        "errors": _count(picked, "error", bool),
        "timed_out": _count(picked, "timed_out", bool),
        "escalated": _count(picked, "escalated_to", bool),
        "reasoned": _count(picked, "thinking_chars", lambda c: bool(c and c > 0)),
    }
    if "versus" in claim:
        theirs = _one_per_task(select(rows if versus_rows is None else versus_rows,
                                      claim["versus"].get("select") or {}))
        common = sorted(mine.keys() & theirs.keys())
        if not common:
            raise ValueError("the two agents answered no task in common")
        only_a = sum(mine[t] and not theirs[t] for t in common)
        only_b = sum(theirs[t] and not mine[t] for t in common)
        out.update({"pairs": float(len(common)),
                    "pair_passed": float(sum(mine[t] for t in common)),
                    "versus_passed": float(sum(theirs[t] for t in common)),
                    "only_this": float(only_a), "only_versus": float(only_b),
                    "mcnemar_p": mcnemar_exact(only_a, only_b)})
    return out
