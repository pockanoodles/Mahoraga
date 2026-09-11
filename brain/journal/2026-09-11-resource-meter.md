# 2026-09-11 — the third meter lands, and one sentinel becomes a function

Week 1 of the longitudinal plan
(`brain/decisions/2026-09-10-longitudinal-thesis.md`): start the clocks, close
the cheap threads. This is one of the cheap threads — `orch metrics resource`
had been sitting feature-complete and uncommitted in the working tree since
2026-09-10, in no journal entry, with three named gaps. Closed all three.

## What the meter is

The third dogfooding meter, alongside `orch metrics usage` (what the cascade
did) and `orch metrics funnel` (how much delegable work reached it). This one
answers a different question: **is Mahoraga the reason the machine is
straining?**

`routing/resource_sampler.py` samples on an interval for as long as
`orch serve` is up — psutil CPU percent, macOS thermal pressure via
`pmset -g therm`, and the models Ollama currently has warm via `/api/ps` — and
appends to `~/.mahoraga-v2/resource.jsonl`. `routing/resource_report.py`
reduces that to one word: `fine` / `strained` / `critical`. Gated behind
`MAHORAGA_RESOURCE_LOG`, off by default, log-only: nothing here feeds routing,
escalation, or model-unload decisions.

**It deliberately does not measure memory.** High RAM with two local models
warm is the expected shape of this workload, not a problem signal — reporting
it as pressure would manufacture an alarm out of normal operation. The cost
that is actually felt on this machine is sustained CPU load: fans, lag, thermal
throttling. That is what gets sampled.

**The log is its own file, not the routing DB** — same separation
`funnel_report.py` already makes. Hardware samples are a distinct population
from routing decisions; mixing them means every later query has to remember to
exclude one.

## The three gaps, closed

**`sampler_loop` was untested.** Now pinned on the property that matters and is
easy to get wrong: it sleeps the *full* interval before each sample, and takes
no sample at t=0. That ordering isn't cosmetic — `psutil.cpu_percent(interval=None)`
has no prior call to difference against on its first invocation, so a sample at
t=0 would log a meaningless number as if it were a reading. The loop primes the
counter and discards that first value by construction. Also pinned:
cancellation returns cleanly, because `orch serve`'s shutdown cancels this task
and a dangling `CancelledError` would surface as a shutdown error.

**`_thermal_level` parsing was untested — and it's an inverted parse.** macOS
reports health as a *sentence* ("No thermal warning level has been recorded")
and pressure as *values* (`CPU_Speed_Limit = 80`). So the function keys on the
absence of the idle sentence, which means every unexpected output reads as
`elevated`. Three tests now: the idle line → `nominal`, recorded limits →
`elevated`, and — the one that matters — a **non-zero exit → `unknown`, not
`elevated`**. Without that branch, every non-macOS host would report the
machine as `critical` forever, since `level` lets a single thermal sample
outrank any CPU percentage.

**The `"￿"` sentinel is gone, and it was duplicated.** `compute_resource_report`
ended `--until` filtering with `ts > until + "￿"` — appending U+FFFF so that
`--until 2026-09-10` would still admit a `2026-09-10T14:22:01` row, which sorts
after the bare date. It works, and it reads like a typo. Grepping found the
*same* line already in `funnel_report.py:114`, so this was a convention with
two copies and zero documentation.

Replaced with `routing/time_window.py` — one `within_window(ts, since, until)`
that compares only the prefix the bound actually specifies, so a bound given at
any precision (date or full timestamp) is inclusive at that precision. Both
readers now call it. Six tests on the window itself, including the
whole-day-inclusive case the sentinel existed to fake.

**The general shape:** the fix for a clever-looking line in one place was
finding out it wasn't in one place. Two identical brittle comparisons meant the
window semantics were an undocumented shared assumption between two reports —
worth a named function, not a patched character.

## State

- Branch `feat/resource-meter`, PR opened (`main` is push-protected).
- New: `routing/time_window.py`, 6 tests; 5 tests added to
  `test_resource_report.py` (now 24 in that file).
- `funnel_report.py` and `resource_report.py` both windowed through the shared
  helper; funnel's 24 existing tests unchanged and green.
- Full suite green.
- Still log-only. Promoting any of this to a routing input is a separate
  decision and not in the current window.

## Open

The meter has never run. `MAHORAGA_RESOURCE_LOG=1` has to be set before
`orch serve`, and no daemon is currently up — so like the funnel a month ago,
this is an instrument with no readings yet. **Set it when the weeks 2–3
longitudinal run starts:** a ~4-hour full-bank run is exactly the workload
worth a thermal record, and "what this costs the machine it runs on" is a real
column in the break-even framework due in week 4.
