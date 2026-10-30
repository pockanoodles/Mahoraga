"""Keep a run's results valid: count only attempts measured on a healthy machine.

The first run on a second repo showed why this exists. On battery and in use,
qwen3.5 generated as slowly as 3 tok/s against ~14.5 on an idle machine on AC;
aider's calls timed out, surfaced as Ollama 500s, and every one of them looked
like a model failure. None of those attempts measured the agent.

  preflight  before a run: charger, agent binary, Ollama, models, speed
  gate       before each attempt: wait until the machine is on AC, generating
             at >= `ratio` x its reference speed, idle if asked, and, on an
             Ollama shared with other people, holding none of their models;
             with a memory floor, also until the machine has that much memory
             free plus room for the model to load; with a load room, until
             that much memory is available before the model loads at all
  watch      during and after an attempt: an attempt is *degraded* if AC was
             lost, if free memory fell under the floor or the owner came back
             to a machine that yields to them (the attempt is stopped then and
             there), or if speed fell below the bar *and* the attempt
             timed out or had requests fail. Degraded attempts are kept for
             audit but not counted, and the cell is retried

The load room and yielding exist because the laptop is its owner's machine and
16 GB is barely enough for qwen3.5 at 131k (machine.py has the crash that
proved it). Ollama loads a model whether or not the memory is there, so the
check has to happen before the load: in the gate, and in preflight, whose speed
probe is a load too. Once loaded, the model holds ~10.5 GB wired and the
owner's idle apps sit in swap; their coming back pages all of it in at once,
so a machine that yields gives the memory back the moment it is touched.

The memory floor exists because the Studio is someone else's machine. On
2026-10-17 qwen3.6 at 131k ran there while its owner worked in apps that
aren't Ollama, which the busy check can't see; memory ran out and the run had
to be killed by hand. With a floor the run gives the memory back on its own:
it stops the attempt, unloads its model, and waits. It is off by default, since
a 16 GB laptop sits at the kernel's memory warning through every 131k attempt
and the only one it would stop is the run itself.

Slowness alone doesn't degrade an attempt. A slow machine changes when tokens
arrive, not which ones the model can produce: aider samples at temperature 0,
and where Ollama still isn't bit-for-bit repeatable (the same model solved and
missed the same task across two runs on the Studio), that variation is the
model's, at any speed. So counting a slow attempt adds noise, not bias. A slow
machine reaches an outcome through timeouts and failed requests, which is what
the first run hit.
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
from .agents import DEFAULT_NUM_CTX, Agent, kill_live_agents
from .ollama import (BASE_URL, DERIVED_PREFIX, generation_speed, loaded_models,
                     loaded_sizes, pulled_models, tagged, unload)

# Clean readings kept per model; the reference is their median.
READINGS_KEPT = 10
# Input more recent than this means someone is at the machine.
IN_USE_SECS = 60


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    # A condition of this moment (power, speed) that a queued run waits out,
    # rather than something to fix before queueing it.
    waits: bool = False


@dataclass
class Watch:
    """What the machine did during one attempt."""
    degraded: list[str] = field(default_factory=list)
    conditions: dict = field(default_factory=dict)
    # Slow (or Ollama erroring) after the attempt. Whether that degrades it
    # depends on how the attempt went, which only the runner knows.
    slow: str = ""


def _is_mine(loaded: str, agent: Agent) -> bool:
    """The agent's model, or a tag this bench derived from it."""
    mine = tagged(agent.model)
    return loaded == mine or (loaded.startswith(DERIVED_PREFIX)
                              and loaded[len(DERIVED_PREFIX):].startswith(mine + "-"))


class Guard:
    def __init__(self, root: Path, *, ratio: float = 0.5, min_watts: int = 60,
                 wait_idle: float = 0, clean_idle: float = 300, wait_quiet: bool = False,
                 memory_floor: int | None = None, load_room: int | None = None,
                 yield_to_owner: bool = False,
                 poll: float = 60, sample_every: float = 30,
                 power: Callable[[], host.Power] = host.power,
                 idle: Callable[[], float | None] = host.idle_secs,
                 speed: Callable[[str, int | None], float] = generation_speed,
                 pulled: Callable[[], dict[str, str]] = pulled_models,
                 loaded: Callable[[], list[str]] = loaded_models,
                 free_pct: Callable[[], int | None] = host.memory_free_pct,
                 mem_total: Callable[[], int | None] = host.memory_bytes,
                 pressure: Callable[[], int | None] = host.memory_pressure,
                 sizes: Callable[[], dict[str, int]] = loaded_sizes,
                 release: Callable[[str], None] = unload,
                 stop_agent: Callable[[], None] = kill_live_agents,
                 sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] = print) -> None:
        self.path = Path(root) / "speed.json"
        self.ratio, self.min_watts, self.wait_idle = ratio, min_watts, wait_idle
        self.clean_idle, self.wait_quiet = clean_idle, wait_quiet
        # Percent of memory to leave free for the machine's owner; None = off.
        self.memory_floor = memory_floor
        # Bytes that must be available before our model loads; None = off.
        self.load_room = load_room
        if yield_to_owner and wait_idle < IN_USE_SECS:
            raise ValueError("yielding to the owner needs an idle wait of at least a "
                             "minute, or every attempt would start in use and stop")
        self.yield_to_owner = yield_to_owner
        self._loaded = loaded
        self._free_pct, self._mem_total, self._sizes = free_pct, mem_total, sizes
        self._pressure = pressure
        self._release, self._stop_agent = release, stop_agent
        # Bytes each of our models took when last seen loaded, so the gate
        # can leave room for one that isn't loaded yet.
        self._model_bytes: dict[str, int] = {}
        self.poll, self.sample_every = poll, sample_every
        self._power, self._idle, self._speed, self._pulled = power, idle, speed, pulled
        self._sleep, self._log = sleep, log
        self._digests: dict[str, str] | None = None
        self.last_speed: dict[str, float] = {}

    # ── speed ────────────────────────────────────────────────────────────

    def _key(self, model: str, num_ctx: int | None = None) -> str:
        """Model digest, plus the context when it isn't the default: Ollama
        reloads a model at a new size, so its speed there is its own series."""
        if self._digests is None:
            self._digests = self._pulled()
        key = f"{tagged(model)}@{self._digests.get(tagged(model), '?')[:12]}"
        return key + (f"@ctx={num_ctx}" if num_ctx and num_ctx != DEFAULT_NUM_CTX else "")

    def _refs(self) -> dict[str, list[float]]:
        raw = json.loads(self.path.read_text()) if self.path.exists() else {}
        # A file written before readings were kept holds one number: the best.
        return {k: v if isinstance(v, list) else [v] for k, v in raw.items()}

    def references(self) -> dict[str, float]:
        """Every model's median clean speed, as the bar each attempt is held to."""
        return {k: statistics.median(v) for k, v in self._refs().items() if v}

    def reference(self, model: str, num_ctx: int | None = None) -> float | None:
        readings = self._refs().get(self._key(model, num_ctx))
        return statistics.median(readings) if readings else None

    def _clean(self) -> bool:
        """Would a reading taken now be a fair best? Idle on AC, or unknowable."""
        idle = self._idle()
        return self._power().on_ac is not False and (idle is None or idle >= self.clean_idle)

    def measure(self, agent: Agent) -> tuple[float, float | None]:
        """(speed now, reference or None). The reference is taken before this
        reading joins it, so a reading is never judged against itself. A clean
        reading is kept; the oldest past `READINGS_KEPT` is dropped."""
        num_ctx = getattr(agent, "num_ctx", None)
        s = self._speed(agent.model, num_ctx)
        refs, key = self._refs(), self._key(agent.model, num_ctx)
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

    def _busy(self, agent: Agent) -> str | None:
        """Someone else's model is loaded. The Studio's Ollama is shared: a run
        that starts while a colleague's model is in memory slows them down and
        is measured on a contended machine. Their model unloads on its own once
        they stop (Ollama's keep-alive), so waiting for it is waiting for them."""
        try:
            loaded = self._loaded()
        except httpx.HTTPError as e:
            return f"ollama error: {e}"
        others = [m for m in loaded if not _is_mine(m, agent)]
        return f"busy: {', '.join(others)} loaded" if others else None

    def _mine_loaded(self, agent: Agent) -> dict[str, int]:
        """Our model's loaded copies, name -> bytes; remembers the size."""
        try:
            mine = {m: b for m, b in self._sizes().items() if _is_mine(m, agent)}
        except httpx.HTTPError:
            return {}
        if mine:
            self._model_bytes[tagged(agent.model)] = max(mine.values())
        return mine

    def _memory_problem(self, agent: Agent) -> str | None:
        """Under the floor, or would be once our model loads. An unseen model
        adds nothing, and the watch stops its first attempt if it doesn't fit."""
        if self.memory_floor is None or (free := self._free_pct()) is None:
            return None
        need, why = self.memory_floor, f"the {self.memory_floor}% floor"
        size, total = self._model_bytes.get(tagged(agent.model)), self._mem_total()
        if not self._mine_loaded(agent) and size and total:
            room = round(100 * size / total)
            need, why = need + room, f"the {self.memory_floor}% floor plus {room}% to load {agent.model}"
        return f"memory: {free}% free, under {why}" if free < need else None

    def _room_problem(self, agent: Agent) -> str | None:
        """Too little memory available to load our model. Loaded, it is
        already inside the reading, so there's nothing left to check."""
        if self.load_room is None or self._mine_loaded(agent):
            return None
        free, total = self._free_pct(), self._mem_total()
        if free is None or not total:
            return None
        available = free * total // 100
        if available >= self.load_room:
            return None
        return (f"memory: {available / 2**30:.1f} GB available, under the "
                f"{self.load_room / 2**30:.0f} GB to load {agent.model}")

    def _idle_problem(self) -> str | None:
        if not self.wait_idle:
            return None
        idle = self._idle()
        if idle is not None and idle < self.wait_idle:
            return f"in use (idle {idle:.0f}s, waiting for {self.wait_idle:.0f}s)"
        return None

    def problems(self, agent: Agent) -> list[str]:
        """Why an attempt shouldn't start now. Speed is probed last and only
        if everything else is fine, since a probe is a real generation."""
        out = [p for p in [self._power_problem(), self._idle_problem()] if p]
        if self.wait_quiet and (busy := self._busy(agent)):
            out.append(busy)
        if memory := self._memory_problem(agent):
            out.append(memory)
        if room := self._room_problem(agent):
            out.append(room)
        if not out and (slow := self._slow(agent)):
            out.append(slow)
        return out

    def release(self, agent: Agent) -> None:
        """Unload our model now, so waiting on someone doesn't hold their memory."""
        for m in self._mine_loaded(agent):
            try:
                self._release(m)
            except httpx.HTTPError:
                pass  # its keep-alive unloads it anyway

    def wait_ready(self, agent: Agent, deadline: float | None = None) -> bool:
        """Block until an attempt can start. False if the deadline passes first."""
        shown: list[str] | None = None
        while problems := self.problems(agent):
            if deadline is not None and time.time() >= deadline:
                return False
            if [p.split(" ")[0] for p in problems] != shown:  # log changes, not every poll
                self._log("paused: " + "; ".join(problems))
                shown = [p.split(" ")[0] for p in problems]
                # Waiting on someone: don't hold memory they're using.
                if any(p.startswith(("busy:", "memory:", "in use")) for p in problems):
                    self.release(agent)
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
                                f"AC, {p.watts}W, battery {p.percent}%", waits=True))
        if self.memory_floor is not None and (free := self._free_pct()) is not None:
            checks.append(Check("memory", free >= self.memory_floor,
                                f"{free}% free, floor {self.memory_floor}%", waits=True))
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
            # The probe loads the model, so it waits for what an attempt waits for.
            if why := self._idle_problem() or self._room_problem(a):
                checks.append(Check(a.model, False, f"speed not probed yet: {why}",
                                    waits=True))
                continue
            s, ref = self.measure(a)
            if ref is None:
                checks.append(Check(a.model, True, f"{s:.1f} tok/s; no idle reference yet, "
                                    "so speed isn't judged until an idle reading sets one"))
            else:
                checks.append(Check(a.model, s >= self.ratio * ref,
                                    f"{s:.1f} tok/s (idle median on this machine {ref:.1f})",
                                    waits=True))
        return checks


class _Watching:
    """Samples power and memory while the agent works; probes speed once it
    stops. Probing during the attempt would compete with the agent for the model."""

    def __init__(self, guard: Guard, agent: Agent) -> None:
        self.g, self.agent, self.result = guard, agent, Watch()
        self._stop = threading.Event()
        self._samples: list[tuple[host.Power, float | None]] = []
        self._free: list[int] = []
        self._pressures: list[int] = []

    def _give_back(self, why: str) -> None:
        """Stop the attempt and unload its model. Once the agent has stopped,
        there is nothing left to stop."""
        if self.result.degraded or self._stop.is_set():
            return
        self.result.degraded.append(f"{why}; stopped the attempt and unloaded the "
                                    "model to give it back")
        self.g._stop_agent()
        self.g.release(self.agent)

    def _sample(self) -> None:
        idle = self.g._idle()
        self._samples.append((self.g._power(), idle))
        if (p := self.g._pressure()) is not None:
            self._pressures.append(p)
        if self.g.yield_to_owner and idle is not None and idle < IN_USE_SECS:
            self._give_back(f"the machine was used {idle:.0f}s ago")
        floor = self.g.memory_floor
        if floor is None or (free := self.g._free_pct()) is None:
            return
        self._free.append(free)
        self.g._mine_loaded(self.agent)  # learn the model's size while it's loaded
        if free < floor:
            self._give_back(f"memory fell to {free}% free, under the {floor}% floor")

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
        gave_back = bool(degraded)  # only _give_back fills it before here
        if any(p.on_ac is False for p, _ in self._samples):
            degraded.append("lost AC power during the attempt")
        # A probe would reload the model just given back.
        slow = None if gave_back else self.g._slow(self.agent)
        self.result.slow = slow + " after the attempt" if slow else ""
        idles = [i for _, i in self._samples if i is not None]
        self.result.conditions = {
            "tok_s_before": self._before,
            "tok_s_after": self.g.last_speed.get(self.agent.model),
            "slow_after": self.result.slow or None,
            "min_battery": min((p.percent for p, _ in self._samples
                                if p.percent is not None), default=None),
            "in_use_share": round(sum(i < IN_USE_SECS for i in idles) / len(idles), 2)
                            if idles else None,
        }
        if self._free:
            self.result.conditions["min_free_pct"] = min(self._free)
        # Whether an attempt on a machine at its limit reaches critical (4):
        # the evidence for stopping on pressure, not only on the owner.
        if self._pressures:
            self.result.conditions["max_pressure"] = max(self._pressures)
