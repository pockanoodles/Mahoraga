"""This machine's terms: when a bench may load a model on it.

Every run on the machine reads them, whichever command started it, so the
protection a machine needs can't be left off by a forgotten flag. That is how
the laptop crashed on 2026-10-29: a night script passed --wait-quiet but no
idle or memory gate, and started qwen3.5 at 131k while its owner worked with
~9 GB of apps open. Ollama loaded ~10.5 GB on top anyway (it checks the GPU's
working-set limit, not free memory), wired memory reached 14.2 of 16 GB,
WindowServer starved for two minutes, and the watchdog panicked the kernel.

~/.mahoraga-v2/machine.json, every key optional:

  wait_idle_min   start no attempt until nobody has touched the machine for
                  this many minutes
  load_room_gb    load no model until this much memory is available (free plus
                  what the kernel can reclaim); a loaded model already counts
  yield_to_owner  when someone uses the machine mid-attempt, stop the attempt,
                  unload the model, and wait for the machine to be free again;
                  the stopped attempt isn't counted and its cell is retried.
                  Needs wait_idle_min, or the attempt would start in use
  memory_floor    percent of memory to keep free for the owner while an
                  attempt runs; for a shared machine (see guard)

A flag on the command line overrides its term. No file, no terms: a machine
with memory to spare needs none.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

PATH = Path.home() / ".mahoraga-v2" / "machine.json"


@dataclass(frozen=True)
class Terms:
    wait_idle_min: float = 0
    load_room_gb: float | None = None
    yield_to_owner: bool = False
    memory_floor: int | None = None

    def guard_kw(self) -> dict:
        """The terms as Guard's keyword arguments."""
        return {"wait_idle": self.wait_idle_min * 60,
                "load_room": None if self.load_room_gb is None else int(self.load_room_gb * 2**30),
                "yield_to_owner": self.yield_to_owner, "memory_floor": self.memory_floor}

    def record(self) -> dict:
        """For a run's manifest: the terms its attempts ran under."""
        return asdict(self)


def load(path: Path = PATH) -> Terms:
    """The machine's terms. A file that can't be read is an error, not no
    terms: silently running without them is what this file exists to stop."""
    if not path.exists():
        return Terms()
    try:
        raw = json.loads(path.read_text())
        return Terms(**raw)
    except (ValueError, TypeError) as e:
        raise ValueError(f"{path}: {e}") from e
