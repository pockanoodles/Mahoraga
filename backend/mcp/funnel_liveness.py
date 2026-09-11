"""funnel_liveness.py — record that the delegation tool was actually reachable.

The delegation funnel measures what share of delegable work reached Mahoraga.
On 2026-09-10 it produced its first reading — **0.0%, over 170 delegable
actions** — and the number was worthless, because `~/.claude.json` pointed the
MCP server at a bare `python` that does not exist on this machine. The server
never started, `run_task` was never listed, and the funnel spent four weeks
measuring an unplugged tool while looking exactly like a behavioural finding.

A rate cannot be read without knowing the tool was there. This module records
the one fact that distinguishes the two cases: **the MCP server successfully
served its tool list**, which is the moment `run_task` becomes visible to the
agent. Zero rows in a window means the rate is *uninterpretable*; rows plus no
delegations means the tool was available and went unused, which is a real
finding about behaviour.

Deliberately **stdlib-only and fire-and-forget**, for the same reasons
`scripts/claude_code_funnel_hook.py` is: this runs on the tool-discovery path,
so it must add no meaningful latency, pull in no heavy imports, and never
propagate an exception. A liveness probe that can break tool discovery would be
worse than no liveness probe.

The log path and event name are duplicated from `funnel_report.py` rather than
imported, to keep this module free of the orchestrator package — the same
tradeoff the hook makes. `tests/orchestrator_v2/test_funnel_liveness.py` pins
writer and reader to the same values so the duplication cannot drift.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path.home() / ".mahoraga-v2" / "funnel.jsonl"

# Read by funnel_report.compute_funnel. Not an "inline" or "delegated" event,
# so it never touches the numerator or the denominator — it only says whether
# the ratio can be read at all.
ALIVE_EVENT = "mcp-alive"


def record_alive(tool_count: int, log_path: Path | None = None) -> None:
    """Append one liveness row. Never raises, never blocks meaningfully.

    Called when the MCP server serves a tool list. `tool_count` is recorded
    because "served a list" and "served a list containing the delegation tool"
    are different claims, and a zero would be worth seeing.
    """
    if os.environ.get("MAHORAGA_FUNNEL_LIVENESS", "").strip().lower() in ("0", "off", "false"):
        return
    try:
        path = log_path or LOG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session": "",
            "event": ALIVE_EVENT,
            "tools": int(tool_count),
        }
        with path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001 — tool discovery must never fail for this
        pass
