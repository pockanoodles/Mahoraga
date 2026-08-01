"""
judge_live_report.py — what the judge gate actually does on ORGANIC traffic.

Phases 5c and 5d measured the judge on curated banks: 50 gold code prompts with
hidden tests (Era 14) and 30 authored reference/mutant pairs (Era 15). Both
supplied ground truth, which is what made accuracy, recall, and mutant-catch
computable. Organic `/api/task` traffic supplies none of that.

So this module deliberately measures the gate's **operating point**, not its
accuracy:

  - how often it fires (the live analogue of Era 14's 10-escalations-in-50),
  - where it fires (per bucket — Era 15 predicts prose behaves differently from
    code, since the same judge was permissive on one and conservative on the
    other),
  - what it costs (judge latency, paid on every judged task whether it escalates
    or not — on a serving path that tax is time, not tokens),
  - how often the escalate-signal invariant actually caught something (fallbacks
    served, i.e. escalations that went nowhere and would have blocked a task
    under a hard-reject design).

**What this canNOT tell you:** whether an escalation was *right*. Era 14's
"4 of 50 needless" figure came from grading against hidden tests. Nothing here
substitutes for that, and a live escalation rate near 20% is not evidence the
gate is behaving well — only that it is behaving *similarly*. Divergence is the
signal worth acting on; agreement is weak confirmation.

The Era-14 reference point below is the live-route run (50 verifiable code
prompts, granite judged by qwen3.5): 10 escalations, 6 of them real failures.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Era 14 (`orch bench live-route`, bench_run mode=live-route): granite's 50
# outputs judged by a free local qwen3.5. 10 escalations, of which 6 were real
# failures and 4 were needless. Code bucket only.
ERA14_ESCALATION_RATE = 10 / 50
ERA14_NEEDLESS_RATE = 4 / 50
ERA14_LABEL = "Era 14 (5c live, 50 code prompts)"


@dataclass
class Cell:
    """Aggregates for one slice (overall, or one bucket)."""
    judged: int = 0
    escalated: int = 0
    verdict_correct: int = 0
    verdict_incorrect: int = 0
    verdict_unparseable: int = 0
    abstained: int = 0            # judge errored or had nothing to grade
    served_fallback: int = 0
    judge_ms: list[float] = field(default_factory=list)

    @property
    def escalation_rate(self) -> float:
        return self.escalated / self.judged if self.judged else 0.0

    @property
    def fallback_rate(self) -> float:
        """Share of escalations that went nowhere and fell back to the original."""
        return self.served_fallback / self.escalated if self.escalated else 0.0

    @property
    def mean_judge_ms(self) -> float:
        return sum(self.judge_ms) / len(self.judge_ms) if self.judge_ms else 0.0

    def p90_judge_ms(self) -> float:
        if not self.judge_ms:
            return 0.0
        ordered = sorted(self.judge_ms)
        # Nearest-rank p90; on tiny n this is just the max, which is honest.
        idx = min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))
        return ordered[idx]


@dataclass
class LiveJudgeSummary:
    overall: Cell
    per_bucket: dict[str, Cell]
    per_agent: dict[str, Cell]
    first_seen: str = ""
    last_seen: str = ""

    @property
    def escalation_delta(self) -> float:
        """Live escalation rate minus Era 14's. Positive = escalating more."""
        return self.overall.escalation_rate - ERA14_ESCALATION_RATE


def load_events(
    db_path: Path,
    *,
    since: Optional[str] = None,
    bucket: Optional[str] = None,
) -> list[dict]:
    """Read judge_gate_events rows. Returns [] if the table doesn't exist yet.

    A missing table is the normal state before the gate has ever run, not an
    error — the caller reports "no data" rather than failing.
    """
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "judge_gate_events" not in tables:
            return []
        sql = "SELECT * FROM judge_gate_events"
        clauses: list[str] = []
        params: list = []
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if bucket:
            clauses.append("bucket = ?")
            params.append(bucket)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id ASC"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _accumulate(cell: Cell, row: dict) -> None:
    cell.judged += 1
    verdict = row.get("verdict")
    escalated = bool(row.get("escalated"))
    if escalated:
        cell.escalated += 1
    if bool(row.get("served_fallback")):
        cell.served_fallback += 1
    if verdict == 1:
        cell.verdict_correct += 1
    elif verdict == 0:
        cell.verdict_incorrect += 1
    elif escalated:
        # No verdict but escalated → the judge replied unparseably (5c default).
        cell.verdict_unparseable += 1
    else:
        # No verdict and no escalation → the call errored or there was nothing
        # to grade, and the gate abstained. Distinct from a "correct" vote.
        cell.abstained += 1
    ms = row.get("judge_ms")
    if ms is not None:
        cell.judge_ms.append(float(ms))


def summarize(rows: list[dict]) -> LiveJudgeSummary:
    """Aggregate raw event rows into overall / per-bucket / per-agent cells."""
    overall = Cell()
    per_bucket: dict[str, Cell] = {}
    per_agent: dict[str, Cell] = {}
    for row in rows:
        _accumulate(overall, row)
        _accumulate(per_bucket.setdefault(row.get("bucket") or "?", Cell()), row)
        _accumulate(per_agent.setdefault(row.get("judged_agent") or "?", Cell()), row)
    return LiveJudgeSummary(
        overall=overall,
        per_bucket=per_bucket,
        per_agent=per_agent,
        first_seen=(rows[0].get("timestamp") or "") if rows else "",
        last_seen=(rows[-1].get("timestamp") or "") if rows else "",
    )


def as_dict(summary: LiveJudgeSummary) -> dict:
    """JSON-friendly view, for `--json` and for pasting into the brain."""

    def cell(c: Cell) -> dict:
        return {
            "judged": c.judged,
            "escalated": c.escalated,
            "escalation_rate": round(c.escalation_rate, 4),
            "verdict_correct": c.verdict_correct,
            "verdict_incorrect": c.verdict_incorrect,
            "verdict_unparseable": c.verdict_unparseable,
            "abstained": c.abstained,
            "served_fallback": c.served_fallback,
            "fallback_rate": round(c.fallback_rate, 4),
            "mean_judge_ms": round(c.mean_judge_ms, 1),
            "p90_judge_ms": round(c.p90_judge_ms(), 1),
        }

    return {
        "window": {"first_seen": summary.first_seen, "last_seen": summary.last_seen},
        "overall": cell(summary.overall),
        "per_bucket": {k: cell(v) for k, v in sorted(summary.per_bucket.items())},
        "per_agent": {k: cell(v) for k, v in sorted(summary.per_agent.items())},
        "baseline": {
            "label": ERA14_LABEL,
            "escalation_rate": round(ERA14_ESCALATION_RATE, 4),
            "needless_rate": round(ERA14_NEEDLESS_RATE, 4),
        },
        "escalation_delta_vs_era14": round(summary.escalation_delta, 4),
        "caveat": (
            "Operating point only. Organic traffic has no ground truth, so "
            "accuracy/recall are NOT computed here — divergence from the Era-14 "
            "rate is the signal, agreement is weak confirmation."
        ),
    }
