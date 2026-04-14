# backend/orchestrator/routing/implicit_quality.py
"""
Implicit quality signals from user behavior.

Two signals:
  - Retry (0.0): same task_hash within 5 minutes → user was unsatisfied
  - Accept (0.6): different task within 10 minutes after completion → user moved on

These don't replace heuristic quality scores — they calibrate them.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional

_RETRY_WINDOW_S  = 300.0   # 5 minutes
_ACCEPT_WINDOW_S = 600.0   # 10 minutes
_RETRY_SIGNAL    = 0.0
_ACCEPT_SIGNAL   = 0.6


@dataclass
class _PendingTask:
    task_id: str
    task_hash: str
    completed_at: float


class ImplicitQualityTracker:
    """In-memory tracker. One instance per server process."""

    def __init__(self) -> None:
        self._pending: Optional[_PendingTask] = None

    def on_task_complete(self, task_id: str, task_hash: str, completed_at: float | None = None) -> None:
        """Record that a task completed."""
        self._pending = _PendingTask(
            task_id=task_id,
            task_hash=task_hash,
            completed_at=completed_at if completed_at is not None else time.time(),
        )

    def on_task_submitted(
        self, task_hash: str, submitted_at: float | None = None
    ) -> Optional[tuple[str, float]]:
        """Called when a new task is submitted. Returns (task_id, signal) or None.

        Retry window: same hash within 5 min → signal = 0.0
        Accept window: different hash within 10 min → signal = 0.6
        Outside both windows → None, pending cleared
        """
        if self._pending is None:
            return None

        t = submitted_at if submitted_at is not None else time.time()
        elapsed = t - self._pending.completed_at
        pending = self._pending

        if elapsed <= _RETRY_WINDOW_S and task_hash == pending.task_hash:
            self._pending = None
            return (pending.task_id, _RETRY_SIGNAL)

        if elapsed <= _ACCEPT_WINDOW_S and task_hash != pending.task_hash:
            self._pending = None
            return (pending.task_id, _ACCEPT_SIGNAL)

        if elapsed > _ACCEPT_WINDOW_S:
            self._pending = None

        return None
