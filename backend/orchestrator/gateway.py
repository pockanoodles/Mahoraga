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
from .domain.models import Mission, Plan, Run, RunMode, RunStatus, Task, TaskStatus
from .planning.classifier import classify_tier
from .planning.planner import PlannerError, generate_tasks
from .service.executor import run_task
from .store.base import Store
from .store.chat_log import ChatLogEntry
from .adapters.registry import AdapterRegistry
from .config import ENABLED_BACKENDS
from .workers.registry import WorkerRegistry
from .workers.router import TaskRouter, _CODE_KEYWORDS, _PLANNING_KEYWORDS
from .verifier.verifier import Verifier
from .brain_logger import log_task_completion

logger = logging.getLogger(__name__)


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
            routed_tasks.append(dataclasses.replace(t, preferred_worker_type=worker_id))
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

            try:
                await run_task(task.id, self._store, self._registry, self._verifier)
            except Exception as exc:
                logger.error("gateway: run_task error for %s: %s", task.id, exc)
                chunk = f"[Task '{task.title}' failed: {exc}]"
                response_chunks.append(chunk)
                yield chunk
                continue

            # Collect the latest completed attempt output as a response chunk.
            # Fall back to summary for legacy DB rows where output column is empty.
            attempts = await self._store.tasks.list_attempts(task.id)
            completed = [a for a in attempts if a.status.value == "completed"]
            logger.info("GATEWAY ATTEMPT OUTPUT: %s", [a.output[:100] for a in completed])
            if completed:
                attempt = completed[-1]
                output = attempt.output or attempt.summary
                if output:
                    response_chunks.append(output)
                    yield output
                    try:
                        log_task_completion(
                            task_title=task.title or mission.title,
                            task_goal=task.goal or "",
                            agent_used=attempt.worker_id or "unknown",
                            output_preview=output[:500] if output else "",
                            cost=0.0,
                        )
                    except Exception:
                        pass  # Never let logging break the main flow

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

        Uses AdapterRegistry capability-based routing if available,
        falls back to TaskRouter keyword matching for Ollama-only mode.
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

            adapter = await self._adapter_registry.route(task, required_capability=capability)
            if adapter is not None:
                return adapter.worker_id

        # Fallback: keyword-based Ollama routing
        if active_backend == "ollama" or "claude" not in ENABLED_BACKENDS:
            return self._router.route(task, "ollama")

        return None
