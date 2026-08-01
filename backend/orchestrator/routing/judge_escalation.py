"""
judge_escalation.py — the 5c local→judge→escalate cascade on the LIVE serving path.

Phases 5a–5d proved Thesis A as a *bench*: `orch bench live-route` ran the
local→judge→cloud cascade on fresh inference and got 1.000 pass@1 at 22% of
always-cloud's cost (findings Era 14). That proof lived entirely in
`routing/live_route.py`, reachable only from the CLI. This module is the same
gate as a serving-path feature, so `/api/task` traffic gets it too.

## What it does

After a worker's output clears the executor's cheap validator, a free local
judge re-reads (prompt, output) and votes correct / incorrect from those two
things alone — no hidden tests, the production posture. An "incorrect" vote
routes the task to the next capable worker.

## The escalate-signal invariant (the whole design)

The judge is fallible in *both* directions, differently by task shape:

  - on code (Era 14, live) it is conservative — recall 6/6, but it needlessly
    escalated 4 of 50 correct answers;
  - on prose (Era 15) it is permissive — ref-accept 1.000, mutant-catch 0.733;
  - the tool-augmented variant (Era 18) can false-reject outright when the
    solver is *systematically* wrong (`pipes-tank` computed as -12).

So the gate is built so that a judge mistake can only ever cost money and
latency, never a correct answer the system already had:

  1. **A reject escalates; it never fails a task.** The verdict is routed
     through the executor's existing escalation path, not its failure path.
  2. **The judge is only consulted when escalation is actually possible.** With
     nowhere to escalate to, a reject could do nothing but block a task the
     validator already passed — so the call is skipped entirely (which also
     saves its latency).
  3. **The pre-escalation answer is kept as a fallback.** If the judge escalates
     a passing answer and the escalation target then fails outright, the
     executor serves the original rather than blocking (see
     `executor._JudgeFallback`).

Together those make the live gate's worst case the *measured* one: the Era-14
verification tax, paid in a needless escalation, with no quality downside.

## Deviation from `live_route.route_one` (deliberate, documented)

`route_one` treats every non-True verdict as "escalate", including one produced
by a failed judge call — correct for a bench, where a broken judge should stop
the run being scored as clean. On the serving path that would turn a dead
Ollama into a blanket reroute of all traffic, so an **errored** judge call
abstains here (log + keep local). An unparseable verdict from a judge that did
reply still escalates, matching 5c.

Off by default — it adds an LLM call to every task and changes which answer is
served. Enable with MAHORAGA_JUDGE_GATE=on.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from .judge_gate import GENERAL_RUBRIC, JUDGE_RUBRIC, judge_one

logger = logging.getLogger(__name__)

# Buckets whose outputs are code and therefore get the code-specific rubric.
# Mirrors execution_gate.EXEC_GATE_BUCKETS plus `security`, whose outputs are
# code-shaped too. Everything else grades under GENERAL_RUBRIC (Era 15).
CODE_RUBRIC_BUCKETS: frozenset[str] = frozenset(
    {"code", "test", "refactor", "debug", "security"}
)

# The 5c judge. An arm id (`qwen3.5`), a full worker id
# (`ollama:qwen3.5:general`), or a bare model tag all resolve.
DEFAULT_JUDGE_ARM = "qwen3.5"


def judge_gate_enabled() -> bool:
    """Off by default; MAHORAGA_JUDGE_GATE=on|1|true|yes enables it.

    Opposite default from the execution gate on purpose. The exec gate only
    rewrites the bandit's reward, while this gate changes *which answer the
    user gets* and adds a judge call to every task — a bigger blast radius than
    is warranted by bank measurements alone, until it has organic-traffic hours.
    """
    return os.getenv("MAHORAGA_JUDGE_GATE", "off").strip().lower() in (
        "1", "on", "true", "yes",
    )


def judge_arm() -> str:
    """Which arm judges. Override with MAHORAGA_JUDGE_MODEL."""
    return os.getenv("MAHORAGA_JUDGE_MODEL", "").strip() or DEFAULT_JUDGE_ARM


def rubric_for_bucket(bucket: str) -> str:
    """Code buckets grade under the code rubric, everything else under the general one."""
    return JUDGE_RUBRIC if bucket in CODE_RUBRIC_BUCKETS else GENERAL_RUBRIC


def select_judge_worker(registry, *, producer_worker_id: str = ""):
    """Resolve the configured judge to a registered local worker, or None.

    Prefers the `general` role of the configured arm (the role the bench judge
    used — no code-specific system framing). Falls back to any registered
    worker of that arm, then gives up rather than silently judging with an
    arbitrary model.

    Returns None when the judge can't be resolved; the caller treats that as
    "gate unavailable" and keeps the local answer.
    """
    arm = judge_arm()
    candidates = [w for w in registry.list_all() if _is_local_ollama(w.id)]
    if not candidates:
        return None

    exact = next((w for w in candidates if w.id == arm), None)
    if exact is not None:
        return exact

    same_arm = [w for w in candidates if _arm_of(w.id) == _normalize_arm(arm)]
    if not same_arm:
        logger.warning(
            "judge_gate: configured judge %r is not a registered local worker; "
            "gate inactive for this task", arm,
        )
        return None

    chosen = next((w for w in same_arm if w.id.endswith(":general")), same_arm[0])
    if producer_worker_id and _arm_of(producer_worker_id) == _arm_of(chosen.id):
        # 5c judged granite's output with qwen3.5. A model grading its own
        # output is a weaker signal than anything Era 14/15 measured, so say so
        # once per task rather than letting it pass as the measured setup.
        logger.warning(
            "judge_gate: judge %s and producer %s are the same arm — self-judging "
            "is not the configuration Era 14/15 measured", chosen.id, producer_worker_id,
        )
    return chosen


def _is_local_ollama(worker_id: str) -> bool:
    return worker_id.startswith("ollama:")


def _normalize_arm(name: str) -> str:
    """`ollama:qwen3.5:general` / `ollama:qwen3.5` / `qwen3.5` → `qwen3.5`."""
    parts = name.split(":")
    if parts and parts[0] == "ollama":
        parts = parts[1:]
    return parts[0] if parts else name


def _arm_of(worker_id: str) -> str:
    return _normalize_arm(worker_id)


async def should_escalate_by_judge(
    judge_worker, prompt: str, output: str, bucket: str,
) -> tuple[bool, Optional[bool], str]:
    """Ask the judge whether `output` answers `prompt`; decide escalation.

    Returns (escalate, verdict, reason):
      escalate — True to route the task on to the next capable worker
      verdict  — the raw judge vote (True correct / False incorrect / None
                 unparseable), for logging and audit
      reason   — short human-readable explanation

    Escalates on an explicit "incorrect" and on an unparseable reply (5c's safe
    default). Abstains — keeps the local answer — when the judge call itself
    errored, which is infrastructure failing, not evidence about the answer.
    """
    if not output.strip():
        return False, None, "empty output: nothing for the judge to grade"

    verdict, _cost, _raw, error = await judge_one(
        judge_worker, prompt, output, rubric=rubric_for_bucket(bucket),
    )

    if error:
        logger.warning("judge_gate: judge call failed (%s); keeping local answer", error)
        return False, None, f"judge unavailable: {error}"
    if verdict is True:
        return False, True, "judge: correct"
    if verdict is False:
        return True, False, "judge: incorrect"
    return True, None, "judge verdict unparseable; escalating (5c default)"
