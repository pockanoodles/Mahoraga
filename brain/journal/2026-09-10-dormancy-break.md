# 2026-09-10 — the meter's first reading, and what it actually measured

Four weeks dormant. Last commit 2026-08-12, last organic routing decision
2026-08-12, nothing since. This session was a state audit that turned into a
direction change.

## What was sitting there

**PR #39 had been green, mergeable, and unmerged for 29 days.** Thirteen commits:
the escalation cascade on the live serving path, `orch bench verify`, the
API-key repro arm, `orch metrics usage`, `orch metrics funnel`. All of it
finished, none of it on `main`. Merged this session as `7583594`. Suite **1719
green** — 1706 from the PR plus 13 from uncommitted work in the tree.

That uncommitted work is a **third dogfooding meter**: `orch metrics resource`
(CPU/thermal, log-only), feature-complete and wired end to end, in no journal
entry. Still uncommitted as of this writing.

## The reading

`orch metrics funnel`, first real output, 1613 rows over 44 sessions:

```
Delegable work observed   170
  delegated to Mahoraga   0
  handled inline          170
Delegation rate           0.0%
```

The meter built on 2026-08-12 to make "improve delegation" falsifiable
falsified it immediately. But the first reading of the reading was wrong.

## What I got wrong, twice, before checking

**1. I blamed the exclusions.** The breakdown shows 1302 of 1617 actions
excluded as `edit-in-place`, and the obvious story writes itself: the arms get
no repo, so surgical edits can't be delegated, so the rate is floored.

That is backwards. **Exclusions shrink the denominator — removing them would
push the rate up, not down.** The denominator is a healthy 171. The rate is
zero because the numerator is zero: not one `run_task` row exists in the log.

**2. Then the timestamps.**

```
MCP mahoraga last used:  2026-08-11 21:33
Funnel hook started:     2026-08-12 05:52
```

The hook began recording **eight hours after the last time the integration
worked.** Across 44 sessions it has never once observed a functioning tool.

Root cause found this session: `~/.claude.json` registered the server as
`command: "python"`, and no bare `python` exists on this machine — only
`/opt/homebrew/bin/python3`, a 3.14 build without `psutil`. The server failed
with `ENOENT: Executable not found in $PATH: python`. Two entries were wrong
(project scopes `Projects/Mahoraga` *and* `Projects/ops`, both with
`cwd=Mahoraga`); both now pin `/Users/kaitosoeno/Projects/Mahoraga/.venv/bin/python`.
Same root cause makes `python3 -m pytest` fail at collection — tests need
`.venv/bin/python`.

**So the honest reading is: this is mostly a broken-integration artifact, not a
measurement of behaviour.** The `edit-in-place` ceiling is real and separate —
it caps delegable work at ~10% of coding actions — but it is not why the number
is zero. The meter's first job turned out to be detecting that the thing it
measures was unplugged.

Lesson worth keeping: **an instrument pointed at an integration needs a
liveness check on that integration.** A 0% rate and a broken tool are
indistinguishable in this log, and the log is the only evidence.

## What the delegable slice actually is

Profiled from the log directly:

```
1302  Edit    → edit-in-place      (80.5%)
 171  Write   → CANDIDATE          (10.6%)
 135  Write   → non-code-file
   6  Write   → oversized-for-local-arm
   3  Write   → below-round-trip-threshold
```

`edit-in-place` is literally `tool == "Edit"`, checked first, before extension
or size (`scripts/claude_code_funnel_hook.py:87`). Candidates are 100% `Write`,
median 86 lines (p25 49, p75 134), 106 `.py` / 33 `.ts` / 17 `.tsx`.

The distribution across repos is the part that matters for scope:

```
  31/592   5.2%  course-tutor
  44/384  11.5%  erwin
  34/246  13.8%  foresight-industries
  39/191  20.4%  gene
   7/ 37  18.9%  Mahoraga
  10/ 13  76.9%  investments
```

Nine repos, 44 sessions, and **Mahoraga is 37 of 1617 rows.** A serving path
that only knows about itself is addressing 2% of the work. Cross-repo is not a
nice-to-have.

## Two more corrections, from reading the tree instead of the ledger

**A1 semantic routing is already built.** The brain has named it "the next
lever, never started" in Era 23, Era 24, 08-05 and 08-11. The tree disagrees:
`routing/embeddings.py` exists, `MEMORY_MODE_SEMANTIC` is the *default*
(`bandit_router.py:62-66`), the dual HNSW index, `orch memory
backfill|inspect|clear`, and the per-bucket bandits the spec deferred are all
shipped. It was also already evaluated, and mostly nulled: best configuration
is **+0.35 reward at ~0.25σ**, "strictly directional." The one clean win
(paraphrase transfer, 21.25% vs 11.25%) needs per-bucket LinUCB *and* semantic
together, on synthetic oracle-labelled benchmarks. The genuine remaining gap is
narrow — never validated against the real 2-arm roster on real traffic.

**The README leads with the claim RESULTS.md refutes.** README opening: *"a
local-first LLM orchestrator that learns which agent to use for each task."*
`docs/RESULTS.md:113`: *"That the contextual bandit improves routing. It does
not, measurably... The bandit is architecture in this repo, not a result."*
Both true, both published, ninety seconds apart for any careful reader. The
project's differentiator is rigour; this is the one place the repo undercuts it.

## State

`main` at `7583594`, local fast-forwarded, suite 1719 green. MCP repointed at
the venv interpreter (backup `~/.claude.json.bak-20260910-200532`); needs a
session restart to reconnect. Uncommitted in the tree: the resource meter (3 new
files, 3 modified). No daemon running. `qwen3:14b` still on disk at 9.3 GB
though dropped as an arm on 2026-07-26. PR #38 ("Update README.md") open since
08-06, probably a stray web edit.

## Open

- **The dogfooding clock restarts today.** Every future delegation number
  measures from 2026-09-10, not 2026-08-12. The earlier window is void.
- Delegation *quality* still unmeasured — rate counts whether work was
  delegated, not whether the output was kept. Unchanged from 08-12.
- The K=5 sweep cache (`judge_gate_cache_k5.json`, 94 KB, Aug 5) has never been
  read out. The inference is paid for; only the analysis is missing.
- `findings.md` stops at Era 24; three sessions (08-11, 08-12, and this one) are
  not back-filled.
- Direction set this session: see
  `brain/decisions/2026-09-10-longitudinal-thesis.md`.
