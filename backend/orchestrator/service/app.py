from __future__ import annotations
import asyncio
import datetime
import logging
import time
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv()
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from ..adaptive.store import AdaptiveStore
from ..channels.base import ChannelMessage
from ..channels.web import _STATIC_DIR
from ..domain.models import Mission, Plan, Run, RunMode, RunStatus, TaskStatus
from ..domain.transitions import IllegalTransition
from ..gateway import Gateway
from ..store.base import Store
from ..store.chat_log import ChatLogStore
from ..tracking.ledger import CostLedger
from ..tracking.pricing import resolve_cost
import anthropic

from ..verifier.verifier import Verifier
from ..config import ENABLED_BACKENDS, MahoragaConfig, get_workdir
from ..workers.claude import ClaudeWorker
from ..workers.registry import WorkerRegistry
from ..adapters.registry import AdapterRegistry
from .approvals import grant_approval, reject_approval
from .executor import run_task as _run_task, pop_judge_gate, pop_task_metrics
from .run_executor import run_run as _run_run
from ..planning.planner import generate_tasks, PlannerError
from ..routing import BanditRouter, STRATEGIES, TaskOutcome
from ..routing.escalation_strategies import (
    EscalationStrategy,
    apply_strategy as _apply_escalation_strategy,
)
from ..routing.implicit_quality import ImplicitQualityTracker
from ..store.eval_store import EvalStore
from ..store.rankings_store import RankingsStore
from ..store.metrics import MetricsStore
from ..domain.models import TaskAttempt

# ── singletons (replaced via dependency_overrides in tests) ──────────────────

_store: Store | None = None
_registry: WorkerRegistry | None = None
_verifier: Verifier | None = None
_gateway: Gateway | None = None
_adaptive_store: AdaptiveStore | None = None
_cost_ledger: CostLedger | None = None
_config: MahoragaConfig | None = None
_adapter_registry: AdapterRegistry | None = None
_bandit_router: BanditRouter | None = None
_implicit_tracker: ImplicitQualityTracker | None = None
_eval_store: EvalStore | None = None
_START_TIME: float = time.time()
_bandit_seed: int | None = None


def get_store() -> Store:
    assert _store is not None, "Store not initialised"
    return _store


def get_registry() -> WorkerRegistry:
    assert _registry is not None, "Registry not initialised"
    return _registry


def get_verifier() -> Verifier:
    assert _verifier is not None, "Verifier not initialised"
    return _verifier


def get_gateway() -> Gateway:
    assert _gateway is not None, "Gateway not initialised"
    return _gateway


def get_adapter_registry() -> AdapterRegistry:
    assert _adapter_registry is not None, "AdapterRegistry not initialised"
    return _adapter_registry


def get_bandit_router() -> BanditRouter:
    assert _bandit_router is not None, "BanditRouter not initialised"
    return _bandit_router


def get_eval_store() -> EvalStore:
    assert _eval_store is not None, "EvalStore not initialised"
    return _eval_store


StoreDep = Annotated[Store, Depends(get_store)]
RegistryDep = Annotated[WorkerRegistry, Depends(get_registry)]
VerifierDep = Annotated[Verifier, Depends(get_verifier)]
GatewayDep = Annotated[Gateway, Depends(get_gateway)]
AdapterRegistryDep = Annotated[AdapterRegistry, Depends(get_adapter_registry)]
EvalStoreDep = Annotated[EvalStore, Depends(get_eval_store)]

_rankings_store: RankingsStore | None = None
_metrics_store: MetricsStore | None = None


def get_rankings_store() -> RankingsStore:
    assert _rankings_store is not None
    return _rankings_store


def get_metrics_store() -> MetricsStore:
    assert _metrics_store is not None
    return _metrics_store


RankingsStoreDep = Annotated[RankingsStore, Depends(get_rankings_store)]
MetricsStoreDep = Annotated[MetricsStore, Depends(get_metrics_store)]


# ── lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    global _store, _registry, _verifier, _gateway, _adaptive_store, _cost_ledger, _config, _adapter_registry, _bandit_router, _implicit_tracker, _bandit_seed
    _startup_logger = logging.getLogger(__name__)
    _bandit_seed_env = os.getenv("MAHORAGA_BANDIT_SEED")
    if _bandit_seed_env is not None:
        try:
            _seed = int(_bandit_seed_env)
            import random as _random
            import numpy as _np
            _random.seed(_seed)
            _np.random.seed(_seed)
            _bandit_seed = _seed
            _startup_logger.info("MAHORAGA_BANDIT_SEED=%d — seeded random + numpy", _seed)
        except ValueError:
            _startup_logger.warning("MAHORAGA_BANDIT_SEED=%r is not an integer; ignoring", _bandit_seed_env)
    else:
        _startup_logger.info("MAHORAGA_BANDIT_SEED not set — bandit randomness is unseeded")
    _store = await Store.connect()
    _registry = WorkerRegistry()

    class _PassthroughVerifier(Verifier):
        def __init__(self) -> None:
            pass
        async def verify(self, task, output):
            from ..verifier.verifier import VerificationResult
            if not output or not output.strip() or len(output.strip()) < 10:
                return VerificationResult(score=0, passed=False, feedback="empty or trivial output", action="retry")
            return VerificationResult(score=10, passed=True, feedback="", action="pass")

    # ── Verifier setup ────────────────────────────────────────────────────────
    if "claude" in ENABLED_BACKENDS:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        _verifier = (
            Verifier(client=anthropic.Anthropic(api_key=api_key))
            if api_key else _PassthroughVerifier()
        )
    else:
        _verifier = _PassthroughVerifier()

    # ── Agent pool from agents.yaml ───────────────────────────────────────────
    # Edit agents.yaml at the project root to add, remove, or retune agents.
    # No Python required — restart orch serve to apply changes.
    _config = MahoragaConfig()
    from ..adapters.loader import load_agent_pool
    _workdir = get_workdir()
    _pool_workers, _pool_adapters = load_agent_pool(
        workdir=_workdir,
        ollama_url_override=_config.get("ollama_base_url") or None,
    )
    for w in _pool_workers:
        _registry.register(w)

    # Opus: opt-in via ENABLE_OPUS=1, intentionally absent from agents.yaml
    # (escalation-only; not a default bandit arm)
    if "claude" in ENABLED_BACKENDS and os.getenv("ENABLE_OPUS") == "1":
        _opus_key = os.getenv("ANTHROPIC_API_KEY")
        if _opus_key:
            _registry.register(ClaudeWorker(
                api_key=_opus_key,
                model="claude-opus-4-6",
                worker_id="claude:opus",
                capabilities=["complex_reasoning", "deep_reasoning", "general"],
            ))

    # ── Build AdapterRegistry ─────────────────────────────────────────────────
    _adapter_registry = AdapterRegistry()
    for a in _pool_adapters:
        _adapter_registry.register(a)

    logger = logging.getLogger(__name__)
    for adapter in _adapter_registry.all():
        logger.info("adapter registered: %s", adapter.name)

    _bandit_router = BanditRouter(
        strategy="linucb_per_bucket",
        registry=_adapter_registry,
    )

    global _implicit_tracker
    _implicit_tracker = ImplicitQualityTracker()

    # Orphan recovery: tasks left in_progress from a crashed previous run
    for orphan in await _store.tasks.list_by_status(TaskStatus.in_progress):
        await _store.tasks.update_status(orphan.id, TaskStatus.failed)

    # Adaptive store + cost ledger share the same DB connection as the main store
    _adaptive_store = AdaptiveStore(_store._conn)
    await _adaptive_store.migrate()

    _cost_ledger = CostLedger(_store._conn)
    await _cost_ledger.migrate()

    global _eval_store
    _eval_store = EvalStore(_store._conn)
    await _eval_store.migrate()

    global _rankings_store, _metrics_store
    _rankings_store = RankingsStore(_store._conn)
    await _rankings_store.migrate()
    _metrics_store = MetricsStore(_store._conn)
    await _metrics_store.migrate()

    _gateway = Gateway(
        store=_store,
        registry=_registry,
        verifier=_verifier,
        adaptive_store=_adaptive_store,
        cost_ledger=_cost_ledger,
        config=_config,
        adapter_registry=_adapter_registry,
        bandit_router=_bandit_router,
    )

    # A3 retrain lifespan hook. Gated by MAHORAGA_AUTO_RETRAIN so a fresh
    # install doesn't spend startup time on retraining without explicit
    # opt-in. Two triggers:
    #   1. Startup staleness check (one-shot, in a background thread so
    #      the FastAPI app boots without waiting for the trainer).
    #   2. Periodic check every MAHORAGA_AUTO_RETRAIN_INTERVAL_S seconds
    #      (default 1800 = 30 min) for the duration of the session.
    # The mtime-based hot-swap in quality_predictor.get_model() means
    # any new weights propagate to in-process callers automatically.
    _retrain_task: asyncio.Task | None = None
    if os.getenv("MAHORAGA_AUTO_RETRAIN", "").strip().lower() in ("1", "true", "yes", "on"):
        from ..routing.quality_predictor import maybe_retrain as _maybe_retrain
        _interval_s = int(
            os.getenv("MAHORAGA_AUTO_RETRAIN_INTERVAL_S", "1800") or "1800"
        )

        def _retrain_once() -> None:
            try:
                result = _maybe_retrain()
                _startup_logger.info(
                    "auto_retrain: %s",
                    {k: v for k, v in result.items() if k != "outcome"},
                )
            except Exception as exc:  # noqa: BLE001
                _startup_logger.warning("auto_retrain failed: %s", exc)

        async def _retrain_loop() -> None:
            # First check at startup (in a thread so we don't block).
            await asyncio.get_event_loop().run_in_executor(None, _retrain_once)
            while True:
                try:
                    await asyncio.sleep(_interval_s)
                except asyncio.CancelledError:
                    return
                await asyncio.get_event_loop().run_in_executor(None, _retrain_once)

        _retrain_task = asyncio.create_task(_retrain_loop())
        _startup_logger.info(
            "auto_retrain: enabled (interval=%ds)", _interval_s,
        )
    else:
        _startup_logger.info(
            "auto_retrain: disabled (set MAHORAGA_AUTO_RETRAIN=1 to enable)"
        )

    yield
    # Cancel the retrain loop cleanly on shutdown so pytest event loops
    # don't see a dangling task.
    if _retrain_task is not None:
        _retrain_task.cancel()
        try:
            await _retrain_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    await _store.close()


app = FastAPI(title="Orchestrator v2", lifespan=lifespan)

# ── web chat routes ───────────────────────────────────────────────────────────


class _ChatRequest(BaseModel):
    message: str
    user_id: str = "web-user"


class _EvalStartRequest(BaseModel):
    run_type: str
    routing_enabled: bool
    baseline_policy: str | None = None
    suite_name: str
    repeat_index: int = 0


class _EvalTaskRequest(BaseModel):
    text: str
    bucket: str = "general"
    difficulty: str = "medium"
    routing_mode: str = "adaptive"  # "adaptive" | "fixed:agent_id"
    run_id: int | None = None
    task_id: str | None = None


class _EvalTaskResult(BaseModel):
    agent: str
    latency_ms: float
    ttft_ms: float | None
    success: bool
    reward: float | None
    output_preview: str


class _EvalFinishRequest(BaseModel):
    run_id: int


# Resolve the React SPA dist directory: repo_root/frontend/dist.
# _STATIC_DIR already points at repo_root/static, so its parent is repo_root.
_FRONTEND_DIST = _STATIC_DIR.parent / "frontend" / "dist"


@app.get("/", response_class=HTMLResponse)
async def serve_index() -> HTMLResponse:
    # Prefer the built React SPA; fall back to the legacy static shell.
    dist_index = _FRONTEND_DIST / "index.html"
    if dist_index.exists():
        return HTMLResponse(content=dist_index.read_text())
    index_path = _STATIC_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse(content="<html><body><h1>Mahoraga</h1></body></html>")
    return HTMLResponse(content=index_path.read_text())


@app.post("/chat")
async def chat(request: _ChatRequest, gateway: GatewayDep) -> StreamingResponse:
    msg = ChannelMessage.new(
        user_id=request.user_id,
        channel="web",
        text=request.message,
    )

    async def event_stream():
        import json as _json
        try:
            async for chunk in gateway.handle_message(msg):
                # Intercept metrics dicts emitted by OllamaWorker
                if isinstance(chunk, dict) and chunk.get("type") == "metrics":
                    _record_metrics(
                        elapsed_s=chunk.get("elapsed_s", 0.0),
                        tokens=chunk.get("tokens", 0),
                    )
                # JSON-encode so multi-line chunks don't break SSE frame parsing
                yield f"data: {_json.dumps(chunk)}\n\n"
        except Exception as exc:
            yield f"data: {_json.dumps('[ERROR] ' + str(exc))}\n\n"
        yield f"data: {_json.dumps('[DONE]')}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# Mount static files if the static directory exists (legacy UI).
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Mount the React SPA's hashed asset bundle when a production build is present.
_DIST_ASSETS = _FRONTEND_DIST / "assets"
if _DIST_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST_ASSETS)), name="frontend-assets")


# ── request / response models ─────────────────────────────────────────────────

class ApprovalRequest(BaseModel):
    run_id: str


class CreateMissionRequest(BaseModel):
    title: str
    goal: str
    background: str = ""
    success_condition: str = ""


class CreatePlanRequest(BaseModel):
    mission_id: str
    mode: str = "direct"


class LogEventItem(BaseModel):
    id: str
    type: str
    task_id: str | None
    attempt_id: str | None
    ts: float


class TaskRequest(BaseModel):
    prompt: str
    capability_hint: str | None = None
    agent_override: str | None = None
    bench_run_id: int | None = None


class BenchRunCreate(BaseModel):
    started_at: str | None = None
    mode: str | None = None
    git_sha: str | None = None
    git_dirty: int | None = None
    ollama_version: str | None = None
    hostname: str | None = None
    on_charger: int | None = None
    bandit_seed: int | None = None
    prompt_seed: int | None = None
    prompts_file: str | None = None
    agents: str | None = None
    repeats: int | None = None
    task_count_planned: int | None = None
    notes: str | None = None


class BenchRunFinalize(BaseModel):
    task_count_completed: int


class BatchTaskItem(BaseModel):
    prompt: str
    depends_on: list[int] = []
    expected_files: list[str] = []
    capability_hint: str | None = None


class BatchRequest(BaseModel):
    tasks: list[BatchTaskItem]
    parallel: bool = True
    max_concurrent: int = 2


class LogRunItem(BaseModel):
    id: str
    mission_id: str
    status: str
    created_at: float
    events: list[LogEventItem]


class LogsResponse(BaseModel):
    runs: list[LogRunItem]


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/tasks/{task_id}")
async def get_task(task_id: str, store: StoreDep):
    task = await store.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/runs/{run_id}/tasks")
async def list_run_tasks(run_id: str, store: StoreDep):
    return await store.tasks.list_by_run(run_id)


@app.get("/runs/{run_id}/events")
async def list_run_events(run_id: str, store: StoreDep):
    return await store.events.list_by_run(run_id)


@app.get("/tasks/{task_id}/events")
async def list_task_events(task_id: str, store: StoreDep):
    return await store.events.list_by_task(task_id)


@app.get("/tasks/{task_id}/attempts")
async def list_task_attempts(task_id: str, store: StoreDep):
    return await store.tasks.list_attempts(task_id)


@app.post("/tasks/{task_id}/approve")
async def approve_task(task_id: str, req: ApprovalRequest, store: StoreDep):
    try:
        await grant_approval(req.run_id, task_id, store)
    except IllegalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "approved"}


@app.post("/tasks/{task_id}/reject")
async def reject_task(task_id: str, req: ApprovalRequest, store: StoreDep):
    try:
        await reject_approval(req.run_id, task_id, store)
    except IllegalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "rejected"}


@app.post("/tasks/{task_id}/run", status_code=202)
async def execute_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    store: StoreDep,
    registry: RegistryDep,
    verifier: VerifierDep,
):
    task = await store.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (TaskStatus.ready, TaskStatus.blocked):
        raise HTTPException(status_code=409, detail="Task is not in a runnable state")
    background_tasks.add_task(_run_task, task_id, store, registry, verifier)
    return {"task_id": task_id, "status": "queued"}


@app.get("/workers/health")
async def workers_health(registry: RegistryDep):
    results = await registry.health_all()
    return {worker_id: {"worker_id": h.worker_id, "healthy": h.healthy, "detail": h.detail}
            for worker_id, h in results.items()}


@app.get("/api/health")
async def api_health(adapter_reg: AdapterRegistryDep):
    """Lightweight heartbeat for MCP clients. No heavy computation."""
    router = get_bandit_router()
    agents_online = 0
    for adapter in adapter_reg.all():
        try:
            status = await adapter.health_check()
            if status.available:
                agents_online += 1
        except Exception:
            pass
    return {
        "status": "ok",
        "uptime_s": int(time.time() - _START_TIME),
        "agents_registered": len(list(adapter_reg.all())),
        "agents_online": agents_online,
        "strategy": router.strategy.name,
        "total_decisions": router.logger.count(),
    }


@app.get("/api/health/routing")
async def api_health_routing():
    """R1.4 — full routing health snapshot.

    Pulls from `routing_decisions.db` directly, so it works regardless
    of in-process router state (and a separate `orch metrics live`
    process sees the same data). Cheap (<100ms even at 10K rows).
    """
    from ..routing.observability import compute_health_snapshot
    router = get_bandit_router()
    snap = compute_health_snapshot(db_path=router.logger.db_path)
    return snap.to_dict()


@app.get("/api/agents/status")
async def agents_status(registry: AdapterRegistryDep):
    """Return health status for all registered AgentAdapters."""
    return await registry.all_statuses()


@app.post("/runs/{plan_id}/start", status_code=202)
async def start_run(
    plan_id: str,
    background_tasks: BackgroundTasks,
    store: StoreDep,
    registry: RegistryDep,
    verifier: VerifierDep,
):
    plan = await store.missions.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Reuse the paused run created by /generate if it has tasks, otherwise create fresh
    existing_runs = await store.missions.list_runs(plan.mission_id)
    run = None
    for r in existing_runs:
        if r.plan_id == plan_id and r.status == RunStatus.paused:
            tasks = await store.tasks.list_by_run(r.id)
            if tasks:
                run = r
                break

    if run is None:
        run = Run.new(mission_id=plan.mission_id, plan_id=plan_id, mode=RunMode.direct)
        await store.missions.save_run(run)

    background_tasks.add_task(_run_run, run.id, store, registry, verifier)
    return {"run_id": run.id, "status": "queued"}


@app.post("/runs/reset")
async def reset_workflow(store: StoreDep):
    """Cancel all active/paused runs. Used by the sidebar reset button."""
    all_runs = await store.missions.list_all_runs()
    cancelled = []
    for run in all_runs:
        if run.status in (RunStatus.active, RunStatus.paused):
            await store.missions.update_run_status(run.id, RunStatus.cancelled)
            cancelled.append(run.id)
    return {"cancelled": cancelled}


@app.get("/runs/{run_id}")
async def get_run(run_id: str, store: StoreDep):
    run = await store.missions.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.get("/runs")
async def list_runs(store: StoreDep, mission_id: str | None = None):
    if mission_id:
        return await store.missions.list_runs(mission_id)
    return await store.missions.list_all_runs()


@app.get("/logs", response_model=LogsResponse)
async def get_logs(store: StoreDep, limit: int = 5) -> LogsResponse:
    limit = min(limit, 20)
    all_runs = await store.missions.list_all_runs()  # already DESC by created_at
    runs = all_runs[:limit]
    run_items = []
    for run in runs:
        events = await store.events.list_by_run(run.id)
        run_items.append(LogRunItem(
            id=run.id,
            mission_id=run.mission_id,
            status=run.status.value,
            created_at=run.created_at,
            events=[
                LogEventItem(
                    id=e.id,
                    type=e.type,
                    task_id=e.task_id,
                    attempt_id=e.attempt_id,
                    ts=e.ts,
                )
                for e in events
            ],
        ))
    return LogsResponse(runs=run_items)


@app.get("/cost/summary")
async def cost_summary(store: StoreDep, user_id: str = "web-user"):
    if _cost_ledger is None:
        return {"session_usd": 0.0, "total_usd": 0.0, "breakdown": []}

    today_start = datetime.datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()

    session_usd = await _cost_ledger.cost_since(user_id=user_id, since=today_start)
    total_usd = await _cost_ledger.total_cost(user_id=user_id)
    breakdown = await _cost_ledger.cost_by_model(user_id=user_id, since=today_start)

    return {
        "session_usd": round(session_usd, 6),
        "total_usd": round(total_usd, 6),
        "breakdown": breakdown,
    }


# ── In-memory session metrics (legacy — kept for chat SSE stream path) ────────
_session_metrics = {"total_elapsed_s": 0.0, "total_tokens": 0, "task_count": 0}


def _gateway_escalation(
    router: "BanditRouter",
    selected_agent: str,
    adapter_registry: "AdapterRegistry",
) -> tuple[str, dict]:
    """A2 gateway hook: consume the composer's escalation_strategy and
    return the (possibly-swapped) final agent + flags the executor /
    verifier need.

    Pure pass-through when:
      - The composer didn't run (ComposedDecision is None).
      - escalation_strategy is "none" (no escalate signal fired).
      - The strategy's prerequisites aren't met (e.g. claude requested
        but adapter isn't registered → falls through inside apply_strategy).

    Returns (final_agent, flags_dict). flags can carry:
      - "strict_verify": True when AGGRESSIVE_VERIFY fires.
      - "double_run_alt": agent name for telemetry-only double-run hint.
      - "swapped_from": original agent when CLAUDE swap happened.
    """
    composed = getattr(router, "_last_composed", None)
    if composed is None:
        return selected_agent, {}
    strategy = getattr(composed, "escalation_strategy", EscalationStrategy.NONE.value)
    if strategy == EscalationStrategy.NONE.value:
        return selected_agent, {}
    action = _apply_escalation_strategy(
        strategy=strategy,
        selected_agent=selected_agent,
        bandit_pick=getattr(composed, "bandit_pick", None),
        would_be_agent=getattr(composed, "would_be_agent", None),
        adapter_registry=adapter_registry,
    )
    if action.final_agent != selected_agent or action.flags:
        logging.getLogger(__name__).info(
            "escalation gateway: %s (strategy=%s, flags=%s)",
            action.reason, action.strategy, action.flags,
        )
    return action.final_agent, action.flags


def _record_metrics(elapsed_s: float, tokens: int) -> None:
    _session_metrics["total_elapsed_s"] += elapsed_s
    _session_metrics["total_tokens"] += tokens
    _session_metrics["task_count"] += 1


@app.get("/api/metrics")
async def get_metrics(store: StoreDep):
    router = get_bandit_router()
    total_decisions = router.logger.count()

    session   = await store.metrics.get_session_aggregates()
    agents    = await store.metrics.get_agent_breakdown()
    buckets   = await store.metrics.get_bucket_breakdown()
    health    = await store.metrics.get_routing_health(total_decisions)

    return {
        "session": session,
        "agents": agents,
        "buckets": buckets,
        "routing_health": health,
    }


@app.get("/settings/workdir")
async def get_workdir_setting():
    return {"workdir": get_workdir()}


@app.post("/settings/workdir")
async def set_workdir_setting(body: dict):
    new_wd = body.get("workdir", "")
    expanded = os.path.expanduser(new_wd)
    if not os.path.isdir(expanded):
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {expanded}")
    _config.set("workdir", new_wd)
    return {"workdir": expanded}


@app.get("/missions/active")
async def get_active_mission(store: StoreDep):
    """Return the most recently active mission's task graph for the vine chart."""
    all_runs = await store.missions.list_all_runs()
    active_run = None
    for run in all_runs:
        if run.status in (RunStatus.active, RunStatus.paused):
            active_run = run
            break

    if active_run is None:
        return {"mission": None, "run": None, "tasks": []}

    mission = await store.missions.get(active_run.mission_id)
    tasks = await store.tasks.list_by_run(active_run.id)

    task_items = []
    for task in tasks:
        attempts = await store.tasks.list_attempts(task.id)
        worker_id = ""
        if attempts:
            worker_id = attempts[-1].worker_id

        elapsed = 0.0
        if attempts:
            latest = attempts[-1]
            if latest.ended_at is not None and latest.started_at is not None:
                elapsed = round(latest.ended_at - latest.started_at, 1)
            elif latest.started_at is not None:
                elapsed = round(time.time() - latest.started_at, 1)

        task_items.append({
            "id": task.id,
            "title": task.title,
            "status": task.status.value,
            "parent_task_id": task.parent_task_id,
            "dependencies": [
                {"task_id": d.task_id, "type": d.type.value}
                for d in task.dependencies
            ],
            "worker_id": worker_id,
            "elapsed_seconds": elapsed,
        })

    return {
        "mission": {
            "id": mission.id,
            "title": mission.title,
            "goal": mission.goal,
        } if mission else None,
        "run": {
            "id": active_run.id,
            "status": active_run.status.value,
        },
        "tasks": task_items,
    }


@app.get("/logs/recent")
async def logs_recent(store: StoreDep, user_id: str = "web-user", limit: int = 20):
    limit = min(limit, 50)
    entries = await store.chat_log.list_recent(user_id=user_id, limit=limit)
    return {
        "entries": [
            {
                "id": e.id,
                "user_message": e.user_message,
                "assistant_response": e.assistant_response,
                "worker_id": e.worker_id,
                "cost_usd": e.cost_usd,
                "created_at": e.created_at,
            }
            for e in entries
        ]
    }


@app.get("/settings")
async def get_settings():
    """Return current configuration (read-only). Sensitive values are masked."""

    def mask(val: str | None) -> str:
        if not val:
            return "(not set)"
        if len(val) <= 8:
            return "••••••••"
        return val[:4] + "••••" + val[-4:]

    api_key = os.getenv("ANTHROPIC_API_KEY")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    brave_key = os.getenv("BRAVE_API_KEY")

    return {
        "executor_model": "claude-sonnet-4-6",
        "anthropic_api_key": mask(api_key),
        "telegram_token": mask(tg_token),
        "brave_api_key": mask(brave_key),
        "configured": bool(api_key),
    }


class _BackendSettings(BaseModel):
    active_backend: str

    @field_validator("active_backend")
    @classmethod
    def validate_backend(cls, v: str) -> str:
        if v not in ("claude", "ollama"):
            raise ValueError("active_backend must be 'claude' or 'ollama'")
        return v


@app.get("/settings/backend")
async def get_backend_settings():
    """Return current backend config (active_backend + ollama_base_url)."""
    return _config.all()


@app.post("/settings/backend")
async def set_backend_settings(req: _BackendSettings):
    """Switch the active backend. Takes effect on the next request — no restart needed."""
    _config.set("active_backend", req.active_backend)
    return _config.all()


@app.delete("/runs/{run_id}")
async def cancel_run(run_id: str, store: StoreDep):
    run = await store.missions.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    await store.missions.update_run_status(run_id, RunStatus.cancelled)
    return {"run_id": run_id, "status": "cancelled"}


# ── missions ──────────────────────────────────────────────────────────────────

@app.post("/missions", status_code=201)
async def create_mission(req: CreateMissionRequest, store: StoreDep):
    mission = Mission.new(
        title=req.title,
        goal=req.goal,
        background=req.background,
        success_condition=req.success_condition,
    )
    await store.missions.save(mission)
    return {"id": mission.id, "title": mission.title, "status": mission.status}


@app.get("/missions/{mission_id}")
async def get_mission(mission_id: str, store: StoreDep):
    mission = await store.missions.get(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return {"id": mission.id, "title": mission.title, "goal": mission.goal,
            "background": mission.background, "success_condition": mission.success_condition,
            "status": mission.status}


@app.get("/missions")
async def list_missions(store: StoreDep):
    missions = await store.missions.list()
    return [{"id": m.id, "title": m.title, "status": m.status} for m in missions]


# ── plans ─────────────────────────────────────────────────────────────────────

@app.post("/plans", status_code=201)
async def create_plan(req: CreatePlanRequest, store: StoreDep):
    mission = await store.missions.get(req.mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    try:
        mode = RunMode(req.mode)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid mode {req.mode!r}. Valid values: {[m.value for m in RunMode]}")
    plan = Plan.new(mission_id=req.mission_id)
    run = Run.new(mission_id=req.mission_id, plan_id=plan.id, mode=mode)
    await store.missions.save_plan(plan)
    await store.missions.save_run(run)
    return {"plan_id": plan.id, "run_id": run.id, "run_status": run.status}


@app.get("/plans/{plan_id}")
async def get_plan(plan_id: str, store: StoreDep):
    plan = await store.missions.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"id": plan.id, "mission_id": plan.mission_id, "status": plan.status,
            "version": plan.version}


@app.post("/missions/{mission_id}/generate", status_code=201)
async def generate_plan(mission_id: str, store: StoreDep):
    mission = await store.missions.get(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    plan = Plan.new(mission_id=mission_id)
    run = Run.new(mission_id=mission_id, plan_id=plan.id, mode=RunMode.direct)
    await store.missions.save_plan(plan)
    await store.missions.save_run(run)

    try:
        tasks = await generate_tasks(mission, run_id=run.id)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except PlannerError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    for task in tasks:
        await store.tasks.save(task)

    return {
        "plan_id": plan.id,
        "run_id": run.id,
        "tasks": [{"id": t.id, "title": t.title, "goal": t.goal} for t in tasks],
    }


@app.get("/plans")
async def list_plans(store: StoreDep, mission_id: str | None = None):
    if mission_id:
        plans = await store.missions.list_plans(mission_id)
    else:
        plans = []
        missions = await store.missions.list()
        for m in missions:
            plans.extend(await store.missions.list_plans(m.id))
    return [{"id": p.id, "mission_id": p.mission_id, "status": p.status,
             "version": p.version} for p in plans]


# ── eval endpoints ───────────────────────────────────────────────────────────

@app.post("/api/eval/start")
async def eval_start(
    req: _EvalStartRequest,
    eval_store: EvalStoreDep,
) -> dict:
    run_id = await eval_store.create_run(
        run_type=req.run_type,
        routing_enabled=req.routing_enabled,
        baseline_policy=req.baseline_policy,
        suite_name=req.suite_name,
        repeat_index=req.repeat_index,
    )
    return {"run_id": run_id}


@app.post("/api/eval/task", response_model=_EvalTaskResult)
async def eval_task(
    req: _EvalTaskRequest,
    registry: RegistryDep,
    adapter_reg: AdapterRegistryDep,
    bandit: Annotated[BanditRouter, Depends(get_bandit_router)],
    eval_store: EvalStoreDep,
) -> _EvalTaskResult:
    import dataclasses as _dc
    import time as _time
    import uuid as _uuid

    # Select agent — bandit returns adapter name (e.g. "claude"); we need worker_id
    _bandit_adapter_name: str | None = None
    if req.routing_mode.startswith("fixed:"):
        agent_id = req.routing_mode.removeprefix("fixed:")
    else:
        @_dc.dataclass
        class _EvalTask:
            title: str
            goal: str
        _bandit_adapter_name = bandit.route(_EvalTask(title=req.text, goal=req.text))
        adapter = adapter_reg.get(_bandit_adapter_name)
        agent_id = adapter.worker_id if adapter else _bandit_adapter_name

    # Build minimal task and attempt objects
    task_id = req.task_id or str(_uuid.uuid4())

    @_dc.dataclass
    class _EvalTaskObj:
        id: str
        goal: str
        title: str
        scope: str = ""
        context_refs: list = _dc.field(default_factory=list)
        constraints: list = _dc.field(default_factory=list)
        done_criteria: str = ""

    task_obj = _EvalTaskObj(id=task_id, goal=req.text, title=req.text[:80])
    attempt = TaskAttempt.new(task_id=task_id, worker_id=agent_id)

    # Execute
    worker = registry.get(agent_id)
    start = _time.monotonic()
    ttft_ms: float | None = None
    output_parts: list[str] = []
    success = False

    worker_m: dict = {}
    try:
        async for event in worker.execute(attempt, task_obj, None):
            if ttft_ms is None:
                ttft_ms = (_time.monotonic() - start) * 1000
            if event.type == "attempt.completed":
                success = True
                output_parts.append(event.payload.get("summary", ""))
            elif event.type == "attempt.failed":
                success = False
            elif event.type == "metrics":
                worker_m = event.payload
    except Exception:
        success = False

    latency_ms = (_time.monotonic() - start) * 1000
    reward = 0.7 if success else 0.0

    # Feed outcome back to bandit (same as production routing) — only for adaptive mode
    if _bandit_adapter_name is not None:
        from ..routing.reward import TaskOutcome as _TaskOutcome
        bandit.observe(
            type("_T", (), {"goal": req.text, "id": task_id})(),
            _TaskOutcome(
                success=success,
                latency_s=latency_ms / 1000,
                cost_usd=resolve_cost(worker_m),
                quality_score=reward,
                agent_name=_bandit_adapter_name,
            ),
        )

    if req.run_id is not None:
        await eval_store.insert_run_task(
            run_id=req.run_id,
            task_id=task_id,
            task_text=req.text,
            bucket=req.bucket,
            difficulty=req.difficulty,
            selected_agent=agent_id,
            latency_ms=latency_ms,
            success=success,
            reward=reward,
            ttft_ms=ttft_ms,
        )

    return _EvalTaskResult(
        agent=agent_id,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        success=success,
        reward=reward,
        output_preview="".join(output_parts)[:200],
    )


@app.post("/api/eval/finish")
async def eval_finish(
    req: _EvalFinishRequest,
    eval_store: EvalStoreDep,
) -> dict:
    await eval_store.finish_run(req.run_id)
    return {"ok": True}


@app.get("/api/bench_run/seed")
async def get_bench_run_seed() -> dict:
    """Return the bandit_seed this server was started with (or null)."""
    return {"bandit_seed": _bandit_seed}


@app.post("/api/bench_run")
async def create_bench_run(req: BenchRunCreate) -> dict:
    """Create a bench_runs row at session start. Returns bench_run_id."""
    router = get_bandit_router()
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    run_id = router.logger.create_bench_run(**fields)
    return {"bench_run_id": run_id}


@app.post("/api/bench_run/{run_id}/finalize")
async def finalize_bench_run(run_id: int, req: BenchRunFinalize) -> dict:
    """Set ended_at + task_count_completed on the bench_runs row."""
    router = get_bandit_router()
    router.logger.finalize_bench_run(run_id, req.task_count_completed)
    return {"ok": True}


@app.get("/api/rankings")
async def get_rankings(
    rankings_store: RankingsStoreDep,
    metrics_store: MetricsStoreDep,
    scope_type: str = "overall",
    scope_value: str = "all",
    bucket: str | None = None,
    difficulty: str | None = None,
    agent: str | None = None,
    limit: int = 20,
    refresh: bool = False,
) -> dict:
    from ..rankings.aggregator import rebuild_rankings
    if refresh:
        await rebuild_rankings(metrics_store, rankings_store)

    if bucket:
        scope_type, scope_value = "bucket", bucket
    elif difficulty:
        scope_type, scope_value = "difficulty", difficulty

    rows = await rankings_store.get_rankings(
        scope_type=scope_type, scope_value=scope_value, limit=limit
    )
    if agent:
        rows = [r for r in rows if r["agent"] == agent]
    return {"scope_type": scope_type, "scope_value": scope_value, "rankings": rows}


class _BenchmarkRunRequest(BaseModel):
    agent: str
    bucket: str | None = None
    difficulty: str | None = None
    avg_latency_ms: float | None = None
    median_latency_ms: float | None = None
    p90_latency_ms: float | None = None
    win_rate: float | None = None
    reward_mean: float | None = None
    sample_count: int = 0
    source: str = "harness"


@app.post("/api/rankings/benchmark")
async def upsert_benchmark_run(
    req: _BenchmarkRunRequest,
    rankings_store: RankingsStoreDep,
    metrics_store: MetricsStoreDep,
) -> dict:
    """Record a benchmark result and rebuild rankings."""
    await rankings_store.upsert_benchmark_run(
        agent=req.agent,
        bucket=req.bucket,
        difficulty=req.difficulty,
        avg_latency_ms=req.avg_latency_ms,
        median_latency_ms=req.median_latency_ms,
        p90_latency_ms=req.p90_latency_ms,
        win_rate=req.win_rate,
        reward_mean=req.reward_mean,
        sample_count=req.sample_count,
        source=req.source,
    )
    from ..rankings.aggregator import rebuild_rankings
    await rebuild_rankings(metrics_store, rankings_store)
    return {"ok": True}


# ── routing endpoints ─────────────────────────────────────────────────────────

@app.get("/api/routing/stats")
async def routing_stats():
    """Routing statistics for the dashboard."""
    router = get_bandit_router()
    return {
        "strategy": router.strategy.name,
        "total_decisions": router.logger.count(),
        "stats": router.logger.get_stats(),
    }


@app.get("/api/routing/agents")
async def routing_agents(adapter_reg: AdapterRegistryDep):
    """Health and performance for all registered agents."""
    router = get_bandit_router()
    agents = []
    for adapter in adapter_reg.all():
        try:
            status = await adapter.health_check()
        except Exception as exc:
            from ..adapters.base import AgentStatus
            status = AgentStatus(name=adapter.name, available=False, detail=str(exc))
        stats = router.logger.get_stats(agent=adapter.name)
        agents.append({
            "name": adapter.name,
            "healthy": status.available,
            "detail": status.detail,
            "capabilities": [c.name for c in adapter.capabilities],
            **stats,
        })
    return {"agents": agents}


@app.post("/api/routing/strategy")
async def set_routing_strategy(body: dict):
    """Switch routing strategy at runtime."""
    router = get_bandit_router()
    name = body.get("strategy", "linucb_per_bucket")
    if name not in STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {name}. Options: {list(STRATEGIES)}")
    router.set_strategy(name)
    return {"strategy": name, "message": f"Switched to {name}"}


_VALID_ROUTING_MODES = {"local_first", "balanced", "quality_first"}


@app.get("/api/routing/mode")
async def get_routing_mode():
    """Return the current routing_mode preference."""
    mode = MahoragaConfig().get("routing_mode") or "balanced"
    return {"routing_mode": mode}


@app.post("/api/routing/mode")
async def set_routing_mode(body: dict):
    """Set the routing_mode preference. Takes effect on the next routing decision."""
    mode = body.get("mode", "")
    if mode not in _VALID_ROUTING_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode {mode!r}. Options: {sorted(_VALID_ROUTING_MODES)}",
        )
    MahoragaConfig().set("routing_mode", mode)
    return {"routing_mode": mode, "message": f"Switched to {mode}"}


@app.post("/api/routing/dry-run")
async def routing_dry_run(body: dict):
    """Score all available agents for a prompt without committing a routing decision."""
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")

    router = get_bandit_router()

    class _FakeTask:
        goal = prompt
        tier = 2

    result = router.score_all(_FakeTask())
    scores_list = [
        {
            "agent": agent,
            "ucb_score": s["ucb"],
            "exploit": s["exploit"],
            "explore": s["explore"],
        }
        for agent, s in sorted(
            result["scores"].items(), key=lambda x: x[1]["ucb"], reverse=True
        )
    ]
    selected = scores_list[0]["agent"] if scores_list else None

    from ..routing.context import CODE_KEYWORDS, RESEARCH_KEYWORDS
    words = set(prompt.lower().split())
    bucket = "code" if words & CODE_KEYWORDS else "general"

    return {
        "prompt": prompt,
        "keyword_classification": {"capability_bucket": bucket},
        "bandit_selection": {
            "strategy": result["strategy"],
            "selected_agent": selected,
            "scores": scores_list,
        },
    }


@app.get("/api/routing/spread")
async def routing_spread():
    """Per-bucket mean-reward spread between the two active arms (θᵀx, no UCB bonus).

    Used by `orch benchmark v2-review` to evaluate the §13 item 6 learning criterion:
    ≥3 buckets must show |θᵀx(qwen) - θᵀx(granite)| > 0.1 after 200 episodes.
    Compute as (A⁻¹b)ᵀx at each bucket's representative context vector.
    """
    import numpy as np
    from ..routing.warm_start import _BUCKET_VECTORS
    from ..routing.vocab import BUCKETS, ENABLED_AGENTS

    router = get_bandit_router()
    strategy = router.strategy
    if not hasattr(strategy, "A") or not hasattr(strategy, "get_theta"):
        return {"error": "Strategy does not expose per-bucket theta", "strategy": strategy.name}

    results: dict[str, dict] = {}
    for bucket in BUCKETS:
        x = np.array(_BUCKET_VECTORS[bucket], dtype=float)
        bucket_result: dict[str, float] = {}
        for agent in ENABLED_AGENTS:
            try:
                theta = strategy.get_theta(agent, bucket)
                mean_reward = float(x @ theta)
            except Exception:  # noqa: BLE001
                mean_reward = float("nan")
            bucket_result[agent] = round(mean_reward, 4)

        agent_values = [v for v in bucket_result.values() if v == v]  # drop nan
        spread = round(max(agent_values) - min(agent_values), 4) if len(agent_values) >= 2 else 0.0
        results[bucket] = {
            "mean_rewards": bucket_result,
            "spread": spread,
            "criterion_met": spread > 0.1,
        }

    buckets_meeting_criterion = sum(1 for r in results.values() if r["criterion_met"])
    total_decisions = router.logger.count()
    return {
        "total_decisions": total_decisions,
        "criterion_threshold": 0.1,
        "criterion_required_buckets": 3,
        "buckets_meeting_criterion": buckets_meeting_criterion,
        "criterion_passed": buckets_meeting_criterion >= 3,
        "buckets": results,
    }


@app.get("/api/routing/decisions")
async def routing_decisions(
    limit: int = 10,
    agent: str | None = None,
    since: str | None = None,
):
    """Query recent routing decisions from the decision log."""
    # Bumped from 50 → 2000 so the dashboard can render trend windows
    # (last 250 / 500 / 1000 / all) without paginating.
    limit = min(limit, 2000)
    router = get_bandit_router()
    decisions = router.logger.get_recent(limit=limit, agent=agent, since=since)
    return {
        "decisions": decisions,
        "total_available": router.logger.count(),
        "filters_applied": {k: v for k, v in {"agent": agent, "since": since}.items() if v},
    }


@app.get("/api/resource-groups")
async def resource_groups_endpoint():
    """Resource group config. current_load is populated in Task 5."""
    from ..resource_groups import RESOURCE_GROUPS
    return {
        name: {
            "agents": group["agents"],
            "max_concurrent": group["max_concurrent"],
            "description": group["description"],
            "current_load": 0,
        }
        for name, group in RESOURCE_GROUPS.items()
    }


async def _is_ollama_warm() -> bool:
    """Return True if any Ollama model is currently loaded in memory."""
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://localhost:11434/api/ps")
            if resp.status_code == 200:
                return bool(resp.json().get("models"))
    except Exception:
        pass
    return False


@app.post("/api/task")
async def run_api_task(
    req: TaskRequest,
    store: StoreDep,
    registry: RegistryDep,
    verifier: VerifierDep,
):
    """Execute a single task synchronously. Waits for completion (up to 5 min)."""
    import dataclasses
    import time as _time
    from ..domain.models import Mission, Plan, Run, Task, RunMode, TaskStatus
    from ..routing.reward import TaskOutcome
    from ..resource_groups import get_resource_group
    from ..store.metrics import _classify_bucket

    router = get_bandit_router()
    adapter_reg = get_adapter_registry()

    # Implicit quality: check if this new submission is a retry or accept signal
    from hashlib import sha256 as _sha256
    _task_hash = _sha256(req.prompt.encode()).hexdigest()[:16]
    if _implicit_tracker is not None:
        _signal = _implicit_tracker.on_task_submitted(task_hash=_task_hash)
        if _signal is not None:
            _prev_id, _score = _signal
            import asyncio as _asyncio
            _asyncio.ensure_future(
                _store.metrics.update_implicit_quality(task_id=_prev_id, implicit_quality=_score)
            )
            # Also nudge the bandit with the implicit signal
            _bandit_router = get_bandit_router()
            _prev_decision = _bandit_router.logger.get_decision_by_task_id(_prev_id)
            if _prev_decision:
                _bandit_router.apply_implicit_reward(
                    task_id=_prev_id,
                    agent_name=_prev_decision["selected_agent"],
                    task_goal=_prev_decision.get("task_goal", ""),
                    implicit_signal=_score,
                )

    # Minimal infrastructure: one mission → plan → run → task
    mission = Mission.new(title=f"MCP: {req.prompt[:40]}", goal=req.prompt)
    await store.missions.save(mission)
    plan = Plan.new(mission_id=mission.id)
    await store.missions.save_plan(plan)
    run = Run.new(mission_id=mission.id, plan_id=plan.id, mode=RunMode.direct)
    await store.missions.save_run(run)

    task = Task.new(run_id=run.id, title=req.prompt[:80], goal=req.prompt)
    # IMPORTANT: don't save yet. We need to persist the task WITH the
    # resolved preferred_worker_type so the executor's re-fetch sees it.
    # Saving before the routing decision means assign_worker falls back
    # to candidates[0] and every task lands on the first-registered worker,
    # which was a silent bug that broke agent_override completely.

    # Check Ollama warm state before routing decision
    model_was_warm = await _is_ollama_warm() if not req.agent_override else False

    # Route via bandit (logs the decision, populates _last_scores).
    # When agent_override is set, log a synthetic decision row with
    # strategy="override" so bench analytics can still join via bench_run_id.
    t_route_start = _time.monotonic()
    escalation_flags: dict = {}
    if req.agent_override:
        selected_agent = req.agent_override
        router.log_override(task, selected_agent, bench_run_id=req.bench_run_id)
    else:
        selected_agent = router.route(task, bench_run_id=req.bench_run_id)
        # A2 gateway hook: consume composer's escalation_strategy.
        # No-op when escalation_strategy=="none" or composer didn't run.
        selected_agent, escalation_flags = _gateway_escalation(
            router, selected_agent, adapter_reg,
        )
    routing_time_ms = (_time.monotonic() - t_route_start) * 1000
    scores = router.strategy.get_scores()  # populated by route() above

    # Determine exploration flag: was the bandit exploring (UCB bonus > exploit lead)?
    exploration_flag = False
    if scores and len(scores) > 1:
        best_exploit = max(scores, key=lambda a: scores[a].get("exploit", 0))
        exploration_flag = (selected_agent != best_exploit)

    # Map adapter name → worker_id so executor uses the bandit's choice
    adapter = adapter_reg.get(selected_agent)
    if adapter:
        task = dataclasses.replace(task, preferred_worker_type=adapter.worker_id)

    # Now persist — after preferred_worker_type is set.
    await store.tasks.save(task)
    # Transition task to ready so executor can pick it up
    await store.tasks.update_status(task.id, TaskStatus.ready)

    # F2.2: double-run — create alt task before the clock starts so both
    # tasks can be saved and ready before asyncio.gather fires.
    alt_agent: str | None = (
        escalation_flags.get("double_run_alt") if not req.agent_override else None
    )
    alt_task = None
    if alt_agent:
        _alt_adapter = adapter_reg.get(alt_agent)
        if _alt_adapter:
            alt_task = Task.new(run_id=run.id, title=task.title, goal=task.goal)
            alt_task = dataclasses.replace(
                alt_task, preferred_worker_type=_alt_adapter.worker_id
            )
            await store.tasks.save(alt_task)
            await store.tasks.update_status(alt_task.id, TaskStatus.ready)
            router.log_override(alt_task, alt_agent)
        else:
            alt_agent = None

    t0 = _time.monotonic()
    _run_task_exc: Exception | None = None
    _alt_exc: Exception | None = None
    # F2: route execution through the global ExecutionPool so concurrent
    # /api/run/.../execute calls share the same concurrency cap as
    # batch tasks. The pool also feeds queue_depth_norm into the next
    # routing decision's context vector. asyncio.wait_for caps any
    # individual task at MAHORAGA_TASK_TIMEOUT.
    from ..routing.execution_pool import (
        get_default_pool as _get_default_pool,
        resolve_task_timeout as _resolve_task_timeout,
    )
    _pool = _get_default_pool()
    _timeout = _resolve_task_timeout()

    async def _exec_agent(task_id: str, agent: str) -> None:
        async with _pool.acquire(agent):
            await asyncio.wait_for(
                _run_task(task_id, store, registry, verifier), timeout=_timeout
            )

    if alt_task is not None:
        # Run primary + alt concurrently; collect exceptions without raising.
        _dr = await asyncio.gather(
            _exec_agent(task.id, selected_agent),
            _exec_agent(alt_task.id, alt_agent),
            return_exceptions=True,
        )
        if isinstance(_dr[0], Exception):
            _run_task_exc = _dr[0]
            logging.getLogger(__name__).warning(
                "/api/task: primary run failed in double_run for %s: %s",
                task.id, _dr[0],
            )
        if isinstance(_dr[1], Exception):
            _alt_exc = _dr[1]
            logging.getLogger(__name__).warning(
                "/api/task: alt run failed in double_run for %s: %s",
                alt_task.id, _dr[1],
            )
    else:
        try:
            await _exec_agent(task.id, selected_agent)
        except asyncio.TimeoutError as exc:
            _run_task_exc = exc
            logging.getLogger(__name__).warning(
                "/api/task: _run_task timed out (>%ds) for %s",
                int(_timeout), task.id,
            )
        except Exception as exc:
            _run_task_exc = exc
            logging.getLogger(__name__).exception(
                "/api/task: _run_task raised for %s", task.id
            )
    wall_time_ms = (_time.monotonic() - t0) * 1000
    elapsed = round(wall_time_ms / 1000, 2)

    # Collect result — even on failure, attempts/artifacts may have partial data
    task = await store.tasks.get(task.id)
    attempts = await store.tasks.list_attempts(task.id)
    artifacts = await store.artifacts.list_by_task(task.id)

    output = "" if _run_task_exc is not None else next(
        (a.location.get("content", "") for a in artifacts if a.type == "text_output"), ""
    )
    used_worker = attempts[-1].worker_id if attempts else selected_agent
    if _run_task_exc is not None:
        status = "failed"
        success = False
    else:
        status = "success" if task.status == TaskStatus.completed else "failed"
        success = status == "success"

    # Pull worker token metrics captured by executor side-channel
    ollama_m = pop_task_metrics(task.id)
    tokens_generated = ollama_m.get("tokens", 0)
    tokens_per_second = ollama_m.get("throughput_tps", 0.0)
    prompt_tokens = ollama_m.get("prompt_tokens", 0)
    prompt_eval_rate = ollama_m.get("prompt_eval_rate", 0.0)
    agent_spawn_ms = max(0.0, wall_time_ms - routing_time_ms - (ollama_m.get("elapsed_s", 0) * 1000))
    # Real cost: worker-reported (cloud arms), else computed from tokens+model; 0.0 for local arms
    cost_usd = resolve_cost(ollama_m)

    bucket = _classify_bucket(req.prompt, hint=req.capability_hint)
    from ..routing.quality import score_quality_detailed as _score_quality_detailed
    from ..routing.escalation_strategies import STRICT_VERIFY_QUALITY_THRESHOLD
    from ..routing.execution_gate import EXEC_GATE_BUCKETS, check_executes, exec_gate_enabled
    if success:
        quality_score, quality_components = await _score_quality_detailed(req.prompt, output, bucket)
        # A2 aggressive_verify: when the gateway flagged strict verification,
        # treat sub-threshold quality as a failure so the bandit's update
        # reflects the stricter bar. Retry-with-alternative is future work;
        # this gives the learning signal the spec's intent calls for.
        if escalation_flags.get("strict_verify") and quality_score < STRICT_VERIFY_QUALITY_THRESHOLD:
            logging.getLogger(__name__).info(
                "aggressive_verify: quality=%.3f < %.2f threshold; "
                "marking outcome as failed for bandit update",
                quality_score, STRICT_VERIFY_QUALITY_THRESHOLD,
            )
            success = False
            status = "failed"
        # Verifiable-reward execution gate: for code-producing buckets, output
        # that doesn't even run is broken however "structured" it looks — the
        # heuristic quality scorer can't see this (findings.md Era 9). Flip the
        # outcome to failed so reward short-circuits to 0 (capping quality alone
        # is toothless — success is the bulk of the code-bucket weight). Only
        # catches "doesn't run", not "wrong"; opt out with MAHORAGA_EXEC_GATE=off.
        if success and bucket in EXEC_GATE_BUCKETS and exec_gate_enabled():
            _ran_ok, _exec_err = await check_executes(output)
            if not _ran_ok:
                logging.getLogger(__name__).info(
                    "exec_gate: %s output did not execute (%s); marking failed",
                    bucket, (_exec_err or "")[:80],
                )
                success = False
                status = "failed"
    else:
        quality_score, quality_components = 0.0, None

    # Judge gate (routing/judge_escalation.py): the local judge read the ROUTED
    # agent's own output and voted it incorrect, so the executor escalated. The
    # task may well have completed anyway — an escalation target answered, or we
    # fell back to the rejected answer rather than block — and `status` stays
    # honest about that, because the caller did get served. But the bandit
    # attributes every task to `selected_agent`, so scoring this as that agent's
    # success would reinforce the exact output the gate just rejected. Split the
    # two: the user sees what happened, the bandit sees the verdict.
    bandit_success = success
    _judge_gate = pop_judge_gate(task.id)
    if bandit_success and _judge_gate.get("routed_output_rejected"):
        logging.getLogger(__name__).info(
            "judge_gate: routed agent %s was judge-rejected (%s); scoring the "
            "bandit update as a failure for that agent",
            selected_agent, _judge_gate.get("reason", ""),
        )
        bandit_success = False

    # F2.2: score alt output and pick the winner when double-run fired.
    # Both outcomes are fed to the bandit so we learn from two agents per task.
    _double_run_winner: str | None = None
    if alt_task is not None and _alt_exc is None:
        _alt_task_result = await store.tasks.get(alt_task.id)
        _alt_artifacts = await store.artifacts.list_by_task(alt_task.id)
        _alt_output = next(
            (a.location.get("content", "") for a in _alt_artifacts if a.type == "text_output"),
            "",
        )
        _alt_success = (
            _alt_task_result is not None
            and _alt_task_result.status == TaskStatus.completed
        )
        _alt_m = pop_task_metrics(alt_task.id)
        _alt_cost_usd = resolve_cost(_alt_m)
        if _alt_success:
            _alt_quality, _alt_components = await _score_quality_detailed(
                req.prompt, _alt_output, bucket
            )
        else:
            _alt_quality, _alt_components = 0.0, None
        _alt_outcome = TaskOutcome(
            success=_alt_success,
            latency_s=elapsed,
            cost_usd=_alt_cost_usd,
            quality_score=_alt_quality,
            agent_name=alt_agent,
            bucket=bucket,
            spawn_time_ms=0.0,
        )
        try:
            router.observe(alt_task, _alt_outcome)
        except Exception:
            logging.getLogger(__name__).exception(
                "double_run: alt observe failed for %s", alt_task.id
            )
        if _alt_cost_usd > 0 and _cost_ledger is not None:
            try:
                await _cost_ledger.record(
                    user_id="web-user",
                    mission_id=mission.id,
                    model=_alt_m.get("model", "") or alt_agent,
                    input_tokens=int(_alt_m.get("prompt_tokens") or 0),
                    output_tokens=int(_alt_m.get("tokens") or 0),
                    cache_read_tokens=int(_alt_m.get("cache_read_tokens") or 0),
                    cost_usd=_alt_cost_usd,
                )
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "cost ledger record failed for alt task %s: %s", alt_task.id, exc
                )
        if _alt_success and _alt_quality > quality_score:
            logging.getLogger(__name__).info(
                "double_run winner: %s (%.3f) beat %s (%.3f)",
                alt_agent, _alt_quality, selected_agent, quality_score,
            )
            output = _alt_output
            quality_score = _alt_quality
            quality_components = _alt_components
            success = True
            status = "success"
            _double_run_winner = alt_agent
        else:
            _double_run_winner = selected_agent

    # Always update the bandit — even on failure — so the decision row gets a
    # reward and the selected agent is penalized for the failure.
    outcome = TaskOutcome(
        success=bandit_success,
        latency_s=elapsed,
        cost_usd=cost_usd,
        quality_score=quality_score,
        agent_name=selected_agent,
        bucket=bucket,
        spawn_time_ms=agent_spawn_ms,
        quality_components=quality_components,
    )
    try:
        router.observe(task, outcome)
    except Exception:
        logging.getLogger(__name__).exception(
            "/api/task: bandit observe failed for %s", task.id
        )
    reward = router.reward_calc.compute(outcome)

    # Write to task_metrics
    ucb_score = scores.get(selected_agent, {}).get("ucb", 0.0) if scores else 0.0
    from hashlib import sha256 as _sha
    _prompt_hash = _sha(req.prompt.encode()).hexdigest()[:16]
    await store.metrics.record(
        task_id=task.id,
        task_hash=_prompt_hash,
        agent_name=selected_agent,
        capability_bucket=_classify_bucket(req.prompt, hint=req.capability_hint),
        wall_time_ms=round(wall_time_ms, 2),
        routing_time_ms=round(routing_time_ms, 2),
        agent_spawn_time_ms=round(agent_spawn_ms, 2),
        tokens_generated=tokens_generated,
        tokens_per_second=tokens_per_second,
        prompt_tokens=prompt_tokens,
        prompt_eval_rate=prompt_eval_rate,
        model_was_warm=model_was_warm,
        bandit_ucb_score=ucb_score,
        bandit_exploration_flag=exploration_flag,
        reward_score=reward,
        success=bandit_success,
        quality_score=quality_score,
        cost_usd=cost_usd,
    )

    # Nonzero cost → also append to the cost ledger (feeds /cost/summary daily spend)
    if cost_usd > 0 and _cost_ledger is not None:
        try:
            await _cost_ledger.record(
                user_id="web-user",
                mission_id=mission.id,
                model=ollama_m.get("model", "") or used_worker,
                input_tokens=int(prompt_tokens or 0),
                output_tokens=int(tokens_generated or 0),
                cache_read_tokens=int(ollama_m.get("cache_read_tokens") or 0),
                cost_usd=cost_usd,
            )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "cost ledger record failed for task %s: %s", task.id, exc
            )

    # implicit quality tracking — only on actual completion, not failure
    if _implicit_tracker is not None and success:
        from hashlib import sha256 as _sha256
        _th = _sha256((task.goal if hasattr(task, 'goal') else '').encode()).hexdigest()[:16]
        _implicit_tracker.on_task_complete(task_id=task.id, task_hash=_th)

    # Re-raise now that observe + metrics have been recorded — FastAPI returns 500
    if _run_task_exc is not None:
        raise _run_task_exc

    # Build runner-up from scores
    runner_up = None
    if scores:
        sorted_scores = sorted(scores.items(), key=lambda x: x[1]["ucb"], reverse=True)
        if len(sorted_scores) > 1:
            runner_up = {"agent": sorted_scores[1][0], "ucb_score": sorted_scores[1][1]["ucb"]}

    return {
        "task_id": task.id,
        "status": status,
        "agent": selected_agent,
        "worker_id": used_worker,
        "resource_group": get_resource_group(selected_agent),
        "elapsed_s": elapsed,
        "output": output,
        "metrics": {
            "wall_time_ms": round(wall_time_ms, 1),
            "routing_time_ms": round(routing_time_ms, 2),
            "tokens": tokens_generated,
            "tps": tokens_per_second,
            "model_was_warm": model_was_warm,
        },
        "routing": {
            "strategy": router.strategy.name,
            "ucb_score": ucb_score,
            "exploration": exploration_flag,
            "runner_up": runner_up,
            "double_run_alt": alt_agent,
            "double_run_winner": _double_run_winner,
        },
    }


@app.post("/api/batch")
async def run_batch(
    req: BatchRequest,
    store: StoreDep,
    registry: RegistryDep,
    verifier: VerifierDep,
):
    """Batch task execution. Sequential in this version — parallel added in Task 5."""
    import dataclasses
    import time as _time
    import uuid as _uuid
    from ..domain.models import (
        Mission, Plan, Run, Task, RunMode, TaskStatus,
        Dependency, DependencyType,
    )
    from ..resource_groups import get_resource_group

    batch_id = f"b_{_uuid.uuid4().hex[:8]}"
    t_batch_start = _time.time()

    router = get_bandit_router()
    adapter_reg = get_adapter_registry()

    # Create shared run for the batch
    mission = Mission.new(title=f"Batch {batch_id}", goal=f"{len(req.tasks)} tasks")
    await store.missions.save(mission)
    plan = Plan.new(mission_id=mission.id)
    await store.missions.save_plan(plan)
    run = Run.new(mission_id=mission.id, plan_id=plan.id, mode=RunMode.direct)
    await store.missions.save_run(run)

    # Create all tasks upfront (scope stores expected_files)
    created: list[Task] = []
    for i, item in enumerate(req.tasks):
        deps = [
            Dependency(task_id=created[j].id, type=DependencyType.completion)
            for j in item.depends_on
            if 0 <= j < i
        ]
        task = Task.new(
            run_id=run.id,
            title=item.prompt[:80],
            goal=item.prompt,
            dependencies=deps,
            scope=item.expected_files,
        )
        await store.tasks.save(task)
        created.append(task)

    # Pre-route all tasks through bandit
    assignments: dict[str, str] = {}
    escalation_meta: dict[str, dict] = {}
    hints: dict[str, str | None] = {}
    for task, item in zip(created, req.tasks):
        agent = router.route(task)
        agent, flags = _gateway_escalation(router, agent, adapter_reg)
        assignments[task.id] = agent
        escalation_meta[task.id] = flags
        hints[task.id] = item.capability_hint

    sequential_s = 0.0

    async def _run_single(task: Task, agent: str) -> dict:
        nonlocal sequential_s
        from ..store.metrics import _classify_bucket
        from ..routing.quality import score_quality_detailed as _score_quality_detailed

        adapter = adapter_reg.get(agent)
        t_run = dataclasses.replace(task, preferred_worker_type=adapter.worker_id) if adapter else task
        await store.tasks.update_status(t_run.id, TaskStatus.ready)

        bucket = _classify_bucket(task.goal, hint=hints.get(task.id))
        task_index = next((i for i, t in enumerate(created) if t.id == task.id), -1)
        t0 = _time.time()
        _run_exc: Exception | None = None
        # F2: same pool integration as /api/run/.../execute. WaveExecutor
        # already enforces dependency + file-overlap constraints; the
        # ExecutionPool adds the global concurrency cap that's shared
        # across all execution sites and feeds queue_depth_norm.
        from ..routing.execution_pool import (
            get_default_pool as _get_default_pool,
            resolve_task_timeout as _resolve_task_timeout,
        )
        _pool = _get_default_pool()
        try:
            async with _pool.acquire(agent):
                await asyncio.wait_for(
                    _run_task(t_run.id, store, registry, verifier),
                    timeout=_resolve_task_timeout(),
                )
        except asyncio.TimeoutError as exc:
            _run_exc = exc
        except Exception as exc:
            _run_exc = exc
        finally:
            elapsed = round(_time.time() - t0, 2)
            sequential_s += elapsed

        # Worker token/cost metrics from executor side-channel (real cost for cloud arms)
        worker_m = pop_task_metrics(t_run.id)
        task_cost_usd = resolve_cost(worker_m)
        # Guarded: this fires before the bandit observe below — a ledger
        # hiccup must not kill the learning update or abort the batch.
        if task_cost_usd > 0 and _cost_ledger is not None:
            try:
                await _cost_ledger.record(
                    user_id="web-user",
                    mission_id=mission.id,
                    model=worker_m.get("model", "") or agent,
                    input_tokens=int(worker_m.get("prompt_tokens") or 0),
                    output_tokens=int(worker_m.get("tokens") or 0),
                    cache_read_tokens=int(worker_m.get("cache_read_tokens") or 0),
                    cost_usd=task_cost_usd,
                )
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "cost ledger record failed for batch task %s: %s", t_run.id, exc
                )

        if _run_exc is not None:
            outcome = TaskOutcome(
                success=False,
                latency_s=elapsed,
                cost_usd=task_cost_usd,
                quality_score=0.0,
                agent_name=agent,
                bucket=bucket,
                spawn_time_ms=0.0,
            )
            try:
                router.observe(task, outcome)
            except Exception:
                pass  # never let bandit updates break responses
            return {
                "task_index": task_index,
                "status": "failed",
                "agent": agent,
                "resource_group": get_resource_group(agent),
                "elapsed_s": elapsed,
                "output": "",
            }

        t_result = await store.tasks.get(t_run.id)
        artifacts = await store.artifacts.list_by_task(t_run.id)
        output = next(
            (a.location.get("content", "") for a in artifacts if a.type == "text_output"), ""
        )
        status = "success" if t_result.status == TaskStatus.completed else "failed"
        success = status == "success"

        # Feed outcome back to bandit — same path as single-task endpoint
        if success:
            quality_score, quality_components = await _score_quality_detailed(task.goal, output, bucket)
            # A2 aggressive_verify: stricter quality threshold flips
            # success=False when below 0.70 (per spec). This gives the
            # bandit a stronger negative signal on borderline outputs.
            from ..routing.escalation_strategies import (
                STRICT_VERIFY_QUALITY_THRESHOLD,
            )
            if escalation_meta.get(task.id, {}).get("strict_verify") and (
                quality_score < STRICT_VERIFY_QUALITY_THRESHOLD
            ):
                success = False
                status = "failed"
        else:
            quality_score, quality_components = 0.0, None
        outcome = TaskOutcome(
            success=success,
            latency_s=elapsed,
            cost_usd=task_cost_usd,
            quality_score=quality_score,
            agent_name=agent,
            bucket=bucket,
            spawn_time_ms=0.0,
            quality_components=quality_components,
        )
        try:
            router.observe(task, outcome)
        except Exception:
            pass  # never let bandit updates break responses

        return {
            "task_index": task_index,
            "status": status,
            "agent": agent,
            "resource_group": get_resource_group(agent),
            "elapsed_s": elapsed,
            "output": output,
        }

    if req.parallel:
        from .wave_executor import WaveExecutor
        wave_exec = WaveExecutor(max_concurrent=req.max_concurrent)
        all_results = await wave_exec.execute_batch(created, assignments, _run_single)
        waves_executed = max((r.get("wave", 1) for r in all_results), default=1)
    else:
        # Sequential fallback (parallel=false safety valve)
        all_results = []
        for i, task in enumerate(created):
            result = await _run_single(task, assignments[task.id])
            result["wave"] = i + 1
            all_results.append(result)
        waves_executed = len(all_results)

    total_elapsed = round(_time.time() - t_batch_start, 2)
    speedup = round(sequential_s / total_elapsed, 2) if total_elapsed > 0 else 1.0

    return {
        "batch_id": batch_id,
        "total_wall_clock_s": total_elapsed,
        "sequential_estimate_s": round(sequential_s, 2),
        "speedup": f"{speedup}x",
        "waves_executed": waves_executed,
        "results": sorted(all_results, key=lambda r: r.get("task_index", 0)),
    }


# ── SPA catch-all ──────────────────────────────────────────────────────────
# Registered last so every other route wins first. Any path that isn't an
# API endpoint, static asset, or streaming endpoint falls through here and
# gets the React shell — so `/observatory`, `/chat`, etc. survive hard reload.
# The SPA catch-all only runs for GETs that no earlier route matched. Since
# `/chat` is POST-only, a GET `/chat` reaches this handler — and we want it
# to serve the SPA so the React route loads on hard-reload. Only namespace
# prefixes that are exclusively API / static belong in this exclude list.
_SPA_EXCLUDE_PREFIXES = (
    "api/", "assets/", "static/", "logs/", "missions/",
    "runs/", "settings/", "approvals/", "batch/", "tasks/",
)


@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa_catch_all(full_path: str) -> HTMLResponse:
    if any(full_path.startswith(p) for p in _SPA_EXCLUDE_PREFIXES):
        raise HTTPException(status_code=404, detail="not found")
    dist_index = _FRONTEND_DIST / "index.html"
    if dist_index.exists():
        return HTMLResponse(content=dist_index.read_text())
    index_path = _STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text())
    raise HTTPException(status_code=404, detail="SPA not built")
