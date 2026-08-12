"""
usage_report.py — what the cascade actually did for the person running it.

Every other measurement in this repo is a benchmark: a fixed bank, an oracle,
a policy comparison. This one answers a different question, the only one that
justifies running Mahoraga daily rather than admiring it — **of the real work
sent to it, how much did a free local model handle, and what did that avoid?**

The counterfactual is deliberately self-calibrating. Rather than pricing local
rows off a published rate table (which `bench report cost` does, and which its
own docstring calls a floor — bare token pricing misses the cache-creation that
dominates a real CLI call), the baseline here is **the escalation arm's own
measured per-task cost on this machine**. Every escalation records what it
actually charged; the mean of those is what a kept-local task would plausibly
have cost had it gone to the same arm. No price table, no assumed model, no
extrapolation from someone else's hardware — the denominator is measured on the
same box, in the same period, by the same arm.

Two consequences worth stating plainly, because they bound every number here:

  - with no escalations in the window there is no measured rate, so the report
    reports avoided spend as unknown rather than guessing;
  - it is a *substitution* baseline ("what if this had gone to the cloud arm"),
    NOT a claim about what the same task would have cost inside an interactive
    session, which carries conversation context and is far more expensive.
    The honest sentence this supports is "N% of tasks served locally at zero
    marginal cost", not "saved N% of my total spend".

Bench traffic is excluded: rows carrying a `bench_run_id` are experiments, not
usage, and mixing them would let a 200-task forced-explore run swamp a month of
real work.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Escalation reasons recorded by the serving path.
REASON_EXEC_GATE = "exec_gate"
REASON_JUDGE = "judge"


@dataclass
class UsageReport:
    """Organic-traffic summary over a window. Counts are tasks, costs are USD."""

    since: Optional[str] = None
    until: Optional[str] = None
    total_tasks: int = 0
    served_local: int = 0
    escalated: int = 0
    escalated_by_reason: dict[str, int] = field(default_factory=dict)
    judge_accepted: int = 0
    judge_rejected: int = 0
    judge_abstained: int = 0
    escalation_spend: float = 0.0
    measured_task_rate: Optional[float] = None
    avoided_spend: Optional[float] = None
    by_agent: dict[str, int] = field(default_factory=dict)

    @property
    def local_share(self) -> float:
        return self.served_local / self.total_tasks if self.total_tasks else 0.0

    @property
    def escalation_rate(self) -> float:
        return self.escalated / self.total_tasks if self.total_tasks else 0.0

    @property
    def cost_reduction(self) -> Optional[float]:
        """Fraction of the counterfactual all-cloud bill that was not paid."""
        if self.avoided_spend is None:
            return None
        baseline = self.avoided_spend + self.escalation_spend
        return self.avoided_spend / baseline if baseline else None

    def to_dict(self) -> dict:
        return {
            "since": self.since,
            "until": self.until,
            "total_tasks": self.total_tasks,
            "served_local": self.served_local,
            "escalated": self.escalated,
            "escalated_by_reason": self.escalated_by_reason,
            "local_share": round(self.local_share, 4),
            "escalation_rate": round(self.escalation_rate, 4),
            "judge": {
                "accepted": self.judge_accepted,
                "rejected": self.judge_rejected,
                "abstained": self.judge_abstained,
            },
            "escalation_spend_usd": round(self.escalation_spend, 4),
            "measured_task_rate_usd": (
                round(self.measured_task_rate, 6)
                if self.measured_task_rate is not None else None
            ),
            "avoided_spend_usd": (
                round(self.avoided_spend, 4) if self.avoided_spend is not None else None
            ),
            "cost_reduction": (
                round(self.cost_reduction, 4) if self.cost_reduction is not None else None
            ),
            "by_agent": self.by_agent,
        }


def _has_cascade_columns(conn: sqlite3.Connection) -> bool:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    return "escalated_to" in cols


def compute_usage(
    db_path: Path,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> UsageReport:
    """Summarise organic routed traffic in [since, until] (ISO dates, inclusive).

    Only rows with a recorded outcome count: a decision whose `success` is still
    NULL never finished, so counting it would inflate the denominator with tasks
    that were never served at all.
    """
    report = UsageReport(since=since, until=until)
    conn = sqlite3.connect(str(db_path))
    try:
        if not _has_cascade_columns(conn):
            return report

        where = ["bench_run_id IS NULL", "success IS NOT NULL"]
        params: list = []
        if since:
            where.append("date(timestamp) >= date(?)")
            params.append(since)
        if until:
            where.append("date(timestamp) <= date(?)")
            params.append(until)
        clause = " AND ".join(where)

        rows = conn.execute(
            f"""
            SELECT selected_agent, correctness, escalated_to, escalation_cost,
                   escalation_reason
            FROM decisions
            WHERE {clause}
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    for agent, correctness, escalated_to, esc_cost, reason in rows:
        report.total_tasks += 1
        report.by_agent[agent or "unknown"] = report.by_agent.get(agent or "unknown", 0) + 1

        if correctness is None:
            report.judge_abstained += 1
        elif correctness >= 1.0:
            report.judge_accepted += 1
        else:
            report.judge_rejected += 1

        if escalated_to:
            report.escalated += 1
            report.escalation_spend += float(esc_cost or 0.0)
            key = reason or "unknown"
            report.escalated_by_reason[key] = report.escalated_by_reason.get(key, 0) + 1
        else:
            report.served_local += 1

    # The counterfactual rate is measured, not assumed: what the escalation arm
    # actually charged per task in this same window. Escalations with a zero
    # recorded cost are excluded — a free or unpriced call says nothing about
    # what a paid one costs, and averaging them in would understate the baseline.
    priced = [
        float(c) for _a, _cor, esc, c, _r in rows if esc and float(c or 0.0) > 0.0
    ]
    if priced:
        report.measured_task_rate = sum(priced) / len(priced)
        report.avoided_spend = report.measured_task_rate * report.served_local

    return report


def render_usage(report: UsageReport) -> str:
    """Human-readable summary. Deliberately states what is NOT measured."""
    r = report
    window = " → ".join(x for x in (r.since, r.until) if x) or "all recorded traffic"
    lines = [
        f"Organic usage — {window}",
        "",
        f"  Tasks routed        {r.total_tasks}",
    ]
    if r.total_tasks == 0:
        lines += [
            "",
            "  No organic traffic recorded yet. Bench rows are excluded by",
            "  design; route real work through /api/task or the MCP run_task",
            "  tool and re-run this.",
        ]
        return "\n".join(lines)

    lines += [
        f"    served locally    {r.served_local}  ({r.local_share:.1%})",
        f"    escalated         {r.escalated}  ({r.escalation_rate:.1%})",
    ]
    for reason, count in sorted(r.escalated_by_reason.items()):
        label = {
            REASON_EXEC_GATE: "did not execute",
            REASON_JUDGE: "judge rejected",
        }.get(reason, reason)
        lines.append(f"      {label:<18} {count}")

    lines += [
        "",
        "  Judge verdicts",
        f"    accepted          {r.judge_accepted}",
        f"    rejected          {r.judge_rejected}",
        f"    abstained         {r.judge_abstained}   (judge off, unavailable, or task failed)",
        "",
        "  Spend",
        f"    escalations       ${r.escalation_spend:.4f}  (actual, measured)",
    ]

    if r.measured_task_rate is None:
        lines += [
            "    avoided           unknown — no priced escalation in this window,",
            "                      so there is no measured per-task rate to",
            "                      price the locally-served tasks against.",
        ]
    else:
        lines += [
            f"    measured rate     ${r.measured_task_rate:.4f}/task "
            f"(n={sum(r.escalated_by_reason.values())} escalations, this machine)",
            f"    avoided           ${r.avoided_spend:.4f}  "
            f"({r.served_local} tasks × measured rate)",
        ]
        if r.cost_reduction is not None:
            lines.append(f"    cost reduction    {r.cost_reduction:.1%} vs all-cloud")

    lines += [
        "",
        "  Baseline is substitution: what these tasks would have cost on the",
        "  escalation arm. It is NOT a measure of interactive-session spend,",
        "  which carries conversation context and costs considerably more.",
    ]
    return "\n".join(lines)
