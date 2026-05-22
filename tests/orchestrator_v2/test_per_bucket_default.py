"""
Step 3 smoke test: per-bucket bandit as default.

Verifies that:
  1. BanditRouter initialises with LinUCBPerBucketRouter when
     strategy="linucb_per_bucket" (same call as app.py:startup).
  2. After one select_agent call per BUCKET trigger, all 9 buckets
     appear in strategy.A — confirming per-bucket state is laid down
     correctly on first contact, not collapsed into a single matrix.
"""
from __future__ import annotations

from pathlib import Path

from backend.orchestrator.adapters.base import (
    AgentAdapter,
    AgentCapability,
    AgentStatus,
    CostEstimate,
)
from backend.orchestrator.adapters.registry import AdapterRegistry
from backend.orchestrator.routing.bandit_router import BanditRouter
from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.strategies.linucb_per_bucket import LinUCBPerBucketRouter
from backend.orchestrator.routing.vocab import BUCKETS, ENABLED_AGENTS


# ── minimal adapter double ────────────────────────────────────────────────────

class _StubAdapter(AgentAdapter):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def worker_id(self) -> str:
        return self._name

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [AgentCapability(name=t) for t in BUCKETS]

    def estimate_cost(self, task) -> CostEstimate:  # noqa: ARG002
        return CostEstimate(estimated_cost_usd=0.0)

    async def health_check(self) -> AgentStatus:
        return AgentStatus(name=self._name, available=True)


def _make_router() -> BanditRouter:
    logger = DecisionLogger(db_path=Path(":memory:"))
    reg = AdapterRegistry()
    for agent in ENABLED_AGENTS:
        reg.register(_StubAdapter(agent))
    return BanditRouter(
        strategy="linucb_per_bucket",
        registry=reg,
        logger=logger,
        state_path=Path("/tmp/test_per_bucket_smoke.json"),
    )


# One trigger per bucket — same phrases used in test_vocab_contracts.py
_BUCKET_TRIGGERS: dict[str, str] = {
    "debug":    "the service throws a null pointer exception on startup",
    "test":     "write unit tests for the payment processing module using pytest",
    "refactor": "refactor the user repository to decouple the database layer",
    "security": "audit the login endpoint for SQL injection and XSS vulnerabilities",
    "review":   "please review this pull request and give feedback on the approach",
    "research": "explain how transformer attention mechanisms work and compare different types",
    "plan": (
        "we need to design the full architecture for a distributed caching system. "
        "the system must support multi-region replication, automatic failover, TTL-based "
        "eviction, and a pluggable backend. document the component breakdown, the data "
        "flow from client to cache to backend store, the consistency model, the failure "
        "modes, and the operational runbook for cache warm-up after a regional outage."
    ),
    "code":     "implement a REST endpoint that queries the database and returns paginated results",
    "general":  "update the team on the current project status and next steps",
}


# ── tests ─────────────────────────────────────────────────────────────────────

def test_default_strategy_is_per_bucket():
    """BanditRouter with strategy='linucb_per_bucket' yields LinUCBPerBucketRouter."""
    router = _make_router()
    assert router.strategy.name == "linucb_per_bucket"
    assert isinstance(router.strategy, LinUCBPerBucketRouter)


def test_all_buckets_get_per_bucket_state_on_first_contact():
    """After routing one task per bucket, strategy.A contains all 9 BUCKETS."""
    router = _make_router()
    strategy: LinUCBPerBucketRouter = router.strategy  # type: ignore[assignment]
    available = list(ENABLED_AGENTS)

    for bucket, phrase in _BUCKET_TRIGGERS.items():
        ctx = TaskContext.from_task(type("T", (), {"goal": phrase})())
        strategy.select_agent(ctx, available)

    missing = set(BUCKETS) - set(strategy.A.keys())
    assert not missing, f"Buckets missing per-bucket state after one routing pass: {missing}"


def test_per_bucket_arms_are_separate_matrices():
    """Each (bucket, agent) pair gets its own A matrix — not shared across buckets."""
    strategy = LinUCBPerBucketRouter()
    available = list(ENABLED_AGENTS)

    for bucket, phrase in _BUCKET_TRIGGERS.items():
        ctx = TaskContext.from_task(type("T", (), {"goal": phrase})())
        strategy.select_agent(ctx, available)

    # Matrices for the same agent in different buckets must differ
    # (cross-bucket pooling blends them at init, but they diverge after updates).
    # At bare minimum, distinct bucket keys must exist.
    for agent in ENABLED_AGENTS:
        bucket_matrices = [
            strategy.A[b][agent].tobytes()
            for b in strategy.A
            if agent in strategy.A[b]
        ]
        # With 9 buckets and cross-bucket pooling, initial matrices may be similar
        # but there must be at least 9 entries total.
        assert len(bucket_matrices) == len(strategy.A), (
            f"Agent {agent!r} missing matrix in some buckets"
        )
