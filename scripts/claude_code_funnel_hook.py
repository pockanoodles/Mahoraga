#!/usr/bin/env python3
"""Record the top of Mahoraga's delegation funnel from a Claude Code hook.

Every other measurement in this repo starts at `run_task`. That makes the
denominator invisible: `orch metrics usage` can say 93% of tasks that *reached*
Mahoraga were served locally, and cannot say whether that was 15 of 15 delegable
tasks or 15 of 200. Without the work that never arrived, "improve delegation" is
unfalsifiable — the same trap the Era-20 bandit null fell into.

This runs as a Claude Code `PostToolUse` hook and appends one line per
code-producing action to ~/.mahoraga-v2/funnel.jsonl. `orch metrics funnel`
reads it.

Design constraints, in order of importance:

  1. **It can never break a session.** Any failure — malformed payload, missing
     directory, unreadable disk — exits 0 silently. A measurement tool that can
     interrupt the work it measures will be uninstalled within a day, and then
     it measures nothing.
  2. **It must be cheap.** Stdlib only, no imports from the orchestrator (~20 ms
     versus ~500 ms to load the Typer CLI). Analysis lives in
     `routing/funnel_report.py`, where import cost is free.
  3. **It logs no file contents.** Only derived shape — path, extension, line
     and character counts. The log is local-only under ~/.mahoraga-v2/ and
     never enters the repo.

PostToolUse rather than PreToolUse, deliberately: it fires after the tool
actually succeeded, so a rejected or failed edit does not inflate the
denominator.

Install by adding to ~/.claude/settings.json:

    "PostToolUse": [{
      "matcher": "Write|Edit|mcp__mahoraga__run_task",
      "hooks": [{"type": "command", "async": true,
                 "command": "python3 /path/to/Mahoraga/scripts/claude_code_funnel_hook.py"}]
    }]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path.home() / ".mahoraga-v2" / "funnel.jsonl"

# What Mahoraga's local arms can actually take on. These bounds are the
# definition of "candidate", so they are named, not inlined: a delegation rate
# is only meaningful if the reader can see — and argue with — the denominator.
#
# Lower bound: below this, a ~10 s round trip loses to just writing it, so
# counting it as a missed delegation would manufacture a gap that should not be
# closed. Upper bound: a context-free 8B model does not reliably one-shot a file
# this large, so counting it would inflate the denominator with work that would
# have escalated anyway.
MIN_CANDIDATE_LINES = 5
MAX_CANDIDATE_LINES = 300

CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
    ".rb", ".php", ".c", ".h", ".cpp", ".hpp", ".cs", ".swift", ".scala",
    ".sh", ".bash", ".zsh", ".sql", ".lua", ".r", ".jl",
})

DELEGATION_TOOLS = frozenset({
    "mcp__mahoraga__run_task",
    "mcp__mahoraga__run_batch",
})


def _classify_inline(tool: str, tool_input: dict) -> tuple[bool, str, dict]:
    """Decide whether an inline action could plausibly have been delegated.

    Returns (is_candidate, reason_if_not, shape).

    This is deliberately an UPPER bound on delegable work: the hook cannot see
    whether the model needed conversation context to write this, so anything
    context-dependent is counted as a candidate when its shape fits. The
    resulting delegation rate is therefore a LOWER bound, which is the safe
    direction for a number that argues the tool is underused.
    """
    path = str(tool_input.get("file_path") or "")
    ext = Path(path).suffix.lower()

    if tool == "Edit":
        # A surgical change to an existing file is defined by the surrounding
        # code. Mahoraga's arms get a prompt and no repo, so this is not work
        # the cascade could have taken — excluding it keeps the denominator
        # honest rather than flattering.
        content = str(tool_input.get("new_string") or "")
        shape = {"lines": content.count("\n") + 1 if content else 0,
                 "chars": len(content)}
        return False, "edit-in-place", shape

    content = str(tool_input.get("content") or "")
    lines = content.count("\n") + 1 if content else 0
    shape = {"lines": lines, "chars": len(content)}

    if ext not in CODE_EXTENSIONS:
        return False, "non-code-file", shape
    if lines < MIN_CANDIDATE_LINES:
        return False, "below-round-trip-threshold", shape
    if lines > MAX_CANDIDATE_LINES:
        return False, "oversized-for-local-arm", shape
    return True, "", shape


def build_record(payload: dict) -> dict | None:
    """Turn a hook payload into one funnel row, or None if it is not funnel
    traffic. Pure, so the classification is testable without a live session."""
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": str(payload.get("session_id") or ""),
        "cwd": str(payload.get("cwd") or ""),
        "tool": tool,
    }

    if tool in DELEGATION_TOOLS:
        prompt = str(tool_input.get("prompt") or "")
        record.update({
            "event": "delegated",
            "path": "",
            "ext": "",
            "lines": 0,
            "chars": len(prompt),
            "candidate": True,
            "reason": "",
        })
        return record

    if tool not in ("Write", "Edit"):
        return None

    candidate, reason, shape = _classify_inline(tool, tool_input)
    path = str(tool_input.get("file_path") or "")
    record.update({
        "event": "inline",
        "path": path,
        "ext": Path(path).suffix.lower(),
        "lines": shape["lines"],
        "chars": shape["chars"],
        "candidate": candidate,
        "reason": reason,
    })
    return record


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        record = build_record(payload)
        if record is None:
            return 0
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:  # noqa: BLE001 — see constraint 1 in the module docstring
        if os.getenv("MAHORAGA_FUNNEL_DEBUG"):
            raise
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
