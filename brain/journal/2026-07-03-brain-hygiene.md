# Session — 2026-07-03 — Brain hygiene: stop runtime writes into the repo brain

## What happened

Resumed the project after ~6 dormant weeks and found the repo brain being
used as a runtime sink:

- `brain/decisions/log.md` had 2,029,674 lines (24 MB) — every routing
  decision appended a content-free "Routed to X" entry, duplicating
  `~/.mahoraga-v2/routing_decisions.db`. All 225,519 headings were auto-noise;
  zero curated content.
- The daemon wrote an empty "Tasks completed: 0" journal stub on every
  shutdown (the call passed no counters, so it could never say anything else).
- Every pytest run appended fake "test-user" sessions to today's journal,
  because `MAHORAGA_BRAIN_PATH` was resolved at import time and tests never
  redirected it. That's where the May "Message from test-user" files came from.

## What changed (commits 74a2e41, d828d6a, 7efa795)

- Removed the per-decision brain append and the shutdown summary call;
  deleted both dead functions. SQLite is the only decision log.
- `git rm` the 2M-line log.md, gitignored the path. No history rewrite
  (pack is 14 MiB; not worth rewriting a pushed branch).
- Per-call brain path resolution + autouse conftest fixture + 2 regression
  tests, so test traffic can never land in the repo brain again.
- Deleted 9 noise journals (June shutdown stubs, May test-user spam).
- Synced CLAUDE.md and current_state.md to the real 2-arm roster
  (qwen3.5 + granite4.1-8b; gemma4-e4b disabled since the 2026-05-20 bench).
- Restarted the launchd daemon onto the fixed code; verified healthy.

## State of play

- 207 real routing decisions since the May reset (111 qwen3.5, 96 granite) —
  both arms are past the 20–50 pull warmup, so **adaptive per-arm gamma is
  unblocked** (next feature, spec in current_state.md §4).
- Pre-existing test failures (not from this session, still open):
  `backend/mcp/test_server.py::test_handle_health_check` (stale mock: health
  now aggregates 3 endpoints) and
  `test_linucb_per_bucket.py::test_get_stats_includes_per_bucket_summary`
  (stats now report all buckets, test expects only touched ones).

ADR: [2026-07-03-remove-brain-auto-append](../decisions/2026-07-03-remove-brain-auto-append.md)
