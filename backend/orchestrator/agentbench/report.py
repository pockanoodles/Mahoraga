"""Summarise a bench's attempts: per-arm totals and a per-task matrix."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .bank import Task
from .runner import Attempt

# Ordered: the first matching label wins, most informative first.
OUTCOMES = ("resolved", "no-edit", "wrong-file", "broke-other", "partial+broke",
            "partial", "wrong-fix")


def outcome(a: Attempt) -> str:
    """What happened, in one word. `broke-other` = fixed the target but broke
    code elsewhere; the full-suite check is what sees it."""
    if a.resolved:
        return "resolved"
    if a.no_edit:
        return "no-edit"
    if not a.hit_gold_file:
        return "wrong-file"
    broke = a.regressions > 0 or a.suite_crashed
    if broke:
        return "broke-other" if a.f2p_pass == a.f2p_total else "partial+broke"
    return "partial" if a.f2p_pass else "wrong-fix"


@dataclass
class ArmSummary:
    arm: str
    cond: str
    n: int
    resolved: int
    multi_file_resolved: int
    multi_file_n: int
    broke_other_code: int
    touched_tests: int
    median_minutes: float
    outcomes: dict[str, int]
    # Across repeats: tasks solved in at least one attempt, and in every one.
    tasks: int
    tasks_solved_ever: int
    tasks_solved_always: int
    repeats: int


def summarise(tasks: list[Task], attempts: list[Attempt]) -> list[ArmSummary]:
    by_sha = {t.sha: t for t in tasks}
    groups: dict[tuple[str, str], list[Attempt]] = defaultdict(list)
    for a in attempts:
        if a.sha in by_sha:  # attempts for tasks since dropped don't count
            groups[(a.arm, a.cond)].append(a)
    out = []
    for (arm, cond), rows in sorted(groups.items()):
        multi = [a for a in rows if len(by_sha[a.sha].gold_src) > 1]
        secs = sorted(a.secs for a in rows)
        per_task: dict[str, list[bool]] = defaultdict(list)
        for a in rows:
            per_task[a.sha].append(a.resolved)
        out.append(ArmSummary(
            arm=arm, cond=cond, n=len(rows),
            resolved=sum(a.resolved for a in rows),
            multi_file_resolved=sum(a.resolved for a in multi), multi_file_n=len(multi),
            broke_other_code=sum(a.regressions > 0 or a.suite_crashed for a in rows),
            touched_tests=sum(bool(a.touched_tests) for a in rows),
            median_minutes=round(secs[len(secs) // 2] / 60, 1),
            outcomes=dict(Counter(outcome(a) for a in rows)),
            tasks=len(per_task),
            tasks_solved_ever=sum(any(v) for v in per_task.values()),
            tasks_solved_always=sum(all(v) for v in per_task.values()),
            repeats=max(len(v) for v in per_task.values()),
        ))
    return out


def _cell(rows: list[Attempt]) -> str:
    """One attempt: its outcome. Several: resolved count, else the commonest failure."""
    if not rows:
        return "-"
    if len(rows) == 1:
        return outcome(rows[0])
    solved = sum(a.resolved for a in rows)
    if solved:
        return f"{solved}/{len(rows)} resolved"
    return Counter(outcome(a) for a in rows).most_common(1)[0][0] + f" x{len(rows)}"


def degraded_note(degraded: list[Attempt]) -> str | None:
    """Attempts the guard threw out, and why. They are in no count above."""
    if not degraded:
        return None
    why = Counter(r.split(":")[0].split(" (")[0] for a in degraded for r in a.degraded)
    return (f"{len(degraded)} degraded attempts excluded (machine unhealthy): " +
            ", ".join(f"{k} x{v}" for k, v in why.most_common()))


def render(tasks: list[Task], attempts: list[Attempt],
           degraded: list[Attempt] | None = None) -> str:
    summaries = summarise(tasks, attempts)
    note = degraded_note(degraded or [])
    if not summaries:
        return "\n".join(filter(None, ["no attempts yet — run `orch agentbench run`", note]))
    lines = [f"{'arm':34} {'cond':9} {'resolved':>10} {'multi-file':>10} "
             f"{'broke code':>10} {'median':>7}"]
    for s in summaries:
        lines.append(
            f"{s.arm:34} {s.cond:9} {s.resolved:>3}/{s.n:<3} ({s.resolved / s.n:3.0%}) "
            f"{s.multi_file_resolved:>4}/{s.multi_file_n:<5} {s.broke_other_code:>10} "
            f"{s.median_minutes:>5.1f}m")
    if any(s.repeats > 1 for s in summaries):
        lines.append("")
        lines.append("stability across repeats (tasks solved ever / every time):")
        for s in summaries:
            lines.append(f"  {s.arm} / {s.cond}: {s.tasks_solved_ever}/{s.tasks} ever, "
                         f"{s.tasks_solved_always}/{s.tasks} always, up to {s.repeats} repeats")
    lines.append("")
    lines.append("failure modes: " + "  ".join(OUTCOMES[1:]))
    for s in summaries:
        lines.append(f"  {s.arm} / {s.cond}: " + "  ".join(
            f"{k}={s.outcomes[k]}" for k in OUTCOMES if k in s.outcomes))
    lines.append("")
    cols = [(s.arm, s.cond) for s in summaries]
    index: dict[tuple[str, str, str], list[Attempt]] = defaultdict(list)
    for a in attempts:
        index[(a.arm, a.cond, a.sha)].append(a)
    lines.append(f"{'task':10} {'files':>5} {'lines':>5}  " +
                 "".join(f"{i + 1:<15}" for i in range(len(cols))) + "subject")
    for t in tasks:
        cells = "".join(f"{_cell(index.get((arm, cond, t.sha), [])):<15}"
                        for arm, cond in cols)
        lines.append(f"{t.sha[:10]:10} {len(t.gold_src):>5} {t.gold_lines:>5}  {cells}"
                     f"{t.subject[:60]}")
    lines.append("")
    lines += [f"  {i + 1} = {arm} / {cond}" for i, (arm, cond) in enumerate(cols)]
    if note:
        lines += ["", note]
    return "\n".join(lines)
