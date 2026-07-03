"""
F5 — Quarantine manager + recovery probes.

Spec: docs/specs/v2-debug-F1-F4.md §F5.

When the drift detector fires a `DriftAlert`, this module records the
quarantine entry and exposes:

  - `is_quarantined(bucket, agent)` — used by `route()` to filter the
    candidate set before bandit selection.
  - `next_probe_target(bucket)` — when the probe scheduler ticks, this
    returns a quarantined agent to route the next matching task to as
    a recovery probe.
  - `record_probe(bucket, agent, success)` — accounts for probe outcomes;
    after `auto_release_threshold` consecutive successes, the cell is
    auto-released.
  - `quarantine(alert)` / `release(bucket, agent)` — manual hooks for
    operator workflows + the CLI.

State persists in ~/.mahoraga-v2/quarantine.json so quarantines survive
FastAPI restarts (a broken agent that just got quarantined shouldn't be
forgotten on the next reload).

Env config:
  MAHORAGA_QUARANTINE_ENABLED=1
  MAHORAGA_QUARANTINE_PROBE_INTERVAL=50      # tasks per bucket between probes
  MAHORAGA_QUARANTINE_AUTO_RELEASE=3         # consecutive probe successes
  MAHORAGA_QUARANTINE_PROBE_QUALITY_FLOOR=0.50  # reward ≥ this counts as success
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .drift_detector import DriftAlert

_log = logging.getLogger(__name__)

QUARANTINE_STATE_PATH = Path.home() / ".mahoraga-v2" / "quarantine.json"

DEFAULT_PROBE_INTERVAL = 50
DEFAULT_AUTO_RELEASE = 3
DEFAULT_PROBE_QUALITY_FLOOR = 0.50


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
    return _read_bool_env("MAHORAGA_QUARANTINE_ENABLED", default=True)


def resolve_probe_interval() -> int:
    return _read_int_env(
        "MAHORAGA_QUARANTINE_PROBE_INTERVAL", DEFAULT_PROBE_INTERVAL, 1, 10000,
    )


def resolve_auto_release() -> int:
    return _read_int_env(
        "MAHORAGA_QUARANTINE_AUTO_RELEASE", DEFAULT_AUTO_RELEASE, 1, 1000,
    )


def resolve_probe_quality_floor() -> float:
    return _read_float_env(
        "MAHORAGA_QUARANTINE_PROBE_QUALITY_FLOOR",
        DEFAULT_PROBE_QUALITY_FLOOR, 0.0, 1.0,
    )


# ── Entry ────────────────────────────────────────────────────────────────────


@dataclass
class QuarantineEntry:
    """One quarantined (bucket, agent) cell and its probe history."""
    bucket: str
    agent: str
    quarantined_at: str       # ISO 8601 UTC
    reason_kind: str          # "drift_auto" | "manual" | "probe_failure"
    deviation_sigmas: float
    historical_mean: float
    window_mean: float
    probe_attempts: int = 0
    probe_successes: int = 0
    last_probe_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Manager ──────────────────────────────────────────────────────────────────


@dataclass
class QuarantineManager:
    """Per-(bucket, agent) quarantine state with probe scheduling.

    Probe scheduling is deliberately tick-driven, not time-driven: every
    `probe_interval`-th routing decision in a bucket where any agent is
    quarantined sends the task to a quarantined agent as a recovery
    probe. This couples probe rate to task rate (no probes when there's
    no traffic) and avoids the complication of background timer tasks.

    The bucket counter lives in this manager so the scheduling state
    persists alongside the quarantine entries.
    """
    probe_interval: int = field(default_factory=resolve_probe_interval)
    auto_release: int = field(default_factory=resolve_auto_release)
    probe_quality_floor: float = field(
        default_factory=resolve_probe_quality_floor,
    )
    entries: dict[str, QuarantineEntry] = field(default_factory=dict)
    bucket_ticks: dict[str, int] = field(default_factory=dict)
    state_path: Path = field(default=QUARANTINE_STATE_PATH)

    @staticmethod
    def _key(bucket: str, agent: str) -> str:
        return f"{bucket}::{agent}"

    # ── Read API ──────────────────────────────────────────────────────────────

    def is_quarantined(self, bucket: str, agent: str) -> bool:
        return self._key(bucket, agent) in self.entries

    def quarantined_in_bucket(self, bucket: str) -> list[str]:
        return [
            e.agent for e in self.entries.values() if e.bucket == bucket
        ]

    def all_entries(self) -> list[QuarantineEntry]:
        return list(self.entries.values())

    # ── Mutating API ──────────────────────────────────────────────────────────

    def quarantine(self, alert: DriftAlert, kind: str = "drift_auto") -> QuarantineEntry:
        """Add (bucket, agent) to quarantine, idempotent: re-quarantining
        an already-quarantined cell refreshes the timestamp + reason but
        preserves the probe history (so a flapping agent doesn't get
        infinitely fresh probe budget)."""
        key = self._key(alert.bucket, alert.agent)
        existing = self.entries.get(key)
        entry = QuarantineEntry(
            bucket=alert.bucket,
            agent=alert.agent,
            quarantined_at=datetime.now(timezone.utc).isoformat(),
            reason_kind=kind,
            deviation_sigmas=float(alert.deviation_sigmas),
            historical_mean=float(alert.historical_mean),
            window_mean=float(alert.window_mean),
            probe_attempts=existing.probe_attempts if existing else 0,
            probe_successes=existing.probe_successes if existing else 0,
            last_probe_at=existing.last_probe_at if existing else None,
        )
        self.entries[key] = entry
        _log.info(
            "quarantine: %s/%s (kind=%s, σ=%.2f, hist_mean=%.3f, window_mean=%.3f)",
            alert.bucket, alert.agent, kind,
            alert.deviation_sigmas, alert.historical_mean, alert.window_mean,
        )
        return entry

    def manual_quarantine(
        self,
        bucket: str,
        agent: str,
        reason: str = "manual",
    ) -> QuarantineEntry:
        """Operator-driven quarantine without a DriftAlert (e.g. you
        know a service is down and want to pre-empt routing)."""
        synthetic = DriftAlert(
            bucket=bucket,
            agent=agent,
            window_mean=0.0,
            historical_mean=0.0,
            historical_std=0.0,
            deviation_sigmas=0.0,
            window_size=0,
        )
        return self.quarantine(synthetic, kind=reason)

    def release(self, bucket: str, agent: str) -> bool:
        key = self._key(bucket, agent)
        if key in self.entries:
            del self.entries[key]
            _log.info("quarantine: released %s/%s", bucket, agent)
            return True
        return False

    # ── Probe scheduling ──────────────────────────────────────────────────────

    def _tick_bucket(self, bucket: str) -> int:
        self.bucket_ticks[bucket] = self.bucket_ticks.get(bucket, 0) + 1
        return self.bucket_ticks[bucket]

    def maybe_probe(self, bucket: str, available: list[str]) -> Optional[str]:
        """Return a quarantined agent to probe on the current task, or None.

        Conditions for a probe to fire:
          - There's at least one quarantined agent in this bucket.
          - That agent is currently in the `available` set (it hasn't
            been removed from the registry).
          - The bucket's tick counter is at a probe_interval boundary.
        Returns the agent to probe; caller routes the task to that agent
        and reports the outcome via `record_probe`.

        Picks the cell with the most consecutive successes (closest to
        release) to reduce the chance of flapping back and forth.
        """
        tick = self._tick_bucket(bucket)
        if tick % self.probe_interval != 0:
            return None
        candidates = [
            e for e in self.entries.values()
            if e.bucket == bucket and e.agent in available
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda e: e.probe_successes, reverse=True)
        return candidates[0].agent

    def record_probe(
        self,
        bucket: str,
        agent: str,
        reward: float,
    ) -> Optional[str]:
        """Account for a probe outcome. Returns one of:
          - "released" — auto-release threshold reached, cell removed.
          - "progressed" — success but not yet released.
          - "failed"     — reward below quality floor, success counter reset.
          - None         — no quarantine entry for this cell.
        """
        key = self._key(bucket, agent)
        entry = self.entries.get(key)
        if entry is None:
            return None
        entry.probe_attempts += 1
        entry.last_probe_at = datetime.now(timezone.utc).isoformat()
        if reward >= self.probe_quality_floor:
            entry.probe_successes += 1
            if entry.probe_successes >= self.auto_release:
                del self.entries[key]
                _log.info(
                    "quarantine: auto-released %s/%s after %d successful probes",
                    bucket, agent, entry.probe_successes,
                )
                return "released"
            return "progressed"
        entry.probe_successes = 0
        return "failed"

    # ── Fallback ──────────────────────────────────────────────────────────────

    def least_bad_in_bucket(self, bucket: str) -> Optional[str]:
        """Used when every agent in a bucket is quarantined and the
        bandit has nothing to pick. Returns the agent with the smallest
        deviation_sigmas — the "least broken" of the available bad
        options. Caller is responsible for routing to it; this is the
        emergency fallback the spec calls out."""
        bucket_entries = [e for e in self.entries.values() if e.bucket == bucket]
        if not bucket_entries:
            return None
        bucket_entries.sort(key=lambda e: e.deviation_sigmas)
        return bucket_entries[0].agent

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self, path: Optional[Path] = None) -> None:
        """Persist current state. Defaults to the path the manager was
        loaded from (so a manager loaded from a non-default location
        round-trips back there); falls back to the module-level constant
        when constructed from scratch."""
        target = path if path is not None else self.state_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "entries": {k: asdict(v) for k, v in self.entries.items()},
            "bucket_ticks": dict(self.bucket_ticks),
            "probe_interval": self.probe_interval,
            "auto_release": self.auto_release,
            "probe_quality_floor": self.probe_quality_floor,
        }, indent=2))

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "QuarantineManager":
        """Load persisted state, with env-resolved values winning over
        persisted config (so changing PROBE_INTERVAL takes effect on
        restart). Entries + bucket_ticks come from disk. The manager
        records the load path so subsequent save() calls round-trip
        back to the same location without explicit path threading.

        Default path is resolved at CALL time (not def time) so test
        monkeypatching `QUARANTINE_STATE_PATH` works correctly — Python
        binds default arg expressions at def time, which is wrong for
        a module-level constant we want to be patchable."""
        if path is None:
            path = QUARANTINE_STATE_PATH
        if not Path(path).exists():
            return cls(state_path=Path(path))
        try:
            data = json.loads(Path(path).read_text())
        except (json.JSONDecodeError, OSError) as exc:
            _log.warning("quarantine: failed to load %s (%s)", path, exc)
            return cls(state_path=Path(path))
        entries = {}
        for k, v in data.get("entries", {}).items():
            try:
                entries[k] = QuarantineEntry(**v)
            except TypeError:
                continue
        return cls(
            probe_interval=resolve_probe_interval(),
            auto_release=resolve_auto_release(),
            probe_quality_floor=resolve_probe_quality_floor(),
            entries=entries,
            bucket_ticks=dict(data.get("bucket_ticks", {})),
            state_path=Path(path),
        )
