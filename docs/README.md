# Mahoraga documentation

These guides describe the behavior of the current `main` branch.

## Use Mahoraga

- [Getting started](getting-started.md) — install the backend and UI, pull the
  local models, start the service, and verify it.
- [Configuration](configuration.md) — configure agents, routing behavior,
  environment variables, and local state.
- [CLI reference](cli-reference.md) — current `orch` command tree, command
  families, prerequisites, and port behavior.
- [MCP integration](mcp.md) — connect an MCP client and use Mahoraga's nine
  stdio tools.
- [Experiments and evaluation](experimentation.md) — run live batches,
  simulations, replays, and execution-verified evaluations.
- [Results](RESULTS.md) — every published benchmark number, the artifact behind
  it, how to verify it in one second, and what it does and does not support.

## Understand and extend Mahoraga

- [`../README.md`](../README.md) — project overview and architecture.
- [`../agents.yaml`](../agents.yaml) — source of truth for the enabled agent
  roster.
- [`specs/`](specs/) — design specifications and research plans.
- [`../brain/state/current_state.md`](../brain/state/current_state.md) — latest
  local research findings and operational state.
- [`../brain/decisions/`](../brain/decisions/) — architecture decision records.

Specifications are historical design documents as well as implementation
plans. A spec's status header reflects when it was authored and may not describe
the current runtime. Prefer these operator guides, `agents.yaml`, and
`orch <command> --help` when the sources differ.

## Common paths

| Path | Purpose |
| --- | --- |
| `backend/orchestrator/service/` | FastAPI service and endpoints |
| `backend/orchestrator/routing/` | Classifier, bandits, rewards, and memory |
| `backend/orchestrator/adapters/` | Agent registration and health checks |
| `backend/orchestrator/workers/` | Task execution implementations |
| `backend/orchestrator/cli/` | `orch` command implementation |
| `backend/mcp/` | MCP stdio bridge |
| `frontend/` | React/Vite web interface |
| `tests/` | Pytest suite |
| `~/.mahoraga-v2/` | Local runtime state |
