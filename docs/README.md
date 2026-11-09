# Mahoraga documentation

These guides describe the behavior of the current `main` branch.

## The study

Mahoraga is a long-term study of where local AI has caught up with cloud AI, measured on real
commits with each repository's own tests and dated because the line is expected to move.

- [The map](../README.md#the-map-so-far) is the current state, one row per kind of work, with
  local and cloud on the same tasks.
- [The report](report/2026-10-local-coding-ai.md) is the full write-up. Every measured figure
  in it links to the attempts it was computed from, every outside one to its source, and the
  site's [Research page](https://kaitosoeno.com/mahoraga/) publishes it one finding at a time.
- [The study's direction](../brain/decisions/2026-11-08-the-map.md) is the decision record
  for the map, what counts as local having caught up, and what may join it. It refines
  [the 10-29 record](../brain/decisions/2026-10-29-long-term-study.md), which still sets the
  conditions pooled results from readers wait on.
- [`../DATAFLOW.md`](../DATAFLOW.md) says what each part of Mahoraga sends where, written
  for a reader who doesn't read code.

## Replicate it on your repository

- [Getting started](getting-started.md) installs the audit, checks your machine and
  repository, and queues a night of runs.
- [CLI reference](cli-reference.md#the-audit) covers the audit's commands and their main
  flags, from mining through the queue and its worker, the cloud arm, the report and the
  export to the verdict.
- [Configuration](configuration.md) covers environment variables and local state,
  including `MAHORAGA_HOME`.

## The app, the router and MCP

These came before the study, and no number on the map depends on them. The router only has
work to do once a row of the map says local has caught up.

- [The app, the router and MCP](app.md) is their overview: the interfaces, the demo, the
  cascade, routing and rewards, the routing benchmark and its monitoring.
- [Getting started](getting-started.md#set-up-the-router) sets up the backend, the local
  models and the web UI.
- [Configuration](configuration.md) covers agents, routing behavior and its environment
  variables.
- [CLI reference](cli-reference.md) maps the whole command tree and its port behavior.
- [MCP integration](mcp.md) connects an MCP client to Mahoraga's nine stdio tools.
- [Experiments and evaluation](experimentation.md) runs live batches, simulations,
  replays and execution-verified evaluations.
- [Results](RESULTS.md) lists every published routing number, the artifact behind it,
  how to verify it in one second, and what it does and does not support.

## Understand and extend Mahoraga

- [`../README.md`](../README.md) is the study's overview: the map, the method and how to
  replicate it.
- [`../agents.yaml`](../agents.yaml) is the source of truth for the router's agent
  roster.
- [`specs/`](specs/) holds design specifications and research plans.
- [`../brain/state/current_state.md`](../brain/state/current_state.md) is the latest
  research and operational state, and [`../brain/decisions/`](../brain/decisions/) holds
  the architecture decision records.

Specifications are historical design documents as well as implementation plans. A spec's
status header reflects when it was written and may not describe the current runtime, so
prefer these guides, `agents.yaml` and `mahoraga <command> --help` when the sources
differ.

## Common paths

| Path | Purpose |
| --- | --- |
| `backend/orchestrator/agentbench/` | The audit: mining, sandboxes, runner, grader, queue and worker |
| `backend/orchestrator/audit/` | The verdict and its dated prices |
| `backend/orchestrator/service/` | FastAPI service and endpoints |
| `backend/orchestrator/routing/` | Classifier, bandits, rewards, memory and claim verification |
| `backend/orchestrator/adapters/` | Agent registration and health checks |
| `backend/orchestrator/workers/` | Task execution implementations |
| `backend/orchestrator/cli/` | The `mahoraga` (and `orch`) command |
| `backend/mcp/` | MCP stdio bridge |
| `frontend/` | The app (React and Vite) |
| `site/` | The public site at kaitosoeno.com/mahoraga |
| `experiments/` | Committed attempts and `claims.json`, which `bench verify` checks |
| `tests/` | Pytest suite |
| `~/.mahoraga-v2/` | Local runtime state (moved by `MAHORAGA_HOME`) |
