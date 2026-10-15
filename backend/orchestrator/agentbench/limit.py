"""How long one attempt may run on this machine.

A fixed 20 minutes fit the Studio, and the 16 GB laptop at a 32k context. At
131k the laptop timed out 26 of its 38 attempts (2026-10-14): it reads a long
prompt at about 140 tok/s and writes at 5.8 tok/s once the prompt is deep,
so one call that fills the window and the reply cap takes over half an hour.
The limit is sized from this machine's own reading and writing speed at a
deep prompt, measured once per model and context and kept in limits.json next
to speed.json. A run on a faster machine stays at the 20-minute floor.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import ollama
from .bank import from_record
from .runner import DEFAULT_TIMEOUT

FLOOR = DEFAULT_TIMEOUT
CEILING = 3 * 3600
# A task that needs the big window fills about three quarters of it.
FILL = 0.75
# aider's usual attempt is two calls, plus half of one for the test runs.
CALLS = 2.5
# Deep enough that attention cost shows, short enough to read in minutes.
DEPTH = 65536


@dataclass(frozen=True)
class Reading:
    prefill_tps: float  # tokens read per second, over a prompt `depth` deep
    gen_tps: float  # tokens written per second, at that depth
    depth: int


def limit_secs(r: Reading, num_ctx: int, num_predict: int) -> float:
    """Room for CALLS calls that each fill the window and the reply cap, in
    whole 5-minute steps, between FLOOR and CEILING."""
    call = FILL * num_ctx / r.prefill_tps + num_predict / r.gen_tps
    return float(min(CEILING, max(FLOOR, math.ceil(CALLS * call / 300) * 300)))


def describe(r: Reading, secs: float) -> str:
    return (f"{secs / 60:.0f} min per attempt, sized from reading {r.prefill_tps:.0f} tok/s "
            f"and writing {r.gen_tps:.1f} tok/s at a {r.depth:,}-token prompt")


def path(root: Path) -> Path:
    return Path(root) / "limits.json"


def _key(model: str, digest: str, num_ctx: int) -> str:
    """New weights read at their own speed, so the digest is part of the key."""
    return f"{ollama.tagged(model)}@{digest[:12]}@ctx={num_ctx}"


def _load(p: Path) -> dict:
    """A file that won't parse is measured again, not trusted or kept."""
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def for_agent(root: Path, agent, digest: str = "?", *,
              read: Callable[[str, int, int], tuple[float, float, int]] = ollama.reading_speed,
              ready: Callable[[], bool] = lambda: True,
              log: Callable[[str], None] = print) -> float | None:
    """This machine's limit for the agent's model and context, measuring the
    model once if limits.json hasn't seen it, after `ready` says the machine
    is fit to (None if it never is, e.g. the window closed while waiting).
    Agents that don't drive a local model through a known context keep the
    floor. Delete limits.json to measure again."""
    num_ctx, num_predict = getattr(agent, "num_ctx", None), getattr(agent, "num_predict", None)
    if not num_ctx or not num_predict:
        return FLOOR
    p, key = path(root), _key(agent.model, digest, num_ctx)
    saved = _load(p)
    if key in saved:
        r = from_record(Reading, saved[key])
    else:
        if not ready():
            return None
        depth = min(DEPTH, num_ctx // 2)
        log(f"measuring how fast {agent.model} reads a {depth:,}-token prompt at "
            f"ctx={num_ctx}, once on this machine")
        r = Reading(*read(agent.model, num_ctx, depth))
        saved[key] = asdict(r)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved, indent=1) + "\n")
        os.replace(tmp, p)
    secs = limit_secs(r, num_ctx, num_predict)
    log(f"{agent.model}: {describe(r, secs)}")
    return secs
