# ADR 2026-11-08 — The OptiPlex hosts the bench, and the model answers from wherever it runs well

## Status
Parts 1 and 2 accepted (2026-11-08, PR #101). Part 3 declined by Kaito the same day: the box
does not drive the Studio. The split-host record stays, for any machine that serves a model
to another (a reader's home server, or the laptop for a smoke). Cloud tiers below Opus stay
out of the monthly pass (also declined 11-08).

**Builds on:** `2026-11-07-gold-check-and-task-factory.md` (the box mines every night) and
`2026-10-29-long-term-study.md` (the study keeps measuring as new models meet new work).

## Context

The factory works. Night 1 (11-07 → 11-08) kept 46 new tasks: attrs +10 (29 in its bench),
more-itertools +18 (46), and click +18 (38, its first mine). But no arm runs them. The published
47 are still the only tasks any model has met, and the factory only builds up stock.

The box can't generate (i7-9700, 16 GB single channel: ~2–3 tok/s for a 9.7B Q4). The Studio
can, but running a bench there means Mahoraga's worker, task clones, test venvs and agent
processes all live on Adly's machine. Its worker has been dead since a reboot with four 35B
jobs parked, and its night runs depend on a terminal staying up (Ghostty OOM'd on 11-07).

On Linux the agent's sandbox already reaches Ollama through a relay to whatever
`OLLAMA_BASE_URL` names (`agents.ollama_net`), so the agent can run on the box while the
model answers elsewhere. Nothing recorded that, though: the manifest named only the
harness's host, so a split run would have been credited to the OptiPlex's CPU, pooled with
local rows, and printed under the same receipt.

## Decision

1. **A run records where its model answered (#101).** `manifest.inference` = Ollama's URL,
   the machine behind it (`MAHORAGA_INFERENCE_MACHINE`, needed because an SSH tunnel looks
   like loopback), and `here`. The machine label and the ledger receipt name both machines
   ("OptiPlex 7070, 16 GB, model served by Mac Studio M5 Max, 64 GB"), so a verdict never
   pools a split setup with either machine alone. Smoke 11-08: aider + qwen3.5:4b on attrs,
   agent and tests on the box, model on the laptop through `ssh -R`, isolation checks all
   passing, 31 tok/s through the relay.
2. **The factory widens to six repos** (11-08): pypa/packaging (version and marker
   parsing), pallets/werkzeug (HTTP, a larger codebase) and python-poetry/tomlkit (a
   round-trip parser) join attrs, more-itertools and click. Criteria: pure-Python, pytest,
   a steady stream of human commits after June 2026 (the newest models' cutoff), and a
   different kind of code from what's already there. Two small utility libraries were the
   study's whole task pool, and a reviewer would say so first.
3. **Declined: the box drives the Studio's Ollama every night.** A follow job with arms
   (`aider:qwen3.5` and `claude:qwen3.5`, the two harnesses the published rows use) runs on
   the box. The Studio only answers model calls, through a tunnel the box opens (`ssh -L`),
   so Ollama never listens on the network. The Studio then needs no Mahoraga worker, clones
   or venvs, and nothing of the bench depends on a terminal there. Every new task meets the
   local model within days of being mined. That is the study's longitudinal series: the same
   model on work that keeps postdating it.

## Consequences

- Split rows are their own setup. They are comparable to Studio-alone rows on resolve rate
  (the model and its settings are the same), but not on time, which now includes the box's
  test runs and the LAN. A first split pass on the published 47 would show whether the rate
  holds; if it does, later split rows can stand beside the Studio's.
- The guard's memory gates measure the box, not the Studio. On the Studio only the night
  window and `--wait-quiet` (Ollama's own load) protect its owner. That fits Kaito's power
  rule (small models, night only), but it's weaker than `--memory-floor 25`.
- The monthly cloud pass (`night/monthly-cloud.sh`) now covers every factory repo and runs
  a pinned Claude Code 2.1.318. The box's own `claude` auto-updated to 2.1.319 on 11-06, the
  day after the cloud run.

## Not chosen

- **Models on the box's CPU.** At 2–3 tok/s, one Claude Code attempt (39–127 model calls)
  takes hours. A 1–3B model is fast enough but measures only a floor nobody would ship.
- **RAM for the box: parked (Kaito, 2026-11-08).** Generation speed is set by memory
  bandwidth, and more capacity doesn't add any. The spare machine at home is DDR3, so
  its sticks don't fit the 7070 (DDR4), and as a host it would generate slower still. It
  stays unused for Mahoraga. Inference belongs on the Studio or the laptop, and the box hosts
  the bench.
- **Exposing the Studio's Ollama on the tailnet.** Ollama has no auth; a tunnel over the SSH
  that already exists keeps it on loopback.
