"""
route_sim.py — offline routing-policy simulation from a force-explore matrix.

When a bench run is force-explore (round-robin), every arm attempts every
prompt, so we hold a full {arm x prompt} matrix: pass/fail re-graded from the
stored outputs (zero new inference) plus the per-prompt cloud cost recorded at
run time. From that matrix we can compute — exactly, not as a projection — what
any static routing policy WOULD have scored on pass@1 and $/task. This turns
Phase-4's per-arm numbers into a head-to-head between a routing policy and the
always-cloud / always-local baselines.

The escalation gate is injectable. The default is the ORACLE gate: escalate iff
the local cascade genuinely failed the hidden tests. That is an upper bound — it
assumes perfect knowledge of local failure. On verifiable (code) tasks the
oracle is actually achievable (run the tests as the gate); on open-ended tasks
it is not, and a later pass swaps in a heuristic / LLM-judge gate to measure how
much of this ceiling a real, fallible signal captures.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .verify_replay import load_bank, load_results, run_case


@dataclass
class PolicyResult:
    name: str
    passed: int
    n: int
    cost_per_task: float
    escalations: Optional[int] = None  # set for routed (cascade) policies only

    @property
    def pass_rate(self) -> float:
        return self.passed / self.n if self.n else 0.0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "n": self.n,
            "pass_rate": round(self.pass_rate, 4),
            "cost_per_task": round(self.cost_per_task, 6),
            "cost_per_1k": round(self.cost_per_task * 1000, 4),
            "escalations": self.escalations,
        }


def grade_matrix(
    bank_path: Path, results_path: Path
) -> tuple[dict[str, dict[str, bool]], list[str]]:
    """Re-grade stored outputs vs hidden tests → {prompt: {agent: passed}}.

    Returns (matrix, bank_prompts). Only prompts present in the gold bank are
    graded; a result row whose prompt isn't in the bank is ignored (join
    health is the caller's concern via the returned bank_prompts length).
    """
    bank = load_bank(bank_path)
    results = load_results(results_path)
    matrix: dict[str, dict[str, bool]] = {}
    for r in results:
        spec = bank.get(r["prompt"])
        if spec is None:
            continue
        passed, _ = run_case(r["output"], spec["tests"])
        matrix.setdefault(r["prompt"], {})[r["agent"]] = passed
    return matrix, list(bank.keys())


def load_cloud_costs(
    decisions_db: Path, metrics_db: Path, bench_run_id: int, cloud_arm: str
) -> dict[str, float]:
    """{prompt_text: cost_usd} for the cloud arm's rows in this bench run.

    Cost lives in mahoraga.db:task_metrics (keyed by task_id, no prompt text);
    the prompt text + bench_run_id live in routing_decisions.db:decisions. We
    ATTACH the metrics DB and join on task_id, keying the result by the prompt
    text so it lines up with the graded matrix (which is keyed the same way).
    """
    conn = sqlite3.connect(str(decisions_db))
    try:
        conn.execute("ATTACH DATABASE ? AS mdb", (str(Path(metrics_db).resolve()),))
        rows = conn.execute(
            """SELECT d.task_goal, m.cost_usd FROM decisions d
               JOIN mdb.task_metrics m ON m.task_id = d.task_id
               WHERE d.bench_run_id = ? AND d.selected_agent = ?""",
            (bench_run_id, cloud_arm),
        ).fetchall()
    finally:
        conn.close()
    return {goal: cost for goal, cost in rows if cost is not None}


def _rate(matrix: dict[str, dict[str, bool]], prompts: list[str], agent: str) -> tuple[int, int]:
    """(passed, attempted) for an agent over the given prompts.

    Denominator is prompts the agent actually attempted, so an arm with
    infra-dropped rows (empty output never recorded) isn't punished as a
    content failure — matching how Phase-4 reported qwen3.5 as 36/44, not /50.
    """
    n = sum(1 for p in prompts if agent in matrix.get(p, {}))
    ok = sum(1 for p in prompts if matrix.get(p, {}).get(agent))
    return ok, n


def simulate(
    matrix: dict[str, dict[str, bool]],
    prompts: list[str],
    cloud_costs: dict[str, float],
    *,
    local_arms: list[str],
    cloud_arm: str,
    cascade: list[str],
    local_solved: Optional[Callable[[str], bool]] = None,
    gate_cost_per_task: float = 0.0,
) -> list[PolicyResult]:
    """Compute pass@1 + $/task for baselines and the routed cascade policy.

    - always-cloud, always-local:<arm> for each local arm — measured directly.
    - best-of-local — oracle over the local set (any local arm passes); the
      free-quality ceiling if you could always pick the right local arm.
    - routed:<cascade>->cloud — try the local `cascade` in order; if the gate
      accepts the local answer, keep it (pays $0); else escalate to cloud and
      pay that prompt's recorded cloud cost.

    `local_solved(prompt)` is the escalation gate. Default = ORACLE: accept iff
    some cascade arm truly passed the hidden tests. A fallible gate (heuristic /
    judge) is injected here later; note that quality is always scored against
    the true matrix, so a wrong "accept" correctly costs quality and a wrong
    "escalate" correctly costs money — that gap is the verification tax.

    `gate_cost_per_task` is the price of consulting the gate itself, charged on
    every routed prompt (not just escalations). The oracle and heuristic gates
    are free (0.0); an LLM-judge gate is NOT — it runs a model call per task, so
    its own cost must be counted against the routing savings, else the judge
    looks cheaper than it is.
    """
    mean_cloud = (sum(cloud_costs.values()) / len(cloud_costs)) if cloud_costs else 0.0

    def oracle_solved(p: str) -> bool:
        return any(matrix.get(p, {}).get(a) for a in cascade)

    gate = local_solved or oracle_solved

    policies: list[PolicyResult] = []

    ok, n = _rate(matrix, prompts, cloud_arm)
    policies.append(PolicyResult("always-cloud", ok, n, mean_cloud))

    for a in local_arms:
        ok, n = _rate(matrix, prompts, a)
        policies.append(PolicyResult(f"always-local:{a.split(':')[-1]}", ok, n, 0.0))

    bol = sum(1 for p in prompts if any(matrix.get(p, {}).get(a) for a in local_arms))
    policies.append(PolicyResult("best-of-local", bol, len(prompts), 0.0))

    routed_ok = 0
    escalations = 0
    total_cost = 0.0
    for p in prompts:
        total_cost += gate_cost_per_task  # the gate is consulted on every task
        if gate(p):
            # Keep the local answer. True pass iff a cascade arm actually solved
            # it — under the oracle gate this is always true; under a fallible
            # gate a wrong "accept" lands here as a real quality loss.
            if any(matrix.get(p, {}).get(a) for a in cascade):
                routed_ok += 1
        else:
            escalations += 1
            total_cost += cloud_costs.get(p, mean_cloud)
            if matrix.get(p, {}).get(cloud_arm):
                routed_ok += 1
    label = "->".join(a.split(":")[-1] for a in cascade) + "->cloud"
    policies.append(
        PolicyResult(
            f"routed:{label}",
            routed_ok,
            len(prompts),
            total_cost / len(prompts) if prompts else 0.0,
            escalations=escalations,
        )
    )
    return policies


def infer_arms(
    matrix: dict[str, dict[str, bool]], cloud_arm: str
) -> tuple[list[str], str]:
    """Discover (local_arms, best_local_arm) from the matrix.

    Local = any agent id starting with 'ollama:'. Best local = highest pass
    rate over attempted prompts (ties broken by attempt count then name), used
    as the default single-arm cascade.
    """
    agents: set[str] = set()
    for row in matrix.values():
        agents.update(row.keys())
    local_arms = sorted(a for a in agents if a.startswith("ollama:"))
    prompts = list(matrix.keys())

    def score(a: str) -> tuple[float, int, str]:
        ok, n = _rate(matrix, prompts, a)
        return ((ok / n) if n else 0.0, n, a)

    best_local = max(local_arms, key=score) if local_arms else ""
    return local_arms, best_local
