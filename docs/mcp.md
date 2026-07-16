# MCP integration

Mahoraga exposes a stdio MCP server that forwards tool calls to the FastAPI
backend. The backend must already be running.

## Start the backend

```bash
source .venv/bin/activate
orch serve
curl http://127.0.0.1:8000/api/health
```

The MCP process itself starts with:

```bash
python -m backend.mcp.server
```

It communicates over standard input/output, so an MCP client normally launches
it rather than a user running it interactively.

## Client configuration

Use the absolute repository and virtual-environment paths for your machine:

```json
{
  "mcpServers": {
    "mahoraga": {
      "command": "/absolute/path/to/Mahoraga/.venv/bin/python",
      "args": ["-m", "backend.mcp.server"],
      "cwd": "/absolute/path/to/Mahoraga",
      "env": {
        "MAHORAGA_BASE": "http://127.0.0.1:8000"
      }
    }
  }
}
```

The exact outer configuration file differs by MCP client. The `mahoraga`
server object and process fields are portable.

`MAHORAGA_BASE` defaults to `http://localhost:8000`.
`MAHORAGA_MCP_RETRIES` defaults to 2 for read timeouts.

## Tools

The current bridge exposes nine tools:

| Tool | Purpose |
| --- | --- |
| `health_check` | Combine API, routing, and agent health into one status |
| `run_task` | Route and execute one task |
| `run_batch` | Execute a dependency-aware batch in safe waves |
| `route_task` | Preview classification and selection without execution |
| `agent_status` | List registered agents and health |
| `routing_stats` | Summarize decisions and rewards for a time window |
| `switch_strategy` | Switch the live selection strategy |
| `recent_decisions` | Inspect recent routing outcomes |
| `switch_routing_mode` | Change local-first/balanced/quality-first preference |

### `run_task`

Required input:

```json
{"prompt": "Write tests for the parser"}
```

Optional fields:

- `cwd` — worker directory.
- `capability_hint` — `code`, `plan`, or `general`.
- `agent_override` — force a registered agent identifier.

This is a real execution. It records a routing decision and outcome, updates
the bandit, and adds an episodic-memory entry.

### `route_task`

```json
{"prompt": "Review this migration for rollback risks"}
```

This previews the task classification, candidate scores, and selected agent. It
does not execute the task or commit a decision. Prefer it for health probes and
routing investigations so test traffic does not pollute the learning dataset.

### `run_batch`

```json
{
  "tasks": [
    {
      "prompt": "Implement the parser",
      "expected_files": ["src/parser.py"]
    },
    {
      "prompt": "Add parser tests",
      "depends_on": [0],
      "expected_files": ["tests/test_parser.py"]
    }
  ],
  "parallel": true,
  "max_concurrent": 2
}
```

- `depends_on` contains zero-based indices of earlier tasks.
- `expected_files` lets the scheduler serialize tasks that may write the same
  path.
- A batch accepts 1–10 tasks.
- `max_concurrent` accepts 1–5 and defaults to 2.

### Health and history

`health_check` reports:

- backend uptime and current strategy;
- registered and online agent counts;
- degradation level;
- quarantine and drift state;
- budget state; and
- execution-pool depth.

`routing_stats` accepts a `window` of `1h`, `24h`, `7d`, `30d`, or `all`.

`recent_decisions` accepts an optional limit, agent filter, capability filter,
or batch ID.

### Runtime controls

`switch_strategy` accepts `linucb`, `ucb1`, `thompson`, or `static`.
The service boots with `linucb_per_bucket`; the current MCP schema does not
offer that value when switching back, so use the routing strategy API or
restart the service if needed.

`switch_routing_mode` accepts:

- `local_first`
- `balanced`
- `quality_first`

The selected mode is persisted to `~/.mahoraga-v2/config.json`.

## Failure behavior

The bridge returns structured error text instead of raising transport errors
to the client. Read timeouts retry with short backoff. Connection failures
usually mean:

1. `orch serve` is not running;
2. `MAHORAGA_BASE` points to the wrong port; or
3. the backend is bound to another interface.

Check the same base URL directly:

```bash
curl "$MAHORAGA_BASE/api/health"
```

If the backend is healthy but all agents are down, verify Ollama separately:

```bash
curl http://127.0.0.1:11434/api/tags
```

## Data hygiene

Every `run_task` and successful item in `run_batch` becomes training data for
the online router. Use `route_task` for previews, and label deliberate
experiments through `orch bench run --notes` so they remain distinguishable in
the experiment ledger.
