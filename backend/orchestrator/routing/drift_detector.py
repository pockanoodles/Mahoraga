"""
F5 — Drift detection for agent reward distributions.

Spec: docs/specs/v2-debug-F1-F4.md §F5.

Watches per-(bucket, agent) reward streams and fires `DriftAlert` when
the rolling-window mean drops more than `sigma_threshold` standard
deviations below the all-time mean. The bandit's dLinUCB discount
(γ=0.98) eventually decays bad rewards, but it takes ~50 episodes to
meaningfully reduce a broken agent's score. Drift detection catches
acute degradations in ~10 episodes, and pairs with `routing.quarantine`
to route around the broken cell until evidence of recovery.

Welford's online algorithm maintains all-time mean/variance in O(1) per
update with no list of historical values — important because we don't
want detector overhead to scale with episode count.

Env config:
  MAHORAGA_DRIFT_ENABLED=1               # master gate; on by default
  MAHORAGA_DRIFT_WINDOW=50               # rolling-window size
  MAHORAGA_DRIFT_SIGMA=2.0               # σ threshold for alert
  MAHORAGA_DRIFT_MIN_OBS=20              # min observations before checking
  MAHORAGA_DRIFT_CHECK_INTERVAL=10       # check every N observations per cell
"""
from __future__ import annotations

import logging
import math
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

_log = logging.getLogger(__name__)

DEFAULT_WINDOW = 50
DEFAULT_SIGMA = 2.0
DEFAULT_MIN_OBS = 20
DEFAULT_CHECK_INTERVAL = 10


def _read_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _read_int_env(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        if lo <= v <= hi:
            return v
    except ValueError:
        pass
    return default


def _read_float_env(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
        if lo <= v <= hi:
            return v
    except ValueError:
        pass
    return default


def resolve_enabled() -> bool:
    return _read_bool_env("MAHORAGA_DRIFT_ENABLED", default=True)


def resolve_window() -> int:
    return _read_int_env("MAHORAGA_DRIFT_WINDOW", DEFAULT_WINDOW, 5, 5000)


def resolve_sigma() -> float:
    return _read_float_env("MAHORAGA_DRIFT_SIGMA", DEFAULT_SIGMA, 0.5, 10.0)


def resolve_min_obs() -> int:
    return _read_int_env("MAHORAGA_DRIFT_MIN_OBS", DEFAULT_MIN_OBS, 1, 10000)


def resolve_check_interval() -> int:
    return _read_int_env(
        "MAHORAGA_DRIFT_CHECK_INTERVAL", DEFAULT_CHECK_INTERVAL, 1, 1000,
    )


# ── Welford running stats ─────────────────────────────────────────────────────


@dataclass
class RunningStats:
    """Welford's online algorithm for one-pass mean/variance.

    O(1) per update; no list of values retained. Standard deviation is
    `sqrt(m2 / (count - 1))` (sample variance). Returns +inf for std
    when count < 2 so single-observation cells can never satisfy the
    "below historical lower bound" comparison.
    """
    count: int = 0
    mean: float = 0.0
    m2: float = 0.0  # sum of squared deltas; variance = m2 / (count - 1)

    def update(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        delta2 = value - self.mean
        self.m2 += delta * delta2

    @property
    def std(self) -> float:
        if self.count < 2:
            return float("inf")
        return math.sqrt(self.m2 / (self.count - 1))

    def lower_bound(self, sigma: float) -> float:
        return self.mean - sigma * self.std


# ── Alert ────────────────────────────────────────────────────────────────────


@dataclass
class DriftAlert:
    """A regression in (bucket, agent) reward worth quarantining for.

    `deviation_sigmas` is the magnitude in σ-units for ranking — used
    when "all agents quarantined" forces a least-bad fallback. Smaller
    deviation = closer to historical mean = least-bad.
    """
    bucket: str
    agent: str
    window_mean: float
    historical_mean: float
    historical_std: float
    deviation_sigmas: float
    window_size: int

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "agent": self.agent,
            "window_mean": round(self.window_mean, 4),
            "historical_mean": round(self.historical_mean, 4),
            "historical_std": round(self.historical_std, 4),
            "deviation_sigmas": round(self.deviation_sigmas, 4),
            "window_size": self.window_size,
        }


# ── Detector ──────────────────────────────────────────────────────────────────


@dataclass
class DriftDetector:
    """Per-(bucket, agent) drift watcher.

    State (`_windows`, `_historical`) lives in-process. It rebuilds
    organically over a few episodes per cell after a restart, so we
    don't bother persisting it — pre-restart drift events stay in the
    `drift_events` DB table for audit; the detector starts fresh.
    """
    window_size: int = field(default_factory=resolve_window)
    sigma_threshold: float = field(default_factory=resolve_sigma)
    min_observations: int = field(default_factory=resolve_min_obs)
    check_interval: int = field(default_factory=resolve_check_interval)
    _windows: dict[tuple[str, str], deque[float]] = field(default_factory=dict)
    _historical: dict[tuple[str, str], RunningStats] = field(default_factory=dict)

    def check(
        self,
        bucket: str,
        agent: str,
        reward: float,
    ) -> Optional[DriftAlert]:
        """Update stats with a new observation and return an alert if drift fired.

        Updating happens unconditionally — the alert just gates on
        `min_observations` (need enough history before we trust the
        mean) and `check_interval` (rate-limit alerts so we don't spam
        on consecutive bad rewards).
        """
        key = (bucket, agent)
        hist = self._historical.setdefault(key, RunningStats())
        hist.update(reward)

        win = self._windows.setdefault(
            key, deque(maxlen=self.window_size),
        )
        win.append(reward)

        if hist.count < self.min_observations:
            return None
        if hist.count % self.check_interval != 0:
            return None
        if hist.std == float("inf") or hist.std == 0.0:
            return None

        window_mean = sum(win) / len(win)
        lower = hist.lower_bound(self.sigma_threshold)
        if window_mean >= lower:
            return None

        return DriftAlert(
            bucket=bucket,
            agent=agent,
            window_mean=window_mean,
            historical_mean=hist.mean,
            historical_std=hist.std,
            deviation_sigmas=(hist.mean - window_mean) / max(hist.std, 1e-9),
            window_size=len(win),
        )

    def stats_for(self, bucket: str, agent: str) -> Optional[RunningStats]:
        return self._historical.get((bucket, agent))

    def cells(self) -> list[tuple[str, str]]:
        return list(self._historical.keys())
