"""The audit API's JSON shapes: the contract the UI is written against.

Declared once so FastAPI checks every response against them, publishes them in
/openapi.json, and a field that drifts from what the UI reads fails a test
instead of showing up as a blank cell. The field names are the wire names.

The shapes are the engine's own records, flattened for a browser (agentbench
reports and run manifests, audit evidence and verdicts); nothing here is
computed. See audit_api.py for where each one comes from.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Rate(BaseModel):
    """A task-weighted resolve rate and its 95% Wilson interval (agentbench/stats.py)."""

    point: float
    lo: float
    hi: float
    tasks: int
    attempts: int


class Plan(BaseModel):
    """A cloud plan's price as a dated observation (audit/pricing.json)."""

    id: str
    vendor: str
    name: str
    kind: str
    usd_per_month: float | None
    capacity_vs_pro: float | None
    as_of: str
    source: str
    verified: bool
    note: str


# ── /api/engine ───────────────────────────────────────────────────────────────


class Ollama(BaseModel):
    url: str
    up: bool
    version: str | None


class Engine(BaseModel):
    name: str
    version: str
    api: int
    headless: bool
    ui: bool = Field(description="Whether this engine serves a UI at /.")
    demo: bool
    bench_root: str
    data_dir: str
    python: str
    platform: str
    ollama: Ollama


# ── Runs ──────────────────────────────────────────────────────────────────────


class Machine(BaseModel):
    model: str | None
    memory_gb: int | None
    os: str | None


class Harness(BaseModel):
    commit: str | None
    dirty: bool | None


class RunSummary(BaseModel):
    """One run's manifest, boiled down (agentbench/manifest.py)."""

    run_id: str
    bench_id: str
    repo_name: str
    started_at: str
    ended_at: str | None = Field(description="Null while the run is going, or if it was killed.")
    stopped: str | None = Field(description="Why it ended: completed, deadline or interrupted.")
    attempts: int | None = Field(description="Attempts it counted; null until the run ends.")
    arms: list[str]
    machine: Machine
    harness: Harness
    ollama: str | None


class Runs(BaseModel):
    runs: list[RunSummary]


class RunDetail(BaseModel):
    run: RunSummary
    manifest: dict[str, Any] = Field(description="The manifest file as written.")
    attempts: list[dict[str, Any]] = Field(
        description="The run's counted attempts, each as recorded plus `outcome` and the task's `subject`.")


# ── Benches ───────────────────────────────────────────────────────────────────


class BenchRow(BaseModel):
    id: str = Field(description="The bench's directory name, e.g. click-4c074edc.")
    name: str = Field(description="The id without its trailing -<8 hex>.")
    repo: str | None = Field(description="Where the repo was, from the newest run manifest; null if never run.")
    tasks: int
    dropped: int
    attempts: int = Field(description="Counted attempts on tasks still in the bench.")
    degraded: int = Field(description="Attempts kept apart because the machine or the agent invalidated them.")
    arms: list[str]
    conds: list[str]
    runs: int
    last_activity: str | None = Field(
        description="When a run last wrote to the bench (ISO 8601); null if none has. Mining is not activity.")


class Benches(BaseModel):
    benches: list[BenchRow]


class SuiteConfig(BaseModel):
    python: str
    pytest_args: list[str]


class Task(BaseModel):
    sha: str
    subject: str
    body: str
    gold_src: list[str]
    gold_lines: int
    test_files: list[str]
    f2p: int = Field(description="How many tests must go from failing to passing: a count, not the ids.")
    p2p: int = Field(description="How many tests must keep passing: a count, not the ids.")


class Dropped(BaseModel):
    sha: str
    reason: str


class PrecisionPlan(BaseModel):
    """What it takes to narrow an arm's interval to ±target_half_width (agentbench/stats.py)."""

    target_half_width: float
    tasks_needed: int
    tasks_have: int
    attempts_needed: int
    nights: int
    text: str


class ArmSummary(BaseModel):
    """One arm under one condition on one bench (agentbench/report.py)."""

    arm: str
    cond: str
    n: int = Field(description="Counted attempts.")
    resolved: int
    tasks: int
    tasks_solved_ever: int
    tasks_solved_always: int
    repeats: int
    rate: Rate
    median_minutes: float
    outcomes: dict[str, int] = Field(description="Attempts by outcome label (report.OUTCOMES).")
    broke_other_code: int
    touched_tests: int
    multi_file_resolved: int
    multi_file_n: int
    tier: str
    plan: PrecisionPlan


class BenchDetail(BaseModel):
    id: str
    name: str
    repo: str | None
    config: SuiteConfig | None = Field(description="How the repo's tests run; null if the bench has no config.json.")
    tasks: list[Task]
    dropped: list[Dropped]
    arms: list[ArmSummary]
    matrix: dict[str, dict[str, list[str]]] = Field(
        description="task sha -> '<arm> / <cond>' -> one outcome label per repeat. Every task has an entry.")
    degraded_note: str | None
    runs: list[RunSummary]


# ── Arms and pricing ──────────────────────────────────────────────────────────


class ArmRow(BaseModel):
    arm: str
    agent: str
    model: str
    benches: int
    attempts: int
    conds: list[str]
    rate: Rate | None = Field(description="Pooled over benches. Null when the arm ran under more than one condition.")


class ModelRow(BaseModel):
    name: str
    digest: str | None


class AgentRow(BaseModel):
    name: str
    on_path: bool


class Arms(BaseModel):
    arms: list[ArmRow]
    models: list[ModelRow] = Field(description="Models Ollama has pulled; empty when Ollama is down.")
    agents: list[AgentRow]


class Pricing(BaseModel):
    plans: list[Plan]


# ── POST /api/audit/verdict ───────────────────────────────────────────────────


class _Strict(BaseModel):
    # A misspelt assumption must be an error: ignored, it would quietly decide
    # on a default the caller never chose.
    model_config = ConfigDict(extra="forbid")


class ApiBilling(_Strict):
    kind: Literal["api"]
    monthly_usd: float = Field(gt=0)


class SubscriptionBilling(_Strict):
    kind: Literal["subscription"]
    plan: str = Field(description="A plan id from /api/audit/pricing.")
    plan_price: float | None = Field(default=None, ge=0, description="Required when the plan's price isn't verified.")


class AssumptionsIn(_Strict):
    """Everything but tasks_per_month is optional; the engine's defaults fill the rest."""

    tasks_per_month: int = Field(gt=0)
    deferrable_share: float | None = Field(default=None, gt=0, le=1)
    triage_minutes: float | None = Field(default=None, ge=0)
    hourly_usd: float | None = Field(default=None, ge=0)
    watts: float | None = Field(default=None, ge=0)
    usd_per_kwh: float | None = Field(default=None, ge=0)
    usage_of_cap: float | None = Field(default=None, ge=0)


class VerdictRequest(_Strict):
    benches: list[str] | None = Field(
        default=None, min_length=1,
        description="Bench ids to pool. Null pools every bench with attempts for this arm and condition.")
    arm: str
    cond: str
    billing: Annotated[ApiBilling | SubscriptionBilling, Field(discriminator="kind")]
    assumptions: AssumptionsIn


class VerdictEvidence(BaseModel):
    rate: Rate
    minutes_per_attempt: float
    tier: str
    label: str
    hardware: str
    failed: int  # attempts that didn't resolve
    timed_out: int  # of those, the ones that ran out of time


class VerdictAssumptions(BaseModel):
    tasks_per_month: int
    deferrable_share: float
    triage_minutes: float
    hourly_usd: float
    watts: float
    usd_per_kwh: float
    usage_of_cap: float
    lines: list[str] = Field(description="The assumptions in words, as the CLI prints them.")


class VerdictBilling(BaseModel):
    kind: str
    monthly_usd: float
    plan: Plan | None
    lower: Plan | None


class MonthlyUsd(BaseModel):
    lo: float
    point: float
    hi: float


class Verdict(BaseModel):
    call: Literal["stay", "split", "switch"]
    confident: bool
    reason: str
    evidence: VerdictEvidence
    assumptions: VerdictAssumptions
    billing: VerdictBilling
    monthly_usd: MonthlyUsd | None = Field(
        description="Net per month at the interval's low end, point and high end; null on a flat plan.")
    breakeven: float | None = Field(
        description="API billing only: the resolve rate at which local pays. Above 1 means no rate does.")
    sensitivity: list[str]
    rendered: str = Field(description="The verdict as the CLI prints it.")
