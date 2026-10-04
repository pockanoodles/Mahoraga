"""Keep a run's results valid: count only attempts measured on a healthy machine.

The first run on a second repo showed why this exists. On battery and in use,
qwen3.5 generated as slowly as 3 tok/s against ~14.5 on an idle machine on AC;
aider's calls timed out, surfaced as Ollama 500s, and every one of them looked
like a model failure. None of those attempts measured the agent.

  preflight  before a run: charger, agent binary, Ollama, models, speed
  gate       before each attempt: wait until the machine is on AC, generating
             at >= `ratio` x its reference speed, and idle if asked
  watch      during and after an attempt: an attempt is *degraded* if AC was
             lost, or if speed fell below the bar *and* the attempt timed out
             or had requests fail. Degraded attempts are kept for audit but not
             counted, and the cell is retried

Slowness alone doesn't degrade an attempt. The same prompt under the same
generation cap yields the same tokens, only later; a slow machine changes an
outcome through timeouts and failed requests, which is what the first run hit.
On the Studio, an attempt that resolved in 1.6 of its 20 minutes was thrown out
for a slow reading taken after it. So a slow reading with neither is recorded
in the attempt's conditions and the attempt counts.

Speed is measured, not inferred: a fixed short generation, timed by Ollama's
own eval counters, at the agent's num_ctx (a different num_ctx would make
Ollama reload the model). The reference is the median of the last
`READINGS_KEPT` readings for that model digest on this machine *while it was
idle on AC*: in use, even on a charger, qwen3.5 ran at 10-11 tok/s against
~14.5 idle, and a reading taken on a busier machine would set a bar low enough
to wave throttled attempts through. A median rather than the best: gpt-oss:20b
on the Studio read anywhere from 41 to 116 tok/s in one afternoon, and the best
of those set a bar most readings fell under. Until a clean reading exists,
speed is recorded but not judged.
"""
from __future__ import annotations

import json
import shutil
import statistics
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from . import host
from .agents import Agent
from .ollama import BASE_URL, generation_speed, pulled_models, tagged

# Clean readings kept per model; the reference is their median.
READINGS_KEPT = 10


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Watch:
    """What the machine did during one attempt."""
    degraded: list[str] = field(default_factory=list)
    conditions: dict = field(default_factory=dict)
    # Slow (or Ollama erroring) after the attempt. Whether that degrades it
    # depends on how the attempt went, which only the runner knows.
    slow: str = ""


class Guard:
    def __init__(self, root: Path, *, ratio: float = 0.5, min_watts: int = 60,
                 wait_idle: float = 0, clean_idle: float = 300,
                 poll: float = 60, sample_every: float = 30,
                 power: Callable[[], host.Power] = host.power,
                 idle: Callable[[], float | None] = host.idle_secs,
                 speed: Callable[[str, int | None], float] = generation_speed,
                 pulled: Callable[[], dict[str, str]] = pulled_models,
                 sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] = print) -> None:
        self.path = Path(root) / "speed.json"
        self.ratio, self.min_watts, self.wait_idle = ratio, min_watts, wait_idle
        self.clean_idle = clean_idle
        self.poll, self.sample_every = poll, sample_every
        self._power, self._idle, self._speed, self._pulled = power, idle, speed, pulled
        self._sleep, self._log = sleep, log
        self._digests: dict[str, str] | None = None
        self.last_speed: dict[str, float] = {}

    # ── speed ────────────────────────────────────────────────────────────

    def _key(self, model: str) -> str:
        if self._digests is None:
            self._digests = self._pulled()
        return f"{tagged(model)}@{self._digests.get(tagged(model), '?')[:12]}"

    def _refs(self) -> dict[str, list[float]]:
        raw = json.loads(self.path.read_text()) if self.path.exists() else {}
        # A file written before readings were kept holds one number: the best.
        return {k: v if isinstance(v, list) else [v] for k, v in raw.items()}

    def references(self) -> dict[str, float]:
        """Every model's median clean speed, as the bar each attempt is held to."""
        return {k: statistics.median(v) for k, v in self._refs().items() if v}

    def reference(self, model: str) -> float | None:
        readings = self._refs().get(self._key(model))
        return statistics.median(readings) if readings else None

    def _clean(self) -> bool:
        """Would a reading taken now be a fair best? Idle on AC, or unknowable."""
        idle = self._idle()
        return self._power().on_ac is not False and (idle is None or idle >= self.clean_idle)

    def measure(self, agent: Agent) -> tuple[float, float | None]:
        """(speed now, reference or None). The reference is taken before this
        reading joins it, so a reading is never judged against itself. A clean
        reading is kept; the oldest past `READINGS_KEPT` is dropped."""
        s = self._speed(agent.model, getattr(agent, "num_ctx", None))
        refs, key = self._refs(), self._key(agent.model)
        ref = statistics.median(refs[key]) if refs.get(key) else None
        if self._clean():
            refs[key] = [*refs.get(key, []), s][-READINGS_KEPT:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(refs, indent=1, sort_keys=True))
        self.last_speed[agent.model] = s
        return s, ref

    def _slow(self, agent: Agent) -> str | None:
        try:
            s, ref = self.measure(agent)
        except httpx.HTTPError as e:  # an erroring Ollama is an unhealthy one
            return f"ollama error: {e}"
        if ref is not None and s < self.ratio * ref:
            return f"slow: {s:.1f} tok/s, under {self.ratio:.0%} of {ref:.1f}"
        return None

    # ── gate ─────────────────────────────────────────────────────────────

    def _power_problem(self) -> str | None:
        p = self._power()
        if p.on_ac is False:
            return f"on battery ({p.percent}%)"
        if p.watts is not None and p.watts < self.min_watts:
            return f"{p.watts}W charger, under {self.min_watts}W: it cannot keep up with inference"
        return None

    def problems(self, agent: Agent) -> list[str]:
        """Why an attempt shouldn't start now. Speed is probed last and only
        if everything else is fine, since a probe is a real generation."""
        out = [p for p in [self._power_problem()] if p]
        if self.wait_idle:
            idle = self._idle()
            if idle is not None and idle < self.wait_idle:
                out.append(f"in use (idle {idle:.0f}s, waiting for {self.wait_idle:.0f}s)")
        if not out and (slow := self._slow(agent)):
            out.append(slow)
        return out

    def wait_ready(self, agent: Agent, deadline: float | None = None) -> bool:
        """Block until an attempt can start. False if the deadline passes first."""
        shown: list[str] | None = None
        while problems := self.problems(agent):
            if deadline is not None and time.time() >= deadline:
                return False
            if [p.split(" ")[0] for p in problems] != shown:  # log changes, not every poll
                self._log("paused: " + "; ".join(problems))
                shown = [p.split(" ")[0] for p in problems]
            self._sleep(self.poll)
        if shown is not None:
            self._log("resumed")
        return deadline is None or time.time() < deadline

    # ── watch ────────────────────────────────────────────────────────────

    def watch(self, agent: Agent) -> "_Watching":
        return _Watching(self, agent)

    # ── preflight ────────────────────────────────────────────────────────

    def preflight(self, agents: list[Agent]) -> list[Check]:
        checks = []
        p = self._power()
        if p.on_ac is None:
            checks.append(Check("power", True, "unknown on this platform"))
        else:
            problem = self._power_problem()
            checks.append(Check("power", problem is None, problem or
                                f"AC, {p.watts}W, battery {p.percent}%"))
        for name in sorted({a.name for a in agents}):
            found = shutil.which(name)
            checks.append(Check(name, bool(found), found or f"{name} not on PATH"))
        try:
            self._digests = self._pulled()
        except httpx.HTTPError as e:
            checks.append(Check("ollama", False, f"not reachable at {BASE_URL}: {e}"))
            return checks
        checks.append(Check("ollama", True, f"{len(self._digests)} models"))
        for a in agents:
            if tagged(a.model) not in self._digests:
                checks.append(Check(a.model, False, f"not pulled: ollama pull {a.model}"))
                continue
            s, ref = self.measure(a)
            if ref is None:
                checks.append(Check(a.model, True, f"{s:.1f} tok/s; no idle reference yet, "
                                    "so speed isn't judged until an idle reading sets one"))
            else:
                checks.append(Check(a.model, s >= self.ratio * ref,
                                    f"{s:.1f} tok/s (idle median on this machine {ref:.1f})"))
        return checks


class _Watching:
    """Samples power while the agent works; probes speed once it stops.
    Probing during the attempt would compete with the agent for the model."""

    def __init__(self, guard: Guard, agent: Agent) -> None:
        self.g, self.agent, self.result = guard, agent, Watch()
        self._stop = threading.Event()
        self._samples: list[tuple[host.Power, float | None]] = []

    def _sample(self) -> None:
        self._samples.append((self.g._power(), self.g._idle()))

    def _loop(self) -> None:
        while not self._stop.wait(self.g.sample_every):
            self._sample()

    def __enter__(self) -> Watch:
        self._before = self.g.last_speed.get(self.agent.model)
        self._sample()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self.result

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()
        self._sample()
        if exc[0] is not None:
            return
        degraded = self.result.degraded
        if any(p.on_ac is False for p, _ in self._samples):
            degraded.append("lost AC power during the attempt")
        slow = self.g._slow(self.agent)
        self.result.slow = slow + " after the attempt" if slow else ""
        idles = [i for _, i in self._samples if i is not None]
        self.result.conditions = {
            "tok_s_before": self._before,
            "tok_s_after": self.g.last_speed.get(self.agent.model),
            "slow_after": self.result.slow or None,
            "min_battery": min((p.percent for p, _ in self._samples
                                if p.percent is not None), default=None),
            "in_use_share": round(sum(i < 60 for i in idles) / len(idles), 2) if idles else None,
        }
