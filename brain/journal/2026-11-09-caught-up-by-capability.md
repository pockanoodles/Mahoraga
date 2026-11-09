# 2026-11-09: caught up is the gap on the same tasks

Late on 2026-11-08, #103 (every cloud tier) merged and the site deployed. The first deploy
shipped an old build, because the worktree had no `site/node_modules` and a pipe into `tail`
hid the failed `next build`. A reinstall, rebuild and redeploy fixed it within minutes, and
the live verify passed. Check the build's exit code before `wrangler deploy`.

Kaito then asked for a planning session on a capability test, since Haiku 5.5 at a cent a task
had pushed the price-based line to 100%.

## What changed in my own recommendation

Last session I had suggested "not significantly below the tier" as the capability line. It is
wrong, since a small row can't show any gap and so would cross easily. The replacement is a
non-inferiority test: the cloud's lead on the same tasks, with a paired 95% interval, has to sit
wholly inside a margin fixed in advance. Kaito took all six recommendations: a 10-point margin,
the same agent on both sides, a veto from the after-cutoff tasks, a confirmation on fresh tasks,
the price as its own column, and the same four standings for every kind of work.

## What the numbers say

- Every tier reads behind: 64, 55 and 64 points against Opus, Sonnet and Haiku.
- Sonnet against Opus reads open (8.5 points, 0.3 to 19.9). At 47 tasks local reads "caught
  up" only if it loses no task the cloud solves.
- Simulated: a truly level local model that disagrees with the cloud on 10% of tasks reads
  caught up 43% of the time at 47 tasks, 83% at 100, 94% at 150 and 98% at 200.

## Built (PR `feat/capability-test`)

`paired_difference` (Tango, bisection on the score statistic, checked against a brute-force
constrained fit and two closed forms), `audit/capability.py`, a `"capability": "versus"` block on
claims (verify 33/33, 272 metrics), the ADR, the map's caught-up column per tier plus a price
column, the README, report §4 "Caught up, by capability", and a log entry in en and ja.

## Next

#100, the laptop's Claude Code run on the 47, then more tasks toward 150 and the STT row.
