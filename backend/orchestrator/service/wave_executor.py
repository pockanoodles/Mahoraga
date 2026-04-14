"""
Wave executor for concurrent batch task execution.

Groups ready tasks into waves constrained by:
  1. Resource group concurrency limits (ollama+aider share local_ollama, max=1)
  2. File-path overlap (tasks writing same file → different waves)
  3. Global max_concurrent cap (default: 2)

Each wave runs with asyncio.gather; waves execute sequentially.
"""
from __future__ import annotations
import asyncio
import logging

from ..resource_groups import get_resource_group, get_group_concurrency

logger = logging.getLogger(__name__)


class WaveExecutor:
    def __init__(self, max_concurrent: int = 2):
        self.max_concurrent = max_concurrent

    def _build_waves(self, ready_tasks: list, assignments: dict[str, str]) -> list[list]:
        """Partition tasks into concurrently-executable waves.

        A task moves to the next wave if:
        - Its resource group is already at capacity in the current wave, OR
        - The current wave is at the global max_concurrent cap, OR
        - Its expected_files (task.scope) overlap with a file already claimed this wave.
        """
        waves: list[list] = []
        unscheduled = list(ready_tasks)

        while unscheduled:
            wave: list = []
            group_counts: dict[str, int] = {}
            wave_files: set[str] = set()

            for task in list(unscheduled):
                agent = assignments.get(task.id, "unknown")
                group = get_resource_group(agent)
                group_limit = get_group_concurrency(group)
                current_count = group_counts.get(group, 0)

                if current_count >= group_limit:
                    continue
                if len(wave) >= self.max_concurrent:
                    continue

                task_files = set(getattr(task, "scope", None) or [])
                if task_files & wave_files:
                    continue  # file overlap — defer to next wave

                wave.append(task)
                unscheduled.remove(task)
                group_counts[group] = current_count + 1
                wave_files |= task_files

            if not wave:
                # Safety valve: can't schedule any remaining task together — run first one alone
                wave = [unscheduled.pop(0)]
                logger.warning(
                    "wave builder stalled — running task %s alone as fallback", wave[0].id
                )

            waves.append(wave)

        return waves

    async def execute_batch(
        self,
        tasks: list,
        assignments: dict[str, str],
        run_single,
    ) -> list[dict]:
        """Execute all tasks in dependency-aware waves.

        Args:
            tasks: ordered list of task objects (must have .id, .scope, .dependencies)
            assignments: task.id → agent_name (pre-computed by bandit)
            run_single: async callable(task, agent) -> dict result
        Returns:
            list of result dicts in task order
        """
        completed: dict[str, dict] = {}
        remaining = list(tasks)
        wave_num = 0

        while remaining:
            ready = [t for t in remaining if self._deps_satisfied(t, completed)]
            if not ready:
                logger.warning(
                    "no ready tasks with %d remaining — possible dependency cycle", len(remaining)
                )
                break

            waves = self._build_waves(ready, {t.id: assignments.get(t.id, "unknown") for t in ready})

            for wave in waves:
                wave_num += 1
                logger.info(
                    "wave %d: executing %d tasks %s",
                    wave_num, len(wave), [t.id for t in wave],
                )
                wave_results = await asyncio.gather(
                    *[run_single(t, assignments.get(t.id, "unknown")) for t in wave],
                    return_exceptions=True,
                )
                for task, result in zip(wave, wave_results):
                    if isinstance(result, Exception):
                        result = {"status": "failed", "error": str(result), "task_index": 0}
                    completed[task.id] = {**result, "wave": wave_num}
                    remaining.remove(task)

        return [completed[t.id] for t in tasks if t.id in completed]

    @staticmethod
    def _deps_satisfied(task, completed: dict[str, dict]) -> bool:
        for dep in getattr(task, "dependencies", []):
            if dep.task_id not in completed:
                return False
        return True
