# 2026-07-26 — Verifiable bank 18→50 + CI integrity guard

## What happened

PR #19 (cost accounting) merged with CI green. Then closed open thread (b)
from 2026-07-15: the verifiable bank was too small and too easy (18 rows,
only 5/70 failures in the Era 9 run) for a pass@1 anyone would quote.

**Bank expanded 18 → 50 rows** (`feat/verifiable-bank-50`):
- +18 code (6 medium / 12 hard), +14 debug (4 medium / 10 hard).
  Final mix: code 4E/12M/14H, debug 1E/8M/11H.
- Design rule: precise multi-constraint specs (tie-breaks, exact output
  formats, exception behavior — e.g. lexicographically-smallest topo order,
  RFC-6901 `~0`/`~1` unescapes, semver prerelease ordering, CSV `""` escapes)
  instead of famous LeetCode problems 9B models have memorized. Debug rows
  cover distinct bug *classes*: late-binding closures, `[[0]*c]*r` aliasing,
  float-accumulation `==`, truthiness-drops-0, generator exhaustion,
  `x == 'a' or 'A'` precedence, zip truncation, `lst.sort()` returns None…
- **Authorship deliberately not routed through the Mahoraga arms** — the
  models under test must not write their own benchmark. Three parallel
  subagents authored prompts + refs; every row execution-validated.

**New CI guard** (`tests/orchestrator_v2/test_verifiable_bank.py`, 106 tests,
~2.5s): each bank row must have a committed reference that passes its hidden
tests and a near-miss mutant that fails them (test sensitivity — a vacuous
test string would silently inflate every arm's pass@1). Runs through the same
`run_case()`/`extract_code()` path as `orch bench report verify`. Refs live in
`experiments/prompts_verifiable_refs.jsonl` (force-added past the gitignore,
like the bank). This replaces the old scratchpad validator, which was
local-only and turned out to be *lost* — the exact failure mode the code
standards' "scratchpad is disposable" rule implies; integrity checks belong
in tests/.

Full suite: 1426 passed.

## Roadblock found + fixed: the roster models were gone

`ollama list` showed only `qwen3:14b` — `qwen3.5:latest` and `granite4.1:8b`
(arms 1 and 2) had been removed from Ollama at some point since 07-15, along
with `nomic-embed-text` (unused; semantic memory runs all-MiniLM-L6-v2 via
sentence-transformers) and `gemma4:e4b` (disabled arm, fine). Disk has 180 GB
free, so both arm models were re-pulled (~12 GB); roster verified whole again.
Phase 4 would have crashed 2 of 4 arms without this.

## Interesting catch during authoring

Python 3.12's `sum()` uses Neumaier compensated summation, so
`sum([0.1]*10) == 1.0` exactly — the float-accumulation debug row originally
didn't fail. Rewritten around a manual `+=` loop (truer to the bug class).

## Next

Phase 4 head-to-head (runbook in current_state.md), now on the 50-row bank:
one force-explore run over the 3 local arms + `claude-cli` produces the
measured cost delta and pass@1 retention in a single session, all on Max
quota. Note force-explore is safe here *because* it's a bench, not seeding.
