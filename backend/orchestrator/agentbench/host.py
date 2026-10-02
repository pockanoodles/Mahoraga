"""What the machine is doing: its power source, and whether someone is using it.

macOS only (pmset, ioreg). Elsewhere every reading is None, and the guard
treats unknown as fine rather than refusing to run.
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Power:
    on_ac: bool | None
    percent: int | None
    watts: int | None  # adapter rating; None on battery or when unknown


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def parse_power(batt: str, ac: str) -> Power:
    """`pmset -g batt` gives the source and charge, `pmset -g ac` the adapter."""
    source = re.search(r"drawing from '([^']+)'", batt)
    on_ac = source.group(1) == "AC Power" if source else None
    pct = re.search(r"(\d+)%", batt)
    watts = re.search(r"Wattage = (\d+)W", ac)
    return Power(on_ac, int(pct.group(1)) if pct else None,
                 int(watts.group(1)) if watts and on_ac else None)


def power() -> Power:
    if sys.platform != "darwin":
        return Power(None, None, None)
    return parse_power(_run("pmset", "-g", "batt"), _run("pmset", "-g", "ac"))


def parse_idle(ioreg: str) -> float | None:
    m = re.search(r'"HIDIdleTime" = (\d+)', ioreg)
    return int(m.group(1)) / 1e9 if m else None


def idle_secs() -> float | None:
    """Seconds since the last keyboard, mouse, or trackpad input."""
    if sys.platform != "darwin":
        return None
    return parse_idle(_run("ioreg", "-c", "IOHIDSystem"))
