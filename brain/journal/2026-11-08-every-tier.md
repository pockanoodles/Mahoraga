# 2026-11-08: every cloud tier solves the tasks, and Haiku moves the line to 100%

The map reframe merged earlier in the evening (#95, #102) with the claim that local might
catch a cheaper cloud tier long before the best one. Sonnet 5.5 and Haiku 5.5 were started on
the OptiPlex at 19:05 to test that, and both finished by 20:33. This session exported them,
wrote their claims and put them on the map.

## What the run measured

Claude Code 2.1.318 (pinned copy in `night/claude-2.1.318`), blind, 90 minutes an attempt,
an empty home each attempt, the allowlisting proxy, on the 47 published tasks (attrs 19,
more-itertools 28). Harness 1aec6507c (main after #101), the Northeastern seat.

- **Haiku 5.5: 47 of 47 at $0.01 a task**, median 36 s.
- **Sonnet 5.5: 43 of 47 at $0.07 a task**, median 19 s. Its 4 misses ended within 30 s
  with partial target tests, so Sonnet quit early. Against Opus that is 4-0, p = 0.125.
- **Against the laptop:** Sonnet 27-1, Haiku 30-0. The one is attrs cbaef3f, the only task
  of the 47 that local solved and a cloud tier missed.
- **Break-even at what each tier spent: 95% Opus, 98% Sonnet, 100% Haiku.** The net per
  month barely moves (−$77 to −$80), since local's failures cost developer time at every tier.

## Two things worth knowing

- **The premise of fact 4 in the map ADR didn't hold.** The cheapest tier is as capable on
  these tasks as the best, and at a cent a task the price-based line asks local to never
  fail. That makes the line a question for Kaito: keep it, or add a capability test beside
  it (local's rate against a tier's on the same tasks, paired). Recorded in the ADR, not
  changed.
- **Haiku tried `pip install` inside the sandbox.** 217 refused connections to pypi.org and 1
  to api.github.com, all logged by the proxy. It solved the tasks anyway. The proxy held, and
  this is the first time a cloud tier asked for a host beyond the model.

## Housekeeping

- An orphaned werkzeug pytest from the hung first mine (killed ~19:55) was still running at
  20:49 with ppid 1. It was killed. The re-mine (with `-m "not dev_server"`) kept 12 of 25
  and queued follow job e4840b.
- PR #100 (the local Claude Code arm's output cap) is still open. The same-agent run's last
  11 tasks wait on it.
