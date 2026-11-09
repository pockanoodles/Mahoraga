# Mahoraga documentation

These guides describe the behavior of the current `main` branch.

## The study

- [The report](report/2026-10-local-coding-ai.md) asks whether local coding AI is worth
  it on a 16 GB Mac. Every measured figure in it links to the attempts it was computed
  from, every outside one to its source, and
  the site's [Research page](https://kaitosoeno.com/mahoraga/) publishes it one finding
  at a time.
- [The study's direction](../brain/decisions/2026-10-29-long-term-study.md) is the
  decision record for why Mahoraga is a long-term study, what the site publishes, and the
  conditions pooled results from readers wait on.
- [`../DATAFLOW.md`](../DATAFLOW.md) says what each part of Mahoraga sends where, written
  for a reader who doesn't read code.

## Run the audit

- [Getting started](getting-started.md) installs the audit, checks your machine and
  repository, and queues a night of runs.
- [CLI reference](cli-reference.md#the-audit) covers the audit's commands and their main
  flags, from mining through the queue and its worker, the cloud arm, the report and the
  export to the verdict.
- [Configuration](configuration.md) covers environment variables and local state,
  including `MAHORAGA_HOME`.

## The routing layer

The router is the stage that acts on the audit's verdict, by answering with a local model
and paying for a cloud model only when the local answer fails.

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

- [`../README.md`](../README.md) is the project overview and architecture.
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
