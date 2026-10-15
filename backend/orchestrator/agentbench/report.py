"""Summarise a bench's attempts: per-arm totals and a per-task matrix."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .bank import Task
from .runner import Attempt
from .stats import Plan, Rate, plan, task_rate, time_bound

# The precision the report plans toward: ±10 points is the coarsest interval
# that still separates "local takes a tenth of the work" from "a third".
TARGET_HALF_WIDTH = 0.10

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
    # Task-weighted rate and its 95% Wilson interval over tasks (stats.py).
    # This, not resolved/n, is what the verdict reads.
    rate: float = 0.0
    rate_lo: float = 0.0
    rate_hi: float = 1.0
    tier: str = "test-verified"
    # Failed attempts with a reply cut off at the generation cap: out of
    # budget rather than wrong, though both count as failures.
    capped: int = 0
    # Failed attempts that ran out of time, and the limit they ran into.
    timed_out: int = 0
    limit_minutes: float = 0.0

    @property
    def interval(self) -> Rate:
        return Rate(self.rate, self.rate_lo, self.rate_hi, self.tasks, self.n)

    @property
    def time_bound(self) -> bool:
        return time_bound(self.timed_out, self.n - self.resolved)


def time_warning(s: ArmSummary) -> str | None:
    """Said where the rate is read, since a rate the clock cut short reads
    like a model that can't do the work."""
    if not s.time_bound:
        return None
    return (f"WARNING: {s.arm} / {s.cond}: {s.timed_out} of its {s.n - s.resolved} failed "
            f"attempts ran out of time at the {s.limit_minutes:.0f}-minute limit before the "
            "model finished. The rate is a floor set by how fast this machine reads long "
            "prompts. To measure the model, rerun with a longer --timeout or a smaller @ctx.")


def precision_plan(s: ArmSummary) -> Plan:
    """What it takes to narrow this arm's interval to ±TARGET_HALF_WIDTH,
    counting one arm and condition at a time."""
    return plan(s.interval, TARGET_HALF_WIDTH, s.median_minutes or 1.0, cells=1)


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
        r = task_rate(per_task)
        late = [a for a in rows if a.timed_out and not a.resolved]
        tiers = {a.tier for a in rows}
        if len(tiers) > 1:  # grade.py: tiers are evidence of different kinds
            raise ValueError(f"{arm}/{cond} mixes grading tiers {sorted(tiers)}")
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
            rate=r.point, rate_lo=r.lo, rate_hi=r.hi, tier=tiers.pop(),
            capped=sum(not a.resolved and a.extra.get("capped_calls", 0) > 0 for a in rows),
            timed_out=len(late),
            limit_minutes=round(max((a.secs for a in late), default=0) / 60, 1),
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
    """Attempts thrown out because they did not measure the agent — the machine
    was unhealthy, or the agent crashed — and why. They are in no count above."""
    if not degraded:
        return None
    why = Counter(r.split(":")[0].split(" (")[0] for a in degraded for r in a.degraded)
    return (f"{len(degraded)} degraded attempts excluded (did not measure the agent): " +
            ", ".join(f"{k} x{v}" for k, v in why.most_common()))


def _reason(r: str) -> str:
    return r.split(":")[0].split(" (")[0]


@dataclass
class Ledger:
    """Everything that went into one arm's number, and everything kept out."""
    arm: str
    cond: str
    counted: int
    excluded: dict[str, int]   # reason -> tries thrown out
    dropped: list[str]         # runs that gave up on the arm, and why
    receipts: list[str]        # one line per distinct setup that produced counted attempts
    warnings: list[str]


def _setup(m: dict, arm: str) -> str:
    """One line of what a run measured an arm with."""
    agent, _, model = arm.partition(":")
    model = model.split("@")[0]
    digest = (m.get("models", {}).get("digests") or {}).get(model, "?")
    h, hw, t = m.get("harness", {}), m.get("machine", {}), m.get("tasks", {})
    commit = (h.get("commit") or "")[:9] or "installed"
    version = str((m.get("agents") or {}).get(agent, "?"))
    version = version[len(agent):].strip() if version.startswith(agent) else version
    # The prompts, not the task file: sealing a clone rewrites each task's base
    # commit and so the file's hash, while the tasks an agent sees stay the same.
    return (f"{model} @ {digest.split(':')[-1][:12]}, {agent} {version}, "
            f"ollama {m.get('models', {}).get('ollama', '?')}, "
            f"mahoraga {h.get('version', '?')} {commit}{' (dirty)' if h.get('dirty') else ''}, "
            f"{hw.get('model', '?')} {hw.get('memory_gb', '?')} GB, "
            f"prompts {str(t.get('prompts_sha256', '?'))[:12]}")


def ledgers(attempts: list[Attempt], degraded: list[Attempt],
            manifests: list[dict]) -> list[Ledger]:
    by_run = {m["run_id"]: m for m in manifests}
    keys = sorted({(a.arm, a.cond) for a in [*attempts, *degraded]})
    out = []
    for arm, cond in keys:
        mine = [a for a in attempts if (a.arm, a.cond) == (arm, cond)]
        thrown = [a for a in degraded if (a.arm, a.cond) == (arm, cond)]
        runs = {a.run_id for a in [*mine, *thrown]}
        setups = Counter(_setup(by_run[a.run_id], arm) if a.run_id in by_run
                         else "unrecorded: the attempt predates run manifests" for a in mine)
        digests = {(by_run[r].get("models", {}).get("digests") or {}).get(
            arm.partition(":")[2].split("@")[0]) for r in runs if r in by_run} - {None}
        warnings = []
        if len(digests) > 1:
            warnings.append(f"the model's weights changed between runs ({len(digests)} digests)")
        if any(by_run[r].get("harness", {}).get("dirty") for r in runs if r in by_run):
            warnings.append("measured with uncommitted harness changes")
        out.append(Ledger(
            arm=arm, cond=cond, counted=len(mine),
            excluded=dict(Counter(_reason(r) for a in thrown for r in a.degraded).most_common()),
            dropped=[by_run[r]["dropped"][arm] for r in sorted(runs)
                     if r in by_run and arm in (by_run[r].get("dropped") or {})],
            receipts=[f"{n} attempts: {s}" for s, n in setups.most_common()],
            warnings=warnings))
    return out


def render_ledgers(ls: list[Ledger]) -> list[str]:
    lines = ["receipts (what each number was measured with, and what was kept out):"]
    for g in ls:
        lines.append(f"  {g.arm} / {g.cond}: {g.counted} counted")
        lines += [f"    {r}" for r in g.receipts]
        if g.excluded:
            lines.append("    excluded, did not measure the model: " +
                         ", ".join(f"{k} x{v}" for k, v in g.excluded.items()))
        lines += [f"    dropped: {d}" for d in g.dropped]
        lines += [f"    WARNING: {w}" for w in g.warnings]
    return lines


def render(tasks: list[Task], attempts: list[Attempt],
           degraded: list[Attempt] | None = None,
           manifests: list[dict] | None = None) -> str:
    summaries = summarise(tasks, attempts)
    note = degraded_note(degraded or [])
    if not summaries:
        return "\n".join(filter(None, ["no attempts yet — run `mahoraga audit run`", note]))
    lines = [f"{'arm':34} {'cond':9} {'resolved':>10} {'rate [95% CI, by task]':>23} "
             f"{'multi-file':>10} {'broke code':>10} {'median':>7}"]
    for s in summaries:
        lines.append(
            f"{s.arm:34} {s.cond:9} {s.resolved:>4}/{s.n:<5} {s.interval.fmt():>23} "
            f"{s.multi_file_resolved:>4}/{s.multi_file_n:<5} {s.broke_other_code:>10} "
            f"{s.median_minutes:>5.1f}m")
    lines.append(f"(grading tier: {', '.join(sorted({s.tier for s in summaries}))}; "
                 "intervals count tasks, not attempts)")
    lines += [w for s in summaries if (w := time_warning(s))]
    if any(s.repeats > 1 for s in summaries):
        lines.append("")
        lines.append("stability across repeats (tasks solved ever / every time):")
        for s in summaries:
            lines.append(f"  {s.arm} / {s.cond}: {s.tasks_solved_ever}/{s.tasks} ever, "
                         f"{s.tasks_solved_always}/{s.tasks} always, up to {s.repeats} repeats")
    lines.append("")
    lines.append(f"precision (toward ±{TARGET_HALF_WIDTH:.0%}):")
    for s in summaries:
        lines.append(f"  {s.arm} / {s.cond}: ±{s.interval.half_width:.0%} now. "
                     f"{precision_plan(s).fmt()}")
    lines.append("")
    lines.append("failure modes: " + "  ".join(OUTCOMES[1:]))
    for s in summaries:
        lines.append(f"  {s.arm} / {s.cond}: " + "  ".join(
            f"{k}={s.outcomes[k]}" for k in OUTCOMES if k in s.outcomes) +
            (f"  ({s.capped} of the failures hit the generation cap)" if s.capped else "") +
            (f"  ({s.timed_out} of the failures ran out of time)" if s.timed_out else ""))
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
    if manifests is not None:
        live = {t.sha for t in tasks}
        lines += [""] + render_ledgers(ledgers([a for a in attempts if a.sha in live],
                                               degraded or [], manifests))
    elif note:
        lines += ["", note]
    return "\n".join(lines)
