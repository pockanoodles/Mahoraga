"""
reward_judge.py — free local judge verdict as the reward's correctness coefficient.

The exec gate's "ran without crashing" saturates at ~1.0 on live traffic, so the
composite reward's success term carried no gradient and the bandit chased the
faster arm (findings.md Era 20). This module asks a free local judge (Ollama,
same posture as live_route's escalation judge) whether the output is actually
correct, and the verdict becomes `TaskOutcome.correctness` — the coefficient on
the success term in `reward.RewardCalculator.compute`. The exec gate stays the
hard floor: a judge True never resurrects a crash, and a None (judge off,
unavailable, or unparseable) leaves the reward exactly at its legacy value.

Judge noise cannot invert the gradient — both arms face the same judge, so a
noisy verdict attenuates the true quality gap rather than flipping its sign.

All model calls go through the existing judge_gate/code_judge machinery (the
audited egress); this module adds no new SDK call sites. Disable with
MAHORAGA_REWARD_JUDGE=off; MAHORAGA_REWARD_JUDGE=code layers the recall-only
generated-test check on top of a base accept, exactly as live_route.route_one.
"""
from __future__ import annotations

import os

from ..workers.ollama import OllamaWorker
from .code_judge import differential_check
from .execution_gate import EXEC_GATE_BUCKETS
from .judge_gate import judge_one

# The judge rubric is only measured on code-like tasks — same surface as the
# exec gate, and drifting apart would silently change what "success" means.
REWARD_JUDGE_BUCKETS: frozenset[str] = EXEC_GATE_BUCKETS


def reward_judge_mode() -> str:
    """Return `off` | `on` | `code`; default on.

    Mirrors exec_gate_enabled's permissive parse: the off aliases 0/false/no
    count as off, and any unknown value falls back to on.
    """
    raw = os.getenv("MAHORAGA_REWARD_JUDGE", "on").strip().lower()
    if raw in ("0", "off", "false", "no"):
        return "off"
    if raw == "code":
        return "code"
    return "on"


_judge_worker: OllamaWorker | None = None


def _get_judge_worker() -> OllamaWorker:
    """Lazy module singleton — same construction as live_route.load_arms's judge."""
    global _judge_worker
    if _judge_worker is None:
        _judge_worker = OllamaWorker(
            model=os.environ.get("MAHORAGA_REWARD_JUDGE_MODEL", "qwen3.5"),
            worker_id="ollama:reward-judge",
            extra_payload={"think": False},
        )
    return _judge_worker


async def judge_correctness(
    task_prompt: str, output: str, *, thorough: bool = False
) -> tuple[float | None, float, str]:
    """Judge one served output; return (correctness, judge_cost, detail).

    correctness — 1.0 (judge accepts) / 0.0 (rejects) / None (abstain: judge
    unavailable or verdict unparseable — reward falls back to legacy exactly).
    In mode `code`, a base accept additionally runs the recall-only
    `differential_check`; its False flips the verdict, its exceptions degrade
    to an abstain (keep the base accept), mirroring live_route.route_one.

    `thorough=True` forces that generated-test check on for this call whatever
    the global mode says. It exists because the check's price is latency, not
    dollars, and the two differ by two orders of magnitude: measured live on a
    16 GB M-series box it adds ~265s to a task the local arm answered in ~6s
    (K sequential reference generations from a 9.7B judge, plus arm/judge model
    swap thrash — 5.3 GB + 6.6 GB do not coexist in 16 GB). That is fine for
    unattended batch work and unusable for interactive delegation, so the
    caller picks per task rather than the daemon picking once for everything.

    NEVER raises — the reward path must survive any judge failure.
    """
    cost = 0.0
    try:
        worker = _get_judge_worker()
        verdict, cost, _raw, error = await judge_one(worker, task_prompt, output)
        if error:
            return None, cost, f"judge unavailable: {error}"
        detail = ""
        if (thorough or reward_judge_mode() == "code") and verdict is True:
            try:
                tool_verdict, tool_cost, tool_detail = await differential_check(
                    worker, task_prompt, output
                )
            except Exception as exc:  # noqa: BLE001
                tool_verdict, tool_cost, tool_detail = None, 0.0, f"tool crashed: {exc!r}"
            cost += tool_cost
            if tool_verdict is False:
                verdict = False
                detail = f"code-judge override: {tool_detail}"
            else:
                detail = (
                    f"code-judge {'abstain' if tool_verdict is None else 'confirm'}: {tool_detail}"
                )
        if verdict is None:
            return None, cost, detail or "judge verdict unparseable"
        return (1.0 if verdict else 0.0), cost, detail
    except Exception as exc:  # noqa: BLE001
        return None, cost, f"judge unavailable: {exc!r}"
