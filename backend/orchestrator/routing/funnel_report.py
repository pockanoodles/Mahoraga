"""funnel_report.py — how much delegable work actually reaches Mahoraga.

`usage_report.py` answers "of the work sent to Mahoraga, how much did a free
local model handle". This answers the question one step earlier and strictly
harder: **of the work that could have been sent, how much was?** The cascade
saves nothing on a task that never arrives, so this is the metric that bounds
every other number in the repo.

It reads the log written by `scripts/claude_code_funnel_hook.py`. Both the
numerator (delegations) and the denominator (inline code-producing actions) come
from that single log, on purpose: joining delegations from the decisions DB
against inline actions from the hook would mix two populations — the DB records
all organic traffic, the hook only sees Claude Code sessions — and produce a
ratio whose two halves describe different worlds.

**The rate is a lower bound, and the report says so.** A hook cannot see whether
the model needed conversation context to write a file, so the candidate rule
counts anything whose *shape* fits (see `MIN/MAX_CANDIDATE_LINES` in the hook).
That over-counts the denominator, which pushes the measured delegation rate
down. For a number whose job is to argue the tool is underused, erring toward
"you delegate less than this" is the safe direction — an over-stated rate would
quietly retire a problem that is still there.

Exclusions are reported with their reason rather than silently dropped, so the
definition of "delegable" is auditable and arguable instead of asserted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .time_window import within_window

DEFAULT_LOG = Path.home() / ".mahoraga-v2" / "funnel.jsonl"

# Written by backend/mcp/funnel_liveness.py when the MCP server serves its tool
# list. Duplicated rather than imported to keep that writer free of this
# package (it runs on the tool-discovery path); the two are pinned to the same
# value by tests/orchestrator_v2/test_funnel_liveness.py.
ALIVE_EVENT = "mcp-alive"

# Printed with each exclusion so the denominator can be argued with.
_REASON_NOTES = {
    "edit-in-place": "surgical change to an existing file; the arms get no repo",
    "non-code-file": "docs, config, data",
    "below-round-trip-threshold": "faster to write than to round-trip",
    "oversized-for-local-arm": "beyond a context-free 8B's reliable one-shot",
}


@dataclass
class FunnelReport:
    delegated: int = 0
    candidates: int = 0
    inline_total: int = 0
    excluded_by_reason: dict[str, int] = field(default_factory=dict)
    sessions: int = 0
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    mcp_alive: int = 0
    last_alive_ts: Optional[str] = None

    @property
    def delegable(self) -> int:
        """Work the cascade could plausibly have taken: delegated + missed."""
        return self.delegated + self.candidates

    @property
    def delegation_rate(self) -> Optional[float]:
        """Share of delegable work that actually reached Mahoraga.

        None when nothing delegable was observed — a rate over an empty
        denominator is not 0%, it is unknown, and printing 0% would read as a
        finding rather than an absence of data.
        """
        if self.delegable == 0:
            return None
        return self.delegated / self.delegable

    @property
    def interpretation(self) -> str:
        """Whether the rate can be read at all, and as what.

        `no-delegable-work` — nothing shaped like delegable work was seen, so
        there is no ratio.

        `tool-liveness-unknown` — delegable work was seen but the MCP server
        was never observed serving its tool list in this window, so a low rate
        cannot be distinguished from a tool that never loaded. This is the
        state the 2026-09-10 reading was in: 0.0% over 170 delegable actions,
        with the server dead the whole time from a bad interpreter path.

        `tool-available-unused` — the tool was demonstrably there and nothing
        was delegated. That is a real finding about behaviour, and the only
        case where a 0% rate means what it appears to mean.

        `measured` — a rate with a live tool behind it.
        """
        if self.delegable == 0:
            return "no-delegable-work"
        if self.mcp_alive == 0:
            return "tool-liveness-unknown"
        if self.delegated == 0:
            return "tool-available-unused"
        return "measured"

    def to_dict(self) -> dict[str, Any]:
        return {
            "delegated": self.delegated,
            "candidates_missed": self.candidates,
            "delegable": self.delegable,
            "delegation_rate": self.delegation_rate,
            "delegation_rate_is_lower_bound": True,
            "inline_total": self.inline_total,
            "excluded_by_reason": dict(self.excluded_by_reason),
            "sessions": self.sessions,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "interpretation": self.interpretation,
            "mcp_alive_observations": self.mcp_alive,
            "last_alive_ts": self.last_alive_ts,
        }


def compute_funnel(
    log_path: Path = DEFAULT_LOG,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> FunnelReport:
    """Aggregate the hook log. A missing log is an empty report, not an error —
    the common case is "the hook is not installed yet"."""
    report = FunnelReport()
    if not log_path.is_file():
        return report

    sessions: set[str] = set()
    with log_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # A torn line from a crashed write is one lost observation, not
                # a reason to refuse to report on thousands of good ones.
                continue
            ts = str(row.get("ts") or "")
            if not within_window(ts, since, until):
                continue

            if ts:
                report.first_ts = ts if report.first_ts is None else min(report.first_ts, ts)
                report.last_ts = ts if report.last_ts is None else max(report.last_ts, ts)
            if row.get("session"):
                sessions.add(str(row["session"]))

            if row.get("event") == ALIVE_EVENT:
                # Not a delegation and not an inline action — it touches
                # neither half of the ratio, only whether the ratio is
                # readable.
                report.mcp_alive += 1
                if ts:
                    report.last_alive_ts = (
                        ts if report.last_alive_ts is None
                        else max(report.last_alive_ts, ts)
                    )
            elif row.get("event") == "delegated":
                report.delegated += 1
            elif row.get("event") == "inline":
                report.inline_total += 1
                if row.get("candidate"):
                    report.candidates += 1
                else:
                    reason = str(row.get("reason") or "unclassified")
                    report.excluded_by_reason[reason] = (
                        report.excluded_by_reason.get(reason, 0) + 1
                    )

    report.sessions = len(sessions)
    return report


def render_funnel(report: FunnelReport) -> str:
    lines: list[str] = []
    if report.delegated == 0 and report.inline_total == 0:
        lines.append("No funnel traffic recorded yet.")
        lines.append("")
        lines.append(
            "Install the hook to start measuring: `orch metrics funnel "
            "--install-hint` prints the config (see also `The delegation "
            "funnel` in README.md)."
        )
        return "\n".join(lines)

    window = ""
    if report.first_ts and report.last_ts:
        window = f"  {report.first_ts[:10]} → {report.last_ts[:10]}"
    plural = "session" if report.sessions == 1 else "sessions"
    lines.append(f"Delegation funnel{window}   ({report.sessions} {plural})")
    lines.append("")
    lines.append(f"  Delegable work observed   {report.delegable}")
    lines.append(f"    delegated to Mahoraga   {report.delegated}")
    lines.append(f"    handled inline          {report.candidates}")
    lines.append("")

    rate = report.delegation_rate
    if rate is None:
        lines.append("  Delegation rate           unknown (no delegable work seen)")
    else:
        lines.append(f"  Delegation rate           {rate:.1%}  (lower bound)")

    alive_when = f", last {report.last_alive_ts[:16]}" if report.last_alive_ts else ""
    lines.append(
        f"  Tool liveness             {report.mcp_alive} observations{alive_when}"
    )
    lines.append("")

    if report.interpretation == "tool-liveness-unknown":
        lines.append(
            "  !! THIS RATE IS NOT READABLE. The MCP server was never observed\n"
            "     serving its tool list in this window, so a low rate cannot be\n"
            "     told apart from a tool that never loaded. That is exactly what\n"
            "     the 0.0% reading of 2026-09-10 turned out to be — four weeks of\n"
            "     a dead interpreter path, not four weeks of not delegating.\n"
            "     Check that `run_task` is available, then re-read this over a\n"
            "     window that starts after it is."
        )
        lines.append("")
    elif report.interpretation == "tool-available-unused":
        lines.append(
            "  The tool was demonstrably available in this window and nothing was\n"
            "  delegated. Unlike a liveness-unknown 0%, this one means what it\n"
            "  looks like."
        )
        lines.append("")

    if report.excluded_by_reason:
        lines.append(f"  Inline actions not counted as delegable "
                     f"({report.inline_total - report.candidates} of {report.inline_total}):")
        for reason, n in sorted(report.excluded_by_reason.items(), key=lambda kv: -kv[1]):
            note = _REASON_NOTES.get(reason, "")
            suffix = f"  — {note}" if note else ""
            lines.append(f"    {reason:<28} {n:>4}{suffix}")
        lines.append("")

    lines.append(
        "  The rate is a LOWER bound: the hook cannot tell whether a file needed\n"
        "  conversation context to write, so the denominator counts everything\n"
        "  shaped like delegable work. Real delegable volume is at most this."
    )
    return "\n".join(lines)


INSTALL_HINT = """\
Add this to ~/.claude/settings.json (merge into an existing "hooks" block):

  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit|mcp__mahoraga__run_task",
        "hooks": [
          {
            "type": "command",
            "async": true,
            "command": "python3 %s"
          }
        ]
      }
    ]
  }

`async: true` matters: the recorder must never sit between you and your own
tool call. It writes to ~/.mahoraga-v2/funnel.jsonl, logs no file contents, and
exits 0 on any failure.
"""


def install_hint(script_path: Path) -> str:
    return INSTALL_HINT % script_path
