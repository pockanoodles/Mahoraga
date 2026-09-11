"""resource_report.py — plain-language read of resource_sampler.py's log.

Deliberately not a memory-pressure report: high RAM usage while local models
are warm is expected for this workload, not a problem. The signal that
matters is CPU load sustained high enough to spin fans and lag the machine,
plus macOS's own thermal-pressure signal on the rare occasion it fires.

The report's job is to answer one question in one line — fine / strained /
critical — not to hand over raw percentages to interpret. Reading a table of
numbers correctly is exactly the thing this exists to avoid.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .resource_sampler import DEFAULT_LOG
from .time_window import within_window

# A sample counts as "high CPU" at or above this threshold.
HIGH_CPU_PERCENT = 80.0

# Share of samples at/above HIGH_CPU_PERCENT that tips the read to "strained".
_STRAINED_SHARE = 0.2


@dataclass
class ResourceReport:
    samples: int = 0
    cpu_sum: float = 0.0
    cpu_max: float = 0.0
    high_cpu_samples: int = 0
    elevated_thermal_samples: int = 0
    warm_model_counts: Counter = field(default_factory=Counter)
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None

    @property
    def cpu_avg(self) -> Optional[float]:
        if self.samples == 0:
            return None
        return self.cpu_sum / self.samples

    @property
    def high_cpu_share(self) -> Optional[float]:
        if self.samples == 0:
            return None
        return self.high_cpu_samples / self.samples

    @property
    def level(self) -> str:
        """fine / strained / critical — the read a non-sysadmin can act on.

        Thermal escalation always wins over the CPU-share heuristic: macOS
        only ever records a thermal warning level under real pressure, so a
        single occurrence is a harder signal than any CPU percentage.
        """
        if self.samples == 0:
            return "unknown"
        if self.elevated_thermal_samples > 0:
            return "critical"
        if (self.high_cpu_share or 0.0) >= _STRAINED_SHARE:
            return "strained"
        return "fine"

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "cpu_avg": self.cpu_avg,
            "cpu_max": self.cpu_max,
            "high_cpu_share": self.high_cpu_share,
            "elevated_thermal_samples": self.elevated_thermal_samples,
            "level": self.level,
            "warm_models": dict(self.warm_model_counts),
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
        }


def compute_resource_report(
    log_path: Path = DEFAULT_LOG,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> ResourceReport:
    """Aggregate the sampler log. A missing log is an empty report, not an
    error — the common case is "resource logging was never enabled"."""
    report = ResourceReport()
    if not log_path.is_file():
        return report

    with log_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # A torn line from a crashed write is one lost sample, not a
                # reason to refuse to report on the rest.
                continue
            ts = str(row.get("ts") or "")
            if not within_window(ts, since, until):
                continue

            if ts:
                report.first_ts = ts if report.first_ts is None else min(report.first_ts, ts)
                report.last_ts = ts if report.last_ts is None else max(report.last_ts, ts)

            report.samples += 1
            cpu = row.get("cpu_percent")
            if isinstance(cpu, (int, float)):
                report.cpu_sum += cpu
                report.cpu_max = max(report.cpu_max, cpu)
                if cpu >= HIGH_CPU_PERCENT:
                    report.high_cpu_samples += 1
            if row.get("thermal_level") == "elevated":
                report.elevated_thermal_samples += 1
            for m in row.get("warm_models") or []:
                report.warm_model_counts[m] += 1

    return report


_LEVEL_LINES = {
    "fine": "fine — CPU load looks normal for this workload.",
    "strained": "strained — CPU has been running hot a meaningful share of the "
                "time. Worth watching, not yet urgent.",
    "critical": "critical — macOS itself flagged thermal pressure while local "
                "models were warm.",
    "unknown": "unknown — no samples recorded yet.",
}


def render_resource_report(report: ResourceReport) -> str:
    lines: list[str] = []
    if report.samples == 0:
        lines.append("No resource samples recorded yet.")
        lines.append("")
        lines.append(
            "Set MAHORAGA_RESOURCE_LOG=1 before `orch serve` to start sampling "
            "CPU/thermal load while local models are warm."
        )
        return "\n".join(lines)

    window = ""
    if report.first_ts and report.last_ts:
        window = f"  {report.first_ts[:10]} → {report.last_ts[:10]}"
    lines.append(f"Resource footprint{window}   ({report.samples} samples)")
    lines.append("")
    lines.append(f"  Machine state             {_LEVEL_LINES[report.level]}")
    lines.append("")
    lines.append(
        f"  CPU avg / max             {report.cpu_avg:.1f}% / {report.cpu_max:.1f}%"
    )
    lines.append(
        f"  Samples >= {HIGH_CPU_PERCENT:.0f}% CPU        "
        f"{report.high_cpu_samples} ({(report.high_cpu_share or 0.0):.1%})"
    )
    lines.append(f"  Thermal-elevated samples  {report.elevated_thermal_samples}")

    if report.warm_model_counts:
        lines.append("")
        lines.append("  Warm during sampling:")
        for model, n in report.warm_model_counts.most_common():
            lines.append(f"    {model:<24} {n:>4}")

    lines.append("")
    lines.append(
        "  Log-only: nothing here feeds routing, escalation, or model-unload\n"
        "  decisions yet. This just answers whether Mahoraga is the reason\n"
        "  the machine is straining."
    )
    return "\n".join(lines)
