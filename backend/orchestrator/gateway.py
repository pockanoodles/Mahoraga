"""Gateway — wire channel messages through the full orchestrator pipeline."""
from __future__ import annotations

import dataclasses
import logging
import time as _time_log
import uuid as _uuid
from typing import AsyncGenerator

from .adaptive.learner import Learner
from .adaptive.models import AdaptationCategory, UserAdaptation, UserProfile
from .adaptive.profile import build_profile_prompt
from .channels.base import ChannelMessage
from .config import ENABLED_BACKENDS, MahoragaConfig
from .domain.models import Mission, Plan, Run, RunMode, Task, TaskStatus
from .planning.classifier import classify_tier
from .planning.planner import PlannerError, generate_tasks
from .service.executor import run_task
from .store.base import Store
from .store.chat_log import ChatLogEntry
from .adapters.registry import AdapterRegistry
from .workers.registry import WorkerRegistry
from .workers.router import TaskRouter, _CODE_KEYWORDS, _PLANNING_KEYWORDS
from .verifier.verifier import Verifier
from .brain_logger import log_task_completion
from .routing import TaskOutcome

logger = logging.getLogger(__name__)


def _worker_id_to_caps(worker_id: str | None) -> list[str]:
    """Map a worker_id to the required capabilities for escalation filtering.

    When a task escalates (worker fails), assign_worker uses required_capabilities
    to pick the next worker. Without this, all workers match (empty list), and
    Ollama (registered first) always wins — even for code tasks that need Aider.
    """
    if worker_id in ("aider:default", "codex:cli"):
        return ["code"]
    if worker_id and worker_id.startswith("claude:"):
        return ["general"]
    if worker_id and worker_id.startswith("ollama:"):
        if ":coder" in worker_id:
            return ["code"]
        if ":planner" in worker_id:
            return ["plan"]
        return ["general"]
    return []


class Gateway:
    """Routes channel messages through Mission → Plan → Run → Tasks pipeline."""

    def __init__(
        self,
        store: Store,
        registry: WorkerRegistry,
        verifier: Verifier,
        adaptive_store=None,
        cost_ledger=None,
        config: MahoragaConfig | None = None,
        adapter_registry: AdapterRegistry | None = None,
        bandit_router=None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._verifier = verifier
        self._adaptive = adaptive_store
        self._cost_ledger = cost_ledger
        self._learner = Learner()
        self._config = config or MahoragaConfig()
        self._router = TaskRouter()
        self._adapter_registry = adapter_registry
        self._bandit_router = bandit_router

    async def handle_message(self, msg: ChannelMessage) -> AsyncGenerator[str, None]:
        """Process a channel message through the full pipeline.

        Yields response chunks (task summaries) as they complete.
        After all chunks are yielded, runs the adaptive learning loop
        in a fire-and-forget manner (errors are swallowed).
        """
        # ── 1. User profile + adaptation ─────────────────────────────────────
        user_profile_str: str | None = None
        existing_adaptations: list = []

        if self._adaptive is not None:
            profile = await self._adaptive.get_profile(msg.user_id)
            if profile is None:
                profile = UserProfile.new(msg.user_id)
                await self._adaptive.save_profile(profile)

            existing_adaptations = await self._adaptive.list_adaptations(msg.user_id)
            user_profile_str = build_profile_prompt(existing_adaptations)

        # ── 2. Create Mission ─────────────────────────────────────────────────
        mission = Mission.new(
            title=f"Message from {msg.user_id}",
            goal=msg.text,
        )
        await self._store.missions.save(mission)
        logger.info("gateway: created mission %s for user %s", mission.id, msg.user_id)

        # ── 3. Classify tier + generate tasks ────────────────────────────────
        tier = classify_tier(mission.title, mission.goal)
        logger.info("gateway: mission %s classified as tier %d", mission.id, tier)

        if tier <= 2:
            # Skip planner — wrap the whole mission as a single task
            tasks = [
                Task.new(
                    run_id="__pending__",
                    title=mission.title,
                    goal=mission.goal,
                    done_criteria=mission.success_condition or "",
                    context_refs=[],
                    constraints=mission.global_constraints or [],
                )
            ]
        else:
            # Tier 3 — decompose via planner
            try:
                tasks = await generate_tasks(
                    mission, run_id="__pending__", user_profile=user_profile_str
                )
            except PlannerError as exc:
                logger.error("gateway: planner error for mission %s: %s", mission.id, exc)
                yield f"[Planner error: {exc}]"
                return

        # ── Route tasks to Ollama workers if ollama backend ───────────────
        active_backend = self._config.get("active_backend")
        routed_tasks = []
        for t in tasks:
            worker_id = await self._route_task(t, active_backend)
            # Propagate required_capabilities so escalation stays within
            # the right worker class (code tasks don't fall back to ollama:general).
            required_caps = _worker_id_to_caps(worker_id)
            routed_tasks.append(dataclasses.replace(
                t,
                preferred_worker_type=worker_id,
                required_capabilities=required_caps,
            ))
        tasks = routed_tasks

        # ── 4. Create Plan + Run ─────────────────────────────────────────────
        plan = Plan.new(mission_id=mission.id)
        run = Run.new(mission_id=mission.id, plan_id=plan.id, mode=RunMode.direct)
        await self._store.missions.save_plan(plan)
        await self._store.missions.save_run(run)

        # ── 5. Save tasks with correct run_id ────────────────────────────────
        saved_tasks = []
        for task in tasks:
            task = dataclasses.replace(task, run_id=run.id)
            # Tasks without dependencies start ready; others stay pending
            if not task.dependencies:
                task = dataclasses.replace(task, status=TaskStatus.ready)
            await self._store.tasks.save(task)
            saved_tasks.append(task)

        logger.info(
            "gateway: run %s created with %d tasks", run.id, len(saved_tasks)
        )

        # ── 6. Execute tasks + yield output ──────────────────────────────────
        response_chunks: list[str] = []

        for task in saved_tasks:
            # Re-fetch task status — dependency resolution may have updated it
            current = await self._store.tasks.get(task.id)
            if current is None:
                continue
            if current.status != TaskStatus.ready:
                # Only execute ready tasks — pending means unmet dependencies
                continue

            _run_task_exc: Exception | None = None
            try:
                await run_task(task.id, self._store, self._registry, self._verifier)
            except Exception as exc:
                _run_task_exc = exc
                logger.error("gateway: run_task error for %s: %s", task.id, exc)
                chunk = f"[Task '{task.title}' failed: {exc}]"
                response_chunks.append(chunk)
                yield chunk

            # Always fetch attempts so the bandit gets attribution even on
            # failure paths (preserves learning signal for the selected agent).
            attempts = await self._store.tasks.list_attempts(task.id)
            completed = [a for a in attempts if a.status.value == "completed"]

            if _run_task_exc is None:
                logger.info("GATEWAY ATTEMPT OUTPUT: %s", [a.output[:100] for a in completed])
                if completed:
                    attempt = completed[-1]
                    output = attempt.output or attempt.summary
                    if output:
                        response_chunks.append(output)
                        yield output
                        try:
                            _duration = (
                                attempt.ended_at - attempt.started_at
                                if attempt.started_at is not None and attempt.ended_at is not None
                                else None
                            )
                            _quality = 1.0 if attempt.status.value == "completed" else 0.0
                            log_task_completion(
                                task_title=task.title or mission.title,
                                task_goal=task.goal or "",
                                agent_used=attempt.worker_id or "unknown",
                                output_preview=output[:500] if output else "",
                                cost=0.0,
                                quality_score=_quality,
                                duration_seconds=_duration,
                            )
                        except Exception:
                            pass  # Never let logging break the main flow

            if self._bandit_router is not None:
                if _run_task_exc is None and completed:
                    attempt = completed[-1]
                    bandit_outcome = TaskOutcome(
                        success=(attempt.status.value == "completed"),
                        latency_s=0.0,
                        cost_usd=0.0,
                        quality_score=1.0 if attempt.status.value == "completed" else 0.0,
                        agent_name=attempt.worker_id or "unknown",
                    )
                else:
                    # Exception raised, or no completed attempt (escalated /
                    # blocked / retry-exhausted). Attribute to the latest
                    # attempt's worker if any was made, else "unknown".
                    fallback_agent = attempts[-1].worker_id if attempts else "unknown"
                    bandit_outcome = TaskOutcome(
                        success=False,
                        latency_s=0.0,
                        cost_usd=0.0,
                        quality_score=0.0,
                        agent_name=fallback_agent or "unknown",
                    )
                try:
                    self._bandit_router.observe(task, bandit_outcome)
                except Exception:
                    pass  # never let bandit updates break responses

            if _run_task_exc is not None:
                continue

        # ── 7. Adaptive learning (fire-and-forget) ───────────────────────────
        full_response = "\n".join(response_chunks)
        if self._adaptive is not None:
            try:
                new_adaptations = await self._learner.analyze_interaction(
                    user_message=msg.text,
                    assistant_response=full_response,
                    existing_adaptations=existing_adaptations,
                )
                for raw in new_adaptations:
                    try:
                        category = AdaptationCategory(raw["category"])
                        adapt = UserAdaptation.new(
                            user_id=msg.user_id,
                            category=category,
                            key=raw["key"],
                            value=raw["value"],
                            confidence=float(raw.get("confidence", 0.8)),
                        )
                        await self._adaptive.save_adaptation(adapt)
                    except Exception:
                        pass  # never let a bad adaptation record break anything
            except Exception:
                pass  # learning must never break responses

        # ── Persist chat log entry ────────────────────────────────────────────
        try:
            last_worker_id = ""
            for task in saved_tasks:
                attempts = await self._store.tasks.list_attempts(task.id)
                completed = [a for a in attempts if a.status.value == "completed"]
                if completed:
                    last_worker_id = completed[-1].worker_id
            log_entry = ChatLogEntry(
                id=str(_uuid.uuid4()),
                user_id=msg.user_id,
                mission_id=mission.id,
                user_message=msg.text,
                assistant_response=full_response,
                worker_id=last_worker_id,
                cost_usd=0.0,
                created_at=_time_log.time(),
            )
            await self._store.chat_log.save(log_entry)
        except Exception as exc:
            logger.warning("chat log persist failed: %s", exc)

    async def _route_task(self, task: Task, active_backend: str) -> str | None:
        """Determine preferred_worker_type for a task.

        Order:
        1. Determine required capability from task keywords.
        2. BanditRouter picks from capable-only agents (prevents cold-start
           from routing code tasks to general agents).
        3. AdapterRegistry capability-based routing (health-checked, scored).
        4. Keyword-based Ollama fallback.
        """
        if self._adapter_registry is not None:
            text = f"{task.title} {task.goal}".lower()
            words = set(text.split())
            if any(kw in words for kw in _CODE_KEYWORDS):
                capability = "code"
            elif any(kw in words for kw in _PLANNING_KEYWORDS):
                capability = "plan"
            else:
                capability = "general"

            logger.info("[ROUTE] capability=%r | text=%r", capability, text[:120])

            # Bandit picks from capable agents only — never routes a code task
            # to a non-code-capable agent during cold start.
            if self._bandit_router is not None:
                capable_names = [
                    a.name for a, _ in self._adapter_registry.find_capable(capability)
                ]
                logger.info("[ROUTE] candidates for %r: %s", capability, capable_names)
                if capable_names:
                    try:
                        agent_name = self._bandit_router.route(task, capable_names)
                        scores = self._bandit_router.strategy.get_scores()
                        logger.info("[ROUTE] UCB scores: %s", scores)
                        logger.info("[ROUTE] bandit selected: %r", agent_name)
                        adapter = self._adapter_registry.get(agent_name)
                        if adapter is not None:
                            worker_id = self._resolve_worker_id(adapter, capability)
                            logger.info("[ROUTE] resolved → %s", worker_id)
                            return worker_id
                    except Exception as exc:
                        logger.warning("[ROUTE] bandit error: %s", exc)
                        # fall through to capability-based routing

            adapter = await self._adapter_registry.route(task, required_capability=capability)
            if adapter is not None:
                worker_id = self._resolve_worker_id(adapter, capability)
                logger.info("[ROUTE] adapter-registry fallback: %s → %s", adapter.name, worker_id)
                return worker_id

        # Fallback: keyword-based Ollama routing
        if active_backend == "ollama" or "claude" not in ENABLED_BACKENDS:
            worker_id = self._router.route(task, "ollama")
            logger.info("[ROUTE] keyword-ollama fallback → %s", worker_id)
            return worker_id

        return None

    def _resolve_worker_id(self, adapter, capability: str) -> str:
        """Map an Ollama adapter to the right sub-worker for the capability.

        Each Ollama adapter (ollama:qwen3-4b, ollama:gemma4-e4b, etc.) has
        four sub-workers — one per role-prompt (planner / coder / fast /
        general). Adapter selection is the bandit arm; role selection is a
        deterministic capability → role mapping below the bandit.
        """
        role_for_capability = {"code": "coder", "plan": "planner"}
        if adapter.name == "ollama":  # legacy single-model adapter
            return f"ollama:{role_for_capability.get(capability, 'general')}"
        if adapter.name.startswith("ollama:"):
            role = role_for_capability.get(capability, "general")
            return f"{adapter.name}:{role}"
        return adapter.worker_id
