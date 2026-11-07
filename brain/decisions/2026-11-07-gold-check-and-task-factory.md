# ADR 2026-11-07 — A task is gated on its real fix, and the OptiPlex mines every night

## Status
Accepted (2026-11-07). PR #93.

**Builds on:** `2026-10-29-long-term-study.md` (the study keeps measuring as new models meet
new work) and `2026-11-05-cloud-arm.md` (the OptiPlex runs the half of the study that needs
no local model).

## Context

The OptiPlex can't run models, so its job is everything around them. Two problems surfaced
while making it a task factory:

- **Pass-to-pass wasn't SWE-bench's definition.** p2p was every test passing at the base.
  Nothing checked them against the real fix, so a test the fix itself fails, skips or removes
  was held against every attempt, and no correct solution could resolve that task. The
  published 47 are safe empirically: the cloud resolved all of them under the grader.
- **Every gate ran once.** A test that passes nine times in ten could land in p2p, and then
  a tenth of attempts fail on a coin toss.

The queue's `--follow` job mines and runs arms together, so on a box with no Ollama it
stopped at "Ollama not reachable" before mining anything. `audit service` was launchd only.

## Decision

1. **Gold check.** The real fix (every change C made outside its tests) is applied to the
   sealed task clone and run the way the grader runs an attempt. Its targets must give the
   results they gave at C, and p2p = pass at the base ∩ pass with the fix. Tests the fix breaks
   are kept as `gold_fails`. The patch is kept as bytes, so CRLF files still apply.
2. **`--gate-runs K`.** Every gate's tests run K times. A test whose result changes is
   flaky: never f2p or p2p, recorded in `flaky`. A target that flips with the fix drops the
   task. `gate_runs` is on each task; 0 means mined before this.
3. **A follow job with no arms only mines.** It fetches, mines, says what it kept and
   dropped, and rests until its window closes, after a failed fetch too.
4. **`orch service` / `audit service` write a systemd user unit on Linux**, with the launchd
   job's contract. ops' health check watches `mahoraga-*` units (ops#9).
5. **Checks that need nothing the box has run in GitHub Actions** (`nightly.yml`, #94): the
   slow tests and the live page. They're free there, and visible in the Actions tab.

## Alternatives rejected

- **A systemd timer around `audit mine`.** It's the hand-armed night script the queue was
  built to retire. One scheduler means `audit queue ls` says what every machine's unattended
  work is doing and why.
- **Running the gold check at C in the unsealed clone.** Cheaper to write, but it isn't the
  tree and git state the grader sees, and it paid for the fix's full suite before gate B
  could drop the candidate.
- **The nightly checks on the OptiPlex.** They need none of its benches, venvs or tokens,
  and a self-hosted runner on a public repo would run strangers' code on Kaito's box.

## Consequences

- Tasks mined from now on are stricter than the published 47. The OptiPlex is screening
  those 47 under the new gates in a separate bench root
  (`~/.mahoraga-v2/screen-2026-11`); `night/2026-11-07-screen-report.txt` says whether any
  published number rests on a flaky or unsolvable test.
- Mining costs one more targeted run and one more full-suite run per surviving candidate at
  K=1, and 10 full-suite runs at the factory's K=5.
- The box mines attrs, more-itertools and click each night, 01:00–06:00, up to 25 new
  candidates per repo, so post-cutoff tasks accumulate without anyone arming a night.
