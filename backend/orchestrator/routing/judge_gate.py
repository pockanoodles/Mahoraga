"""
judge_gate.py — LLM-as-judge escalation gate for offline route simulation.

Grades a single candidate solution as correct / incorrect from the task prompt
and the output ALONE — no hidden tests, which is the production posture (in a
live system you don't have gold tests for organic traffic). The verdict is used
as `route_sim.simulate`'s `local_solved` gate: "did the local arm solve it, or
escalate to cloud?" The judge call goes through the audited cloud egress
(`ClaudeCliWorker`, Max subscription, no API key), and returns its own per-call
cost so the judge's price can be charged into the routed cost.

Prior-art warning (findings.md Era 7): every LLM judge tried so far shared the
heuristic scorer's elaboration / length bias. The rubric below inherits
`experiments/llm_judge.py`'s explicit anti-length instruction; whether a
single-output correctness judge beats the (near-useless) heuristic gate is the
open question 5b answers.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Optional

from ..domain.models import Task, TaskAttempt, TaskStatus, AttemptStatus

JUDGE_RUBRIC = (
    "You are a strict correctness judge for code. You are given a programming "
    "task and ONE candidate solution. Decide whether the candidate is CORRECT: "
    "does the code, exactly as written, actually solve the task for all "
    "reasonable inputs, including edge cases?\n\n"
    "Judge correctness ONLY. Do NOT reward length, comments, structure, or "
    "explanation — extra prose or defensive boilerplate never makes a wrong or "
    "incomplete solution correct, and a short correct answer beats a long wrong "
    "one. If the code has a bug, wrong logic, a wrong function name/signature, or "
    "would return/raise the wrong thing on any valid input, it is INCORRECT.\n\n"
    'Respond with ONLY a one-line JSON object: {"correct": true|false, '
    '"reason": "<=15 words"}'
)

# General-purpose correctness rubric for NON-VERIFIABLE tasks (explain, reason,
# summarize, factual, instruct) — no hidden tests exist, so the judge grades
# substance from the response alone. Same anti-length framing as the code
# rubric: the failure mode we're fighting (findings Era 7) is judges rewarding
# fluency/elaboration instead of tracking whether the answer is actually right.
GENERAL_RUBRIC = (
    "You are a strict correctness judge. You are given a task and ONE candidate "
    "response. Decide whether the response is CORRECT and adequate: is it "
    "factually accurate, does its reasoning hold, and does it actually fulfill "
    "everything the task asked — the whole task, not just part of it?\n\n"
    "Judge substance ONLY. Do NOT reward length, fluency, confidence, hedging, "
    "or formatting — a long, eloquent, confident answer that is wrong or misses "
    "the point is INCORRECT, and a short plain answer that is right is CORRECT. "
    "If ANY factual claim is false, ANY reasoning step is invalid, the final "
    "conclusion is wrong, the response contradicts or invents information beyond "
    "what the task provided, or it ignores an explicit requirement of the task, "
    "it is INCORRECT.\n\n"
    'Respond with ONLY a one-line JSON object: {"correct": true|false, '
    '"reason": "<=15 words"}'
)


def build_judge_goal(
    task_prompt: str, candidate_output: str, *, rubric: str = JUDGE_RUBRIC
) -> str:
    """Assemble the grading prompt (rubric + task + candidate) for the worker.

    `rubric` defaults to the code rubric (`JUDGE_RUBRIC`); pass `GENERAL_RUBRIC`
    for non-verifiable tasks. The section headers are rubric-neutral so the same
    envelope works for both.
    """
    return (
        f"{rubric}\n\n"
        f"## Task\n{task_prompt}\n\n"
        f"## Candidate response\n{candidate_output}\n\n"
        "## Your verdict (JSON only)"
    )


_VERDICT_RE = re.compile(r'"correct"\s*:\s*(true|false)', re.IGNORECASE)


def parse_verdict(text: str) -> Optional[bool]:
    """Extract the boolean verdict from a judge reply; None if unparseable.

    Prefers the JSON field; falls back to an unambiguous bare true/false so a
    judge that drops the JSON wrapper still counts rather than silently voting.
    """
    m = _VERDICT_RE.search(text or "")
    if m:
        return m.group(1).lower() == "true"
    t = (text or "").strip().lower()
    has_t, has_f = "true" in t, "false" in t
    if has_t and not has_f:
        return True
    if has_f and not has_t:
        return False
    return None


def _make_judge_task(goal: str) -> Task:
    now = time.time()
    return Task(
        id=f"judge-{uuid.uuid4().hex[:12]}",
        run_id="judge-replay",
        parent_task_id=None,
        title="Grade a candidate solution for correctness",
        goal=goal,
        scope=[],
        context_refs=[],
        done_criteria="",
        dependencies=[],
        constraints=[],
        preferred_worker_type=None,
        required_capabilities=[],
        escalation_count=0,
        status=list(TaskStatus)[0],
        created_at=now,
        updated_at=now,
    )


def _make_attempt(task_id: str, worker_id: str) -> TaskAttempt:
    return TaskAttempt(
        id=f"att-{uuid.uuid4().hex[:12]}",
        task_id=task_id,
        worker_id=worker_id,
        status=list(AttemptStatus)[0],
        error_code="",
        blocking_reason="",
        started_at=time.time(),
        ended_at=None,
        summary="",
        output="",
        artifact_refs=[],
        validator_refs=[],
    )


async def judge_one(
    worker, task_prompt: str, candidate_output: str, *, rubric: str = JUDGE_RUBRIC
):
    """Judge one candidate through the worker.

    `rubric` selects the grading standard — `JUDGE_RUBRIC` (code, default) or
    `GENERAL_RUBRIC` (non-verifiable tasks). Returns (verdict, cost_usd,
    raw_reply, error):
      verdict   — True (correct) / False (incorrect) / None (unparseable)
      cost_usd  — the judge call's own cost (estimated under Max auth)
      raw_reply — the judge's text (for auditing)
      error     — non-None if the call itself failed
    """
    task = _make_judge_task(build_judge_goal(task_prompt, candidate_output, rubric=rubric))
    attempt = _make_attempt(task.id, getattr(worker, "id", "judge"))
    summary = ""
    cost = 0.0
    error: Optional[str] = None
    async for ev in worker.execute(attempt, task):
        if ev.type == "metrics":
            cost = float(ev.payload.get("cost_usd", 0.0) or 0.0)
        elif ev.type == "attempt.completed":
            summary = ev.payload.get("summary", "")
        elif ev.type == "attempt.failed":
            error = ev.payload.get("error", "judge call failed")
    if hasattr(worker, "clear_history"):
        worker.clear_history(task.id)
    verdict = parse_verdict(summary) if summary else None
    return verdict, cost, summary, error
