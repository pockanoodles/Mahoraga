"""
live_route.py — the local→judge→cloud escalation cascade, run LIVE end-to-end.

Phase 5a computed the routing ceiling and 5b the verification tax by REPLAYING a
stored force-explore matrix (bench_run_id=19) — zero new inference. This module
closes the loop on fresh inference. For each prompt it:

  1. runs the local arm to produce a candidate,
  2. asks a local judge (free Ollama) to decide correct / incorrect from the
     prompt + output ALONE — the production posture, no hidden tests, and
  3. escalates to the cloud arm only when the judge votes "incorrect".

Every served answer is then graded against the gold bank's hidden tests to get
the *true* routed pass@1, and the cloud arm's real per-call cost is measured
live. Nothing is read from disk: the local outputs, the judge verdicts, and the
cloud costs are all produced in this run. That is the difference from 5a/5b and
the point of 5c — the honest end-to-end proof of Thesis A.

To keep an honest always-cloud denominator, the cloud arm is run on every prompt
by default (`run_cloud_always`), so the baseline is measured on the same fresh
inference as the routed policy; the routed policy is only ever *charged* for the
escalations the judge actually triggered. Set `run_cloud_always=False` to spend
less (cloud runs only on escalation), at the cost of a measured always-cloud
baseline.

Reuses the audited pieces: `judge_gate.judge_one` for the verdict,
`verify_replay.run_case` for grading, and `route_sim.simulate` for aggregation —
the routed line it emits is the live analogue of 5b's `judge-gate` report.

SECURITY NOTE: grading runs model-generated code under a wall-clock timeout only
(via verify_replay.run_case), the same trusted-local-outputs posture as the
offline replays. The cloud arm is the audited egress (ClaudeCliWorker, Max
subscription, no API key).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from ..domain.models import Task, TaskAttempt
from ..workers.claude_cli import ClaudeCliWorker
from ..workers.ollama import OllamaWorker
from .judge_gate import judge_one
from .verify_replay import run_case


@dataclass
class RoutedCase:
    """One prompt's trip through the live cascade, with ground-truth grading.

    `local_passed` / `cloud_passed` are the hidden-test verdicts (the analysis
    ground truth the router never sees). `final_passed` is the grade of the
    answer actually served under the routing policy. `total_cost` charges the
    judge's own per-call cost plus the cloud call iff the judge escalated.
    """
    prompt: str
    bucket: str
    local_arm: str
    cloud_arm: str
    local_output: str
    local_passed: bool
    judge_verdict: Optional[bool]  # True=keep local / False=escalate / None=unparsed→escalate
    judge_cost: float
    escalated: bool
    cloud_output: Optional[str]
    cloud_passed: Optional[bool]
    cloud_cost: float
    final_passed: bool
    total_cost: float
    error: str = ""  # non-empty if a worker call itself failed

    def as_dict(self) -> dict:
        return {
            "prompt_full": self.prompt,
            "bucket": self.bucket,
            "local_arm": self.local_arm,
            "cloud_arm": self.cloud_arm,
            "actual_agent": self.cloud_arm if self.escalated else self.local_arm,
            "output_full": (self.cloud_output or "") if self.escalated else self.local_output,
            "local_output": self.local_output,
            "local_passed": self.local_passed,
            "judge_verdict": self.judge_verdict,
            "judge_cost": round(self.judge_cost, 6),
            "escalated": self.escalated,
            "cloud_output": self.cloud_output,
            "cloud_passed": self.cloud_passed,
            "cloud_cost": round(self.cloud_cost, 6),
            "final_passed": self.final_passed,
            "total_cost": round(self.total_cost, 6),
            "error": self.error,
        }


async def _run_worker(worker, prompt: str) -> tuple[str, float, Optional[str]]:
    """Run one worker on a raw prompt; return (output, cost_usd, error).

    Mirrors how `/api/task` frames a bench prompt (title=prompt[:80],
    goal=prompt) so the model sees the same input as a live serving call.
    """
    task = Task.new(run_id="live-route", title=prompt[:80], goal=prompt)
    attempt = TaskAttempt.new(task_id=task.id, worker_id=getattr(worker, "id", "arm"))
    output = ""
    cost = 0.0
    error: Optional[str] = None
    async for ev in worker.execute(attempt, task):
        if ev.type == "metrics":
            cost = float(ev.payload.get("cost_usd", 0.0) or 0.0)
        elif ev.type == "attempt.completed":
            output = ev.payload.get("summary", "")
        elif ev.type == "attempt.failed":
            error = ev.payload.get("error", "worker call failed")
    if hasattr(worker, "clear_history"):
        worker.clear_history(task.id)
    return output, cost, error


async def route_one(
    local_worker,
    judge_worker,
    cloud_worker,
    prompt: str,
    tests: str,
    *,
    bucket: str = "code",
    run_cloud_always: bool = True,
    local_label: Optional[str] = None,
    cloud_label: Optional[str] = None,
) -> RoutedCase:
    """Run one prompt through local → judge → (maybe) cloud, grading each step.

    The judge sees only (prompt, local_output) — no hidden tests — and its
    verdict alone decides escalation. A None (unparseable) verdict is treated as
    "escalate", the safe default (spend cloud $ rather than serve a maybe-wrong
    local answer). Grading uses the hidden `tests` purely for measurement.

    `local_label` / `cloud_label` set the arm ids recorded on the case (and thus
    the report labels); they default to the workers' own ids. The CLI passes
    role-stripped ids (`ollama:granite4.1-8b`, `claude-cli`) so the matrix keys
    match the 5a/5b convention rather than the role-suffixed worker id.
    """
    local_output, _local_cost, local_err = await _run_worker(local_worker, prompt)
    local_passed, _ = run_case(local_output, tests)

    verdict, judge_cost, _raw, judge_err = await judge_one(judge_worker, prompt, local_output)
    escalate = verdict is not True  # False or None → escalate

    cloud_output: Optional[str] = None
    cloud_passed: Optional[bool] = None
    cloud_cost = 0.0
    cloud_err: Optional[str] = None
    if escalate or run_cloud_always:
        cloud_output, cloud_cost, cloud_err = await _run_worker(cloud_worker, prompt)
        cloud_passed, _ = run_case(cloud_output, tests)

    if escalate:
        final_passed = bool(cloud_passed)
    else:
        final_passed = local_passed

    # The routed policy pays the judge on every task and the cloud call only on
    # escalation (a baseline-only cloud run on a kept-local task is not charged).
    total_cost = judge_cost + (cloud_cost if escalate else 0.0)

    errs = [e for e in (local_err, judge_err, cloud_err if escalate else None) if e]
    return RoutedCase(
        prompt=prompt,
        bucket=bucket,
        local_arm=local_label or getattr(local_worker, "id", "local"),
        cloud_arm=cloud_label or getattr(cloud_worker, "id", "cloud"),
        local_output=local_output,
        local_passed=local_passed,
        judge_verdict=verdict,
        judge_cost=judge_cost,
        escalated=escalate,
        cloud_output=cloud_output,
        cloud_passed=cloud_passed,
        cloud_cost=cloud_cost,
        final_passed=final_passed,
        total_cost=total_cost,
        error="; ".join(errs),
    )


def to_matrix(
    cases: list[RoutedCase],
) -> tuple[dict[str, dict[str, bool]], list[str], dict[str, float], dict[str, Optional[bool]], float]:
    """Fold live cases into the shape `route_sim.simulate` consumes.

    Returns (matrix, prompts, cloud_costs, verdicts, mean_judge_cost):
      matrix        {prompt: {local_arm: local_passed, cloud_arm: cloud_passed}}
      cloud_costs   {prompt: cloud_cost}  — only for prompts the cloud arm ran
      verdicts      {prompt: judge_verdict}  — the live escalation gate
      mean_judge_cost  charged per task by simulate (0.0 for a local judge)

    Feeding these to `simulate(..., local_solved=lambda p: verdicts[p] is True,
    gate_cost_per_task=mean_judge_cost)` reproduces the live routed result
    exactly — same semantics as 5b's judge-gate, on fresh inference.
    """
    matrix: dict[str, dict[str, bool]] = {}
    cloud_costs: dict[str, float] = {}
    verdicts: dict[str, Optional[bool]] = {}
    judge_costs: list[float] = []
    for c in cases:
        row = matrix.setdefault(c.prompt, {})
        row[c.local_arm] = c.local_passed
        if c.cloud_passed is not None:
            row[c.cloud_arm] = c.cloud_passed
            cloud_costs[c.prompt] = c.cloud_cost
        verdicts[c.prompt] = c.judge_verdict
        if c.judge_cost:
            judge_costs.append(c.judge_cost)
    prompts = [c.prompt for c in cases]
    mean_judge_cost = sum(judge_costs) / len(judge_costs) if judge_costs else 0.0
    return matrix, prompts, cloud_costs, verdicts, mean_judge_cost


def load_arms(
    config_path: Path,
    local_arm: str,
    judge_model: str,
    cloud_arm: str = "claude-cli",
    *,
    local_role: str = "coder",
) -> tuple[OllamaWorker, OllamaWorker, ClaudeCliWorker]:
    """Construct (local, judge, cloud) workers faithful to agents.yaml.

    The local arm is built from its `models` spec (options / max_ctx /
    extra_payload / base_url) so it behaves exactly as the configured roster
    arm; the judge is a plain Ollama worker (think off); the cloud arm is the
    audited `ClaudeCliWorker` from the `claude-cli` block (enabled or not — this
    is an explicit bench, the same posture as Phase 4).
    """
    cfg: dict[str, Any] = yaml.safe_load(config_path.read_text()) or {}

    ollama_cfg = cfg.get("ollama", {})
    base_url = ollama_cfg.get("base_url", "http://localhost:11434")
    want = local_arm.split(":")[1] if local_arm.startswith("ollama:") else local_arm
    spec = next((m for m in ollama_cfg.get("models", []) if m.get("id") == want), None)
    if spec is None:
        raise ValueError(f"local arm {local_arm!r} (id {want!r}) not found in {config_path}")
    local_worker = OllamaWorker(
        model=spec["model"],
        worker_id=f"ollama:{want}:{local_role}",
        base_url=base_url,
        options=spec.get("options"),
        extra_payload=spec.get("extra_payload", {"think": False}),
        max_ctx=spec.get("max_ctx"),
    )

    judge_worker = OllamaWorker(
        model=judge_model,
        worker_id="ollama:judge",
        base_url=base_url,
        extra_payload={"think": False},
    )

    cli_cfg = cfg.get(cloud_arm, {})
    cli_kwargs: dict[str, Any] = {
        "model": cli_cfg.get("model", "claude-sonnet-4-6"),
        "worker_id": cli_cfg.get("worker_id", f"{cloud_arm}:sonnet"),
    }
    if cli_cfg.get("binary_path"):
        cli_kwargs["binary_path"] = cli_cfg["binary_path"]
    if cli_cfg.get("timeout"):
        cli_kwargs["timeout"] = float(cli_cfg["timeout"])
    cloud_worker = ClaudeCliWorker(**cli_kwargs)

    return local_worker, judge_worker, cloud_worker
