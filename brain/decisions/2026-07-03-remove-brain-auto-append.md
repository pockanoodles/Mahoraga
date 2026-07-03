# ADR: Remove per-decision auto-append to the repo brain

**Date:** 2026-07-03
**Status:** Accepted

## Context

`brain/decisions/log.md` had grown to 2,029,674 lines (24 MB), 1.94M of them
committed. Every routing decision appended a three-line entry
("Routed to X / strategy=LinUCBPerBucketRouter / mahoraga-router") via
`brain_logger.log_decision()` called from `BanditRouter` — 225,519 entries,
zero curated content. The same decisions are already recorded in
`~/.mahoraga-v2/routing_decisions.db` with full context vectors, UCB scores,
and rewards; the markdown mirror carried strictly less information.

Separately, the FastAPI lifespan hook called `log_session_summary()` on every
daemon shutdown with no counters wired in, so it could only ever write
"Tasks completed: 0 / $0.00" stubs — one junk journal file per day the daemon
restarted.

## Decision

- Delete the `brain_log_decision` call from `BanditRouter.select()` and the
  `log_session_summary` call from the app lifespan. SQLite is the single
  decision log.
- Delete both now-dead functions from `brain_logger.py`. `log_task_completion`
  stays — it writes real per-task content (agent, cost, quality, preview).
- `git rm brain/decisions/log.md` and gitignore the path. No history rewrite:
  the pack is 14 MiB total, not worth rewriting a pushed branch over.
- Delete the noise journals (empty shutdown stubs, `test-user` web-chat spam).

## Consequence

The repo brain is for curated content only: ADRs as individual files in
`brain/decisions/`, journals for sessions with actual work. Per-decision
telemetry lives exclusively in `~/.mahoraga-v2/`. Anything that auto-appends
to the brain must pass the filter: "would this go in a commit message or ADR?"
