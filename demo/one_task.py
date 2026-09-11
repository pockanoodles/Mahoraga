#!/usr/bin/env python3
"""Send one task to a running Mahoraga and print what the cascade did.

A demo helper, not a product surface — `orch` has no equivalent because the
serving path is meant to be called by an agent, not a person. Its only job is
to make one live request legible on screen: which arm answered, what the free
local judge decided, and whether that verdict cost anything.

Usage:  python3 demo/one_task.py "write a python function ..."

Requires `orch serve` (or the launchd daemon) on :8000 and Ollama up. A judged
rejection escalates to a paid arm, so this can spend real money — that is the
behaviour being demonstrated.
"""
from __future__ import annotations

import json
import sys
import urllib.request

URL = "http://localhost:8000/api/task"
BAR = "─" * 62


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 2
    prompt = sys.argv[1]

    body = json.dumps({"prompt": prompt}).encode()
    req = urllib.request.Request(
        URL, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            d = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        print(f"request failed: {exc}\nIs `orch serve` running on :8000?")
        return 1

    c = d.get("cascade") or {}
    r = d.get("routing") or {}
    escalated = bool(c.get("escalated"))
    correctness = c.get("judge_correctness")

    verdict = {1.0: "accepted", 0.0: "REJECTED"}.get(correctness, "abstained")

    print(BAR)
    print(f"  bucket / arm     {d.get('agent', '?')}")
    print(f"  strategy         {r.get('strategy', '?')}"
          f"   ucb {r.get('ucb_score', '?')}")
    print(f"  answered in      {d.get('elapsed_s', '?')}s")
    print(BAR)
    print(f"  local judge      {verdict}   (free, local, never sees tests)")
    if escalated:
        print(f"  escalated to     {c.get('escalated_to')}"
              f"   [{c.get('escalation_reason') or 'judge'}]")
        print(f"  escalation cost  ${c.get('escalation_cost_usd', 0.0):.4f}")
        print("  served answer    from the escalation arm")
    else:
        print("  escalated        no")
        print("  cost             $0.0000   — served locally")
    print(BAR)
    print(f"  escalations today {c.get('escalations_today', 0)}")
    print(BAR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
