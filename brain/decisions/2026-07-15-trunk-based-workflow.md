# ADR: Trunk-based workflow with CI-gated PRs

**Date:** 2026-07-15
**Status:** Accepted

## Context

Work is increasingly delegated to AI coding agents (Claude Code, and now Cursor
cloud/background agents triggered from Slack). The repo had no CI and a confusing
branch layout: `main` was actually the v2 lineage (v2.0 merged in via PR #12 on
2026-07-03) but was documented as "frozen v1"; active work lived on a long-lived
`v2` branch; and parallel `v1.0`/`v2.0` version branches sat frozen. A Cursor
cloud run failed because its base branch had no Python env, and — separately —
`requirements.txt` pinned `uvicorn==0.30.6`, which conflicts with `mcp`
(needs `>=0.31.1`), so a fresh `pip install -r requirements.txt` couldn't resolve
at all. Nothing gated what agents produced before it could reach the main line.

The project already has a strong safety asset: ~1263 hermetic tests (Ollama/HTTP
mocked; 5 `slow` embedding-model tests excepted). The missing piece was a gate
that runs them automatically.

## Decision

Adopt **trunk-based development on `main`**, with agents proposing changes through
CI-gated PRs.

- **`main` is the single trunk.** Merged all v2 work into it (PR #13); `main` is
  now current. Stopped maintaining parallel dev branches.
- **Short-lived branches → PR → CI → merge → delete.** `feat/…`, `fix/…`,
  `chore/…` (agents: `cursor/…`). No long-lived feature branches.
- **CI** (`.github/workflows/ci.yml`): GitHub Actions runs `pytest -m "not slow"`
  on every PR into `main` and on pushes to `main`. The `slow` tests (real
  embedding-model download, not hermetic) run locally, not in CI.
- **Branch protection on `main`:** require a PR + the `test` status check to pass;
  block force-pushes. `enforce_admins=false` — the owner keeps a direct-push
  escape hatch, but agents (non-admin) are fully gated and cannot merge red CI.
  This matches the actual goal: guard against bad *agent* merges, not add friction
  for the solo owner.
- **Releases are tags, not branches.** Retired the `v2`/`v2.0` branches; preserved
  `v2.0` as a tag. Future snapshots are tags on `main`.
- **Cloud agents base off `main`.** The `.cursor/` env (Dockerfile + install) and a
  working `requirements.txt` (uvicorn pinned to the known-good `0.44.0`) now live on
  `main`, so cloud agents boot with Python and install cleanly.

## Consequences

- Every agent-authored change is exercised by the full hermetic suite before it can
  reach `main` — the tests become the trust boundary for delegation.
- Both editors (Claude Code, Cursor) and cloud agents share one trunk and one CI
  gate; the base-branch confusion is gone.
- The owner can still bypass in a genuine emergency (admin), which is a deliberate
  solo-dev tradeoff over `enforce_admins=true`.
- Cost: a CI round-trip (~2 min) per merge. Acceptable for the safety it buys.
- Follow-up: point the Cursor/Slack automation's base branch at `main` in the
  Cursor dashboard (UI-only step).
