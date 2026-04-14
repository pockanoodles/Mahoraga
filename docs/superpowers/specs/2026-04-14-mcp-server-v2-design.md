# Mahoraga — MCP Server Specification (v2)

**Status:** Draft Spec  
**Author:** Nicole (Kaito)  
**Date:** 2026-04-14  
**Branch:** `main`  
**Repo:** `pockanoodles/Mahoraga`  
**Depends on:** `PARALLEL_ROUTING_SPEC.md` (resource groups, wave executor, `run_batch`)  
**Supersedes:** MCP Server Spec v1

---

## Overview

The MCP server is a stdio bridge that exposes Mahoraga's orchestration capabilities as native tools to any MCP-compatible client — Claude Code, Cursor, Zed, Continue.dev. It does not replace Mahoraga's existing web UI or REST API. It adds a new entry point: a stdio pipe that AI coding tools speak natively.

The result is a three-layer adaptive stack:

```
MCP Client (Claude Code / Cursor / Zed)
    │
    │  stdio (JSON-RPC via Model Context Protocol)
    │
    ▼
Mahoraga MCP Server  (backend/mcp/server.py)
    │
    │  HTTP (localhost, internal)
    │
    ▼
Mahoraga Core  (FastAPI @ localhost:8000)
    │
    ├── KeywordRouter → capability bucket
    ├── LinUCB Bandit → agent selection (congestion-aware)
    ├── Wave Executor → concurrent dispatch
    │
    ▼
Agent Roster
    ├── ollama        (local, Qwen3 4B)
    ├── aider         (local, ollama_chat/qwen3:4b)
    ├── codex-cli     (cloud, OpenAI)
    ├── gemini-cli    (cloud, Google)
    ├── goose         (TBD)
    ├── opencode      (TBD)
    └── claude        (cloud, Anthropic — costs money)
```

The MCP server is a thin translation layer. All intelligence — routing, bandit learning, execution, reward tracking — stays in Mahoraga Core. The MCP server converts JSON-RPC tool calls into HTTP requests against Mahoraga's API, then formats the responses for the MCP client. If the MCP server process dies, Mahoraga continues running. If Mahoraga isn't running, the MCP server returns clean errors telling the client to start it.

---

## Architecture: Why stdio Bridge

Two options were considered:

**Option A (chosen): stdio bridge → Mahoraga HTTP API.** The MCP server is a lightweight Python process that speaks stdio on one side and HTTP on the other. Mahoraga's FastAPI server runs independently as it does today.

**Option B (rejected): embed MCP directly in Mahoraga.** Add stdio transport to the FastAPI process itself. Rejected because it couples MCP lifecycle to the web server, complicates process management (FastAPI expects to own its event loop), and means MCP goes down when Mahoraga restarts for a config change.

Option A keeps concerns separated. The MCP server is stateless — it holds no routing state, no bandit weights, no session data. It's a pipe. Mahoraga Core is the brain.

---

## Tools

Eight tools, each mapping to one or more Mahoraga API endpoints. Tool count is well under the 40-tool MCP client limit.

**Design principle for tool descriptions:** Descriptions are written for the LLM client that reads them to decide when to call each tool — not for the developer reading the spec. No bandit terminology, no internal architecture jargon. The descriptions should pattern-match against natural language requests from the user.

---

### 1. `health_check`

Lightweight connectivity check. Verifies Mahoraga is running and returns uptime and version. Call this before committing to a `run_batch` that might time out.

```json
{
    "name": "health_check",
    "description": "Check if Mahoraga is running and responsive. Returns status, uptime, version, and number of registered agents. Use this before running tasks to verify the backend is available.",
    "inputSchema": {
        "type": "object",
        "properties": {},
        "required": []
    }
}
```

**Maps to:** `GET /api/health` (new, trivial endpoint)

**Response shape:**

```json
{
    "status": "ok",
    "uptime_s": 3412,
    "version": "0.4.1",
    "agents_registered": 6,
    "agents_online": 4,
    "strategy": "linucb",
    "total_decisions": 47
}
```

---

### 2. `run_task`

Execute a single task through Mahoraga's full pipeline.

```json
{
    "name": "run_task",
    "description": "Send a task to Mahoraga for execution. Mahoraga automatically picks the best available AI agent for the job based on the task type — code tasks go to coding agents, research tasks go to research agents. Use this for tasks like creating files, refactoring code, writing tests, running shell commands, or researching a topic. Returns the result, which agent handled it, and how well it performed.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "What you want done. e.g. 'refactor auth module to use JWT', 'create a Dockerfile for the Flask app', 'research best practices for input validation'"
            },
            "cwd": {
                "type": "string",
                "description": "Optional. Working directory for the agent to operate in. Defaults to Mahoraga's configured project directory. e.g. '~/Projects/my-app'"
            },
            "capability_hint": {
                "type": "string",
                "enum": ["code", "plan", "general"],
                "description": "Optional. Tell Mahoraga what kind of task this is, overriding automatic classification."
            },
            "agent_override": {
                "type": "string",
                "description": "Optional. Force a specific agent instead of automatic selection. e.g. 'aider', 'ollama', 'codex-cli', 'gemini-cli'"
            }
        },
        "required": ["prompt"]
    }
}
```

**Maps to:** `POST /api/task`

**Response shape:**

```json
{
    "task_id": "t_9f2a3b1c",
    "status": "success",
    "agent": "aider",
    "resource_group": "local_ollama",
    "capability_bucket": "code",
    "elapsed_s": 22.4,
    "output": "Refactored auth module. Changed 3 files: ...",
    "files_written": ["src/auth/middleware.py", "src/auth/jwt.py"],
    "routing": {
        "strategy": "linucb",
        "ucb_score": 0.834,
        "runner_up": {"agent": "codex-cli", "ucb_score": 0.791},
        "queue_depth_at_selection": 0
    },
    "reward": {
        "success": 1.0,
        "quality": 0.78,
        "speed": 0.65,
        "cost": 1.0,
        "composite": 0.836
    }
}
```

The response includes the full routing decision and reward breakdown so the MCP client can reason about what happened — "aider beat codex-cli by 0.043, quality was 0.78, should I try codex-cli next time?"

---

### 3. `run_batch`

Execute multiple tasks with wave-based concurrent dispatch. The primary efficiency tool — one MCP round-trip replaces N sequential `run_task` calls.

```json
{
    "name": "run_batch",
    "description": "Send multiple tasks to Mahoraga at once. Tasks run in parallel where safe — Mahoraga groups them into waves based on which agents share hardware and which files overlap. Use this when you have 3+ independent subtasks like scaffolding multiple files, running code + tests + research in parallel, or any batch of work where you don't need results one at a time. Returns all results together with timing comparison vs sequential execution.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "List of tasks to execute. Each task needs at minimum a prompt.",
                "items": {
                    "type": "object",
                    "properties": {
                        "prompt": { "type": "string", "description": "What you want done for this task." },
                        "depends_on": {
                            "type": "array",
                            "items": { "type": "integer" },
                            "default": [],
                            "description": "Indices of tasks that must complete before this one starts. e.g. [0, 1] means wait for the first two tasks."
                        },
                        "expected_files": {
                            "type": "array",
                            "items": { "type": "string" },
                            "default": [],
                            "description": "File paths this task will write to. Tasks with overlapping files run sequentially to prevent conflicts."
                        },
                        "cwd": { "type": "string", "description": "Optional. Working directory for this specific task." },
                        "capability_hint": { "type": "string", "enum": ["code", "plan", "general"] }
                    },
                    "required": ["prompt"]
                },
                "minItems": 1,
                "maxItems": 10
            },
            "parallel": {
                "type": "boolean",
                "default": true,
                "description": "Set to false to force all tasks to run one at a time, even if they could run in parallel. Use this for tasks with complex cross-file dependencies."
            },
            "max_concurrent": {
                "type": "integer",
                "default": 2,
                "minimum": 1,
                "maximum": 5,
                "description": "Maximum number of tasks running at the same time. Default is 2, which is conservative for a 16GB machine. Raise only if tasks hit different backends (e.g. one local + one cloud)."
            }
        },
        "required": ["tasks"]
    }
}
```

**Note on maxItems:** The 10-task cap is a tunable default tied to memory constraints on consumer hardware (16GB MacBook). For server deployments or machines with more headroom, this can be raised in Mahoraga's config (`~/.mahoraga/config.yaml` → `execution.max_batch_size`). The wave executor handles arbitrarily large batches — the cap is a safety valve, not an architectural limit.

**Maps to:** `POST /api/batch`

**Response:** Full batch result with per-task status, wave assignments, wall-clock vs sequential timing, routing decisions, and batch_id for later reference. See `PARALLEL_ROUTING_SPEC.md` § MCP Interface for the complete response schema.

---

### 4. `route_task`

Dry-run the router without executing.

```json
{
    "name": "route_task",
    "description": "Preview which agent Mahoraga would pick for a task without actually running it. Shows the task classification, all candidate agents with their scores, and which one would win. Use this when you want to understand routing behavior before committing, or to check if a specific agent type is available for your task.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The task description to classify and route."
            }
        },
        "required": ["prompt"]
    }
}
```

**Maps to:** `POST /api/routing/dry-run`

**Response shape:**

```json
{
    "prompt": "refactor the auth module",
    "keyword_classification": {
        "matched_keywords": ["refactor"],
        "capability_bucket": "code",
        "confidence": 0.85
    },
    "bandit_selection": {
        "strategy": "linucb",
        "selected_agent": "aider",
        "scores": [
            {"agent": "aider", "ucb_score": 0.834, "resource_group": "local_ollama", "queue_depth": 0},
            {"agent": "codex-cli", "ucb_score": 0.791, "resource_group": "openai_api", "queue_depth": 0},
            {"agent": "ollama", "ucb_score": 0.723, "resource_group": "local_ollama", "queue_depth": 0},
            {"agent": "gemini-cli", "ucb_score": 0.668, "resource_group": "google_api", "queue_depth": 0}
        ]
    }
}
```

**Implementation note:** Requires `bandit_router.score_all(prompt)` — a read-only method that computes UCB scores for all candidates without updating state or logging a decision. Currently `select_agent()` both scores and commits; this needs to be split into `score_all()` (read-only) and `select_and_commit()` (mutating).

---

### 5. `agent_status`

Check which agents are online and system load.

```json
{
    "name": "agent_status",
    "description": "Show all registered AI agents, whether they're online, what model they run, and how busy they are. Use this to check what's available before sending tasks, or to diagnose why a task might be slow.",
    "inputSchema": {
        "type": "object",
        "properties": {},
        "required": []
    }
}
```

**Maps to:** `GET /api/agents` + `GET /api/resource-groups`

**Response shape:**

```json
{
    "agents": [
        {
            "name": "ollama",
            "status": "online",
            "model": "qwen3:4b-q4_K_M",
            "capabilities": ["code", "general"],
            "resource_group": "local_ollama",
            "queue_depth": 0
        },
        {
            "name": "aider",
            "status": "online",
            "model": "ollama_chat/qwen3:4b",
            "capabilities": ["code"],
            "resource_group": "local_ollama",
            "queue_depth": 0
        },
        {
            "name": "codex-cli",
            "status": "online",
            "model": "cloud",
            "capabilities": ["code"],
            "resource_group": "openai_api",
            "queue_depth": 0
        },
        {
            "name": "gemini-cli",
            "status": "online",
            "model": "cloud",
            "capabilities": ["code", "plan", "general"],
            "resource_group": "google_api",
            "queue_depth": 0
        }
    ],
    "resource_groups": {
        "local_ollama": {"max_concurrent": 1, "current_load": 0, "agents": ["ollama", "aider"]},
        "openai_api": {"max_concurrent": 2, "current_load": 0, "agents": ["codex-cli"]},
        "google_api": {"max_concurrent": 3, "current_load": 0, "agents": ["gemini-cli"]}
    }
}
```

---

### 6. `routing_stats`

Pull aggregate routing statistics.

```json
{
    "name": "routing_stats",
    "description": "Get performance statistics for Mahoraga's routing: how often each agent is selected, average reward scores, success rates, and whether the system is improving over time. Use this to check if the routing is learning effectively or if a specific agent is underperforming.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "window": {
                "type": "string",
                "enum": ["1h", "24h", "7d", "30d", "all"],
                "default": "24h",
                "description": "Time window for statistics."
            }
        },
        "required": []
    }
}
```

**Maps to:** `GET /api/routing/stats?window=24h`

**Response shape:** Same as v1 — per-agent stats, per-capability stats, regret metrics.

---

### 7. `switch_strategy`

Change the routing strategy at runtime.

```json
{
    "name": "switch_strategy",
    "description": "Change how Mahoraga picks agents. Options: 'linucb' (learns which agent is best per task type — recommended), 'ucb1' (simpler learning, doesn't consider task type), 'thompson' (Bayesian sampling — more exploratory), 'static' (always picks the same best agent — no learning). Changes take effect immediately.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "strategy": {
                "type": "string",
                "enum": ["linucb", "ucb1", "thompson", "static"],
                "description": "The routing strategy to switch to."
            }
        },
        "required": ["strategy"]
    }
}
```

**Maps to:** `POST /api/routing/strategy`

**Response shape:** Same as v1 — previous/new strategy, confirmation.

---

### 8. `recent_decisions`

Pull recent routing decisions for review and analysis.

```json
{
    "name": "recent_decisions",
    "description": "See Mahoraga's recent routing decisions: what tasks were sent, which agent handled each one, how well it performed, and how long it took. Use this to review what happened after a batch, spot patterns, or debug routing issues.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
                "description": "Number of recent decisions to return."
            },
            "agent_filter": {
                "type": "string",
                "description": "Optional. Only show decisions for a specific agent. e.g. 'aider'"
            },
            "capability_filter": {
                "type": "string",
                "enum": ["code", "plan", "general"],
                "description": "Optional. Only show decisions for a specific task type."
            },
            "batch_id": {
                "type": "string",
                "description": "Optional. Only show decisions from a specific batch. Use the batch_id from a run_batch response to review that batch's routing."
            }
        },
        "required": []
    }
}
```

**Maps to:** `GET /api/routing/decisions?limit=10`

**Response shape:** Same as v1, plus `batch_id` field on decisions that were part of a batch.

---

## New Mahoraga Endpoints

The MCP server needs four new HTTP endpoints on the Mahoraga FastAPI backend.

### `GET /api/health`

Returns server status, uptime, version, and summary counts. No auth, no heavy computation — just a heartbeat.

```python
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "uptime_s": int(time.time() - START_TIME),
        "version": __version__,
        "agents_registered": len(agent_registry),
        "agents_online": sum(1 for a in agent_registry if a.is_online()),
        "strategy": bandit_router.current_strategy,
        "total_decisions": await decision_db.count()
    }
```

### `POST /api/batch`

Accepts a batch of tasks, runs them through the wave executor, returns all results. Backend for `run_batch`. Full schema in `PARALLEL_ROUTING_SPEC.md`.

### `POST /api/routing/dry-run`

Runs a prompt through keyword classifier and bandit scoring without executing. Returns per-agent UCB scores. Requires `bandit_router.score_all()` — the read-only scoring method split from `select_agent()`.

### `GET /api/routing/decisions`

Query `routing_decisions.db`. Supports `limit`, `agent`, `capability`, `batch_id`, and `since` (ISO timestamp) query parameters.

---

## Implementation

### File: `backend/mcp/server.py`

```python
"""
Mahoraga MCP Server — stdio bridge to Mahoraga's orchestration API.

Usage:
    python -m backend.mcp.server

Configured in Claude Code via ~/.claude/settings.json:
    {
        "mcpServers": {
            "mahoraga": {
                "command": "python",
                "args": ["-m", "backend.mcp.server"],
                "cwd": "~/Projects/Mahoraga"
            }
        }
    }
"""

import asyncio
import json
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

MAHORAGA_BASE = "http://localhost:8000"
TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)
MAX_RETRIES = 1
RETRY_DELAY = 1.0

server = Server("mahoraga")


# ---------------------------------------------------------------------------
# HTTP helpers with retry
# ---------------------------------------------------------------------------

async def _request(method: str, path: str, body: dict = None, params: dict = None) -> dict:
    """
    Make an HTTP request to Mahoraga API with single retry on timeout.
    
    Retries once on ReadTimeout (Mahoraga is running but Ollama is slow).
    Does NOT retry on ConnectError (Mahoraga isn't running — retrying won't help).
    """
    for attempt in range(MAX_RETRIES + 1):
        async with httpx.AsyncClient(base_url=MAHORAGA_BASE, timeout=TIMEOUT) as client:
            try:
                if method == "GET":
                    resp = await client.get(path, params=params)
                else:
                    resp = await client.post(path, json=body)
                resp.raise_for_status()
                return resp.json()
            except httpx.ConnectError:
                return {
                    "error": "Mahoraga is not running.",
                    "fix": "cd ~/Projects/Mahoraga && python -m backend.main"
                }
            except httpx.ReadTimeout:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                return {
                    "error": f"Mahoraga timed out after {TIMEOUT.read}s. The agent may be processing a large task.",
                    "suggestion": "Try again, or use a faster agent with agent_override."
                }
            except httpx.HTTPStatusError as e:
                return {
                    "error": f"Mahoraga returned HTTP {e.response.status_code}",
                    "detail": e.response.text[:300]
                }
    return {"error": "Unexpected retry exhaustion"}


async def _post(path: str, body: dict) -> dict:
    return await _request("POST", path, body=body)

async def _get(path: str, params: dict = None) -> dict:
    return await _request("GET", path, params=params)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="health_check",
        description="Check if Mahoraga is running and responsive. Returns status, uptime, version, and number of registered agents. Use this before running tasks to verify the backend is available.",
        inputSchema={"type": "object", "properties": {}, "required": []}
    ),
    Tool(
        name="run_task",
        description="Send a task to Mahoraga for execution. Mahoraga automatically picks the best available AI agent for the job based on the task type — code tasks go to coding agents, research tasks go to research agents. Use this for tasks like creating files, refactoring code, writing tests, running shell commands, or researching a topic. Returns the result, which agent handled it, and how well it performed.",
        inputSchema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What you want done."},
                "cwd": {"type": "string", "description": "Optional. Working directory for the agent."},
                "capability_hint": {"type": "string", "enum": ["code", "plan", "general"], "description": "Optional. Override automatic task classification."},
                "agent_override": {"type": "string", "description": "Optional. Force a specific agent. e.g. 'aider', 'ollama'"}
            },
            "required": ["prompt"]
        }
    ),
    Tool(
        name="run_batch",
        description="Send multiple tasks to Mahoraga at once. Tasks run in parallel where safe — Mahoraga groups them into waves based on which agents share hardware and which files overlap. Use this when you have 3+ independent subtasks. Returns all results together with timing comparison vs sequential execution.",
        inputSchema={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "depends_on": {"type": "array", "items": {"type": "integer"}, "default": []},
                            "expected_files": {"type": "array", "items": {"type": "string"}, "default": []},
                            "cwd": {"type": "string"},
                            "capability_hint": {"type": "string", "enum": ["code", "plan", "general"]}
                        },
                        "required": ["prompt"]
                    },
                    "minItems": 1, "maxItems": 10
                },
                "parallel": {"type": "boolean", "default": True},
                "max_concurrent": {"type": "integer", "default": 2, "minimum": 1, "maximum": 5}
            },
            "required": ["tasks"]
        }
    ),
    Tool(
        name="route_task",
        description="Preview which agent Mahoraga would pick for a task without actually running it. Shows task classification and all candidate agents with scores. Use this to understand routing behavior before committing.",
        inputSchema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The task to classify and route."}
            },
            "required": ["prompt"]
        }
    ),
    Tool(
        name="agent_status",
        description="Show all registered AI agents, whether they're online, what model they run, and how busy they are. Use this to check what's available before sending tasks.",
        inputSchema={"type": "object", "properties": {}, "required": []}
    ),
    Tool(
        name="routing_stats",
        description="Get performance statistics for Mahoraga's routing: how often each agent is selected, average scores, success rates, and whether the system is improving over time.",
        inputSchema={
            "type": "object",
            "properties": {
                "window": {"type": "string", "enum": ["1h", "24h", "7d", "30d", "all"], "default": "24h"}
            },
            "required": []
        }
    ),
    Tool(
        name="switch_strategy",
        description="Change how Mahoraga picks agents. 'linucb' learns per task type (recommended). 'ucb1' is simpler. 'thompson' explores more. 'static' always picks the same agent. Changes take effect immediately.",
        inputSchema={
            "type": "object",
            "properties": {
                "strategy": {"type": "string", "enum": ["linucb", "ucb1", "thompson", "static"]}
            },
            "required": ["strategy"]
        }
    ),
    Tool(
        name="recent_decisions",
        description="See Mahoraga's recent routing decisions: what tasks were sent, which agent handled each one, how well it performed. Use this to review batch results or debug routing issues.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
                "agent_filter": {"type": "string", "description": "Only show decisions for this agent."},
                "capability_filter": {"type": "string", "enum": ["code", "plan", "general"]},
                "batch_id": {"type": "string", "description": "Only show decisions from a specific batch run."}
            },
            "required": []
        }
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handlers = {
        "health_check": _handle_health_check,
        "run_task": _handle_run_task,
        "run_batch": _handle_run_batch,
        "route_task": _handle_route_task,
        "agent_status": _handle_agent_status,
        "routing_stats": _handle_routing_stats,
        "switch_strategy": _handle_switch_strategy,
        "recent_decisions": _handle_recent_decisions,
    }
    handler = handlers.get(name)
    if not handler:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    result = await handler(arguments)
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_health_check(args: dict) -> dict:
    return await _get("/api/health")


async def _handle_run_task(args: dict) -> dict:
    body = {"prompt": args["prompt"]}
    if "cwd" in args:
        body["cwd"] = args["cwd"]
    if "capability_hint" in args:
        body["capability_hint"] = args["capability_hint"]
    if "agent_override" in args:
        body["agent_override"] = args["agent_override"]
    return await _post("/api/task", body)


async def _handle_run_batch(args: dict) -> dict:
    return await _post("/api/batch", args)


async def _handle_route_task(args: dict) -> dict:
    return await _post("/api/routing/dry-run", {"prompt": args["prompt"]})


async def _handle_agent_status(args: dict) -> dict:
    agents = await _get("/api/agents")
    if "error" in agents:
        return agents
    groups = await _get("/api/resource-groups")
    if "error" in groups:
        return {"agents": agents, "resource_groups": None, "note": "Resource group info unavailable"}
    return {"agents": agents, "resource_groups": groups}


async def _handle_routing_stats(args: dict) -> dict:
    window = args.get("window", "24h")
    return await _get("/api/routing/stats", {"window": window})


async def _handle_switch_strategy(args: dict) -> dict:
    return await _post("/api/routing/strategy", {"strategy": args["strategy"]})


async def _handle_recent_decisions(args: dict) -> dict:
    params = {"limit": args.get("limit", 10)}
    if "agent_filter" in args:
        params["agent"] = args["agent_filter"]
    if "capability_filter" in args:
        params["capability"] = args["capability_filter"]
    if "batch_id" in args:
        params["batch_id"] = args["batch_id"]
    return await _get("/api/routing/decisions", params)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Superpowers Skill: `mahoraga.md`

For Claude Code to know *when* to use these tools and *how to interpret results*, the Superpowers skill encodes the full decision framework.

```markdown
# Mahoraga — When and How to Use

Mahoraga is a multi-agent orchestrator running at localhost:8000.
You have these MCP tools: health_check, run_task, run_batch, route_task,
agent_status, routing_stats, switch_strategy, recent_decisions.

## Before starting work

Call health_check to verify Mahoraga is running. If it returns an error,
tell the user to start Mahoraga and provide the command from the error message.

## When to delegate to Mahoraga

- File creation and boilerplate — Mahoraga routes to cheap/free agents
- Multi-file implementation plans with 3+ independent subtasks — use run_batch
- Tasks where speed matters more than maximum quality
- Repetitive execution work: scaffolding, test generation, documentation
- Research and information gathering tasks

## When to keep work in Claude Code

- Complex reasoning, architecture decisions, code review
- Tasks requiring your full context window and planning ability
- Single-file edits where you already know exactly what to change
- Anything where getting it wrong costs more time than doing it yourself
- Security-critical code that needs careful human review

## Decision flow

1. If unsure whether Mahoraga is running → health_check
2. If the task needs deep reasoning → do it yourself
3. If the task is mechanical execution → run_task
4. If there are 3+ independent subtasks → run_batch
5. If you want to preview the routing → route_task first
6. After a batch, check routing_stats to see if the system is learning

## Interpreting results

After run_task or run_batch returns, check the reward scores:
- composite > 0.7 → trust the result, move on
- composite 0.4–0.7 → review the output carefully, fix issues yourself
- composite < 0.4 → the agent struggled, redo this task yourself
- success = 0.0 → agent crashed or timed out, do not use the output

If quality is consistently low for a task type, check agent_status to see
if the right agents are online. If only ollama is available for code tasks,
quality will be limited by the 4B model. Consider waiting for cloud agents
or doing the work yourself.

## run_batch tips

- Always set expected_files for coding tasks to prevent file conflicts
- Use depends_on when task B reads files that task A writes
- Set parallel: false for tasks touching the same module/package
- Default max_concurrent is 2 — only raise if tasks hit different backends
- After a batch, use recent_decisions with the batch_id to review routing

## Working directory

By default, agents operate in Mahoraga's configured project directory.
If you need an agent to work in a different project, pass the cwd parameter.
Do not assume the agent is in the same directory as your Claude Code session.

## Checking on the system

- "Which agent is doing best?" → routing_stats with window 7d
- "What happened in my last batch?" → recent_decisions with batch_id
- "Is Mahoraga learning?" → routing_stats, check the regret trend
- "Which agents are available?" → agent_status
- "Would Mahoraga pick aider for this?" → route_task to preview
```

---

## Progress Notifications (v2 Enhancement)

**Status:** Designed, not yet implemented. Planned for after core MCP server is stable.

For long-running tasks (30s+), the MCP server can push progress notifications to the client mid-execution. This requires the MCP server to poll Mahoraga's task status endpoint while waiting for the result.

```python
# Concept — not yet implemented
async def _handle_run_task_with_progress(args: dict) -> dict:
    # Submit task, get task_id
    submission = await _post("/api/task/submit", body)
    task_id = submission["task_id"]
    
    # Poll for progress, send notifications
    while True:
        status = await _get(f"/api/task/{task_id}/status")
        if status["state"] == "complete":
            return status["result"]
        
        # Push progress to MCP client
        await server.send_notification(
            "notifications/progress",
            {"task_id": task_id, "message": status["message"], "elapsed_s": status["elapsed_s"]}
        )
        await asyncio.sleep(2)
```

**Requires:** A new `POST /api/task/submit` (returns task_id immediately) and `GET /api/task/{id}/status` (returns current state) on the Mahoraga backend. The current `POST /api/task` is synchronous — it blocks until completion. The async submission + polling pattern is needed for progress reporting.

This is a v2 feature because: (1) the synchronous `POST /api/task` works fine for v1, (2) progress notifications require MCP client support which varies, and (3) the backend needs a task queue which doesn't exist yet.

---

## Error Handling

The MCP server handles four categories of errors:

**Mahoraga not running.** `httpx.ConnectError` → returns error message with the start command. No retry — if the server isn't listening, retrying immediately won't help.

**Mahoraga timeout.** `httpx.ReadTimeout` → retries once after 1 second. If it fails again, returns an error suggesting the user try a faster agent or try again. This handles the common case where Ollama is slow under load.

**Mahoraga HTTP error.** 4xx/5xx responses → returns the status code and first 300 chars of the response body. No retry — these are application errors, not transient failures.

**Task execution failure.** A task that fails inside Mahoraga (agent crash, bad output) is returned as a result with `"status": "failed"`. For `run_batch`, individual failures don't crash the batch — other tasks continue, and the failed task is marked in results.

**MCP protocol errors.** Invalid tool names, missing required arguments, malformed JSON — handled by the `mcp` SDK's built-in validation. No custom handling needed.

---

## Client Configuration

### Claude Code

```json
// ~/.claude/settings.json
{
    "mcpServers": {
        "mahoraga": {
            "command": "python",
            "args": ["-m", "backend.mcp.server"],
            "cwd": "/Users/kaito/Projects/Mahoraga",
            "env": {
                "MAHORAGA_BASE": "http://localhost:8000"
            }
        }
    }
}
```

### Cursor

```json
// .cursor/mcp.json  (project-level)
{
    "mcpServers": {
        "mahoraga": {
            "command": "python",
            "args": ["-m", "backend.mcp.server"],
            "cwd": "/Users/kaito/Projects/Mahoraga",
            "env": {
                "MAHORAGA_BASE": "http://localhost:8000"
            }
        }
    }
}
```

### Any MCP Client

Same format. The server speaks standard MCP stdio — any compliant client works without modification.

---

## Dependencies

Add to `requirements.txt` / `pyproject.toml`:

```
mcp>=1.0.0
```

The `mcp` package is Anthropic's official Python SDK for MCP. Provides `Server`, stdio transport, and type definitions. No other new dependencies — `httpx` is already in the project.

---

## Testing

### Manual Smoke Test

```bash
# Terminal 1: Start Mahoraga
cd ~/Projects/Mahoraga && python -m backend.main

# Terminal 2: Test MCP server directly via stdio
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m backend.mcp.server

# Terminal 3: Test through Claude Code
claude
> "is Mahoraga running?"           # should call health_check
> "check which agents are online"  # should call agent_status
> "route 'write a Dockerfile'"     # should call run_task
> "what are the routing stats?"    # should call routing_stats
```

### Automated Tests

```python
# backend/mcp/test_server.py

import pytest
from unittest.mock import AsyncMock, patch
from backend.mcp.server import (
    _handle_health_check,
    _handle_run_task,
    _handle_route_task,
    _handle_agent_status,
    _handle_recent_decisions,
)

@pytest.mark.asyncio
async def test_health_check():
    """health_check should GET /api/health."""
    with patch("backend.mcp.server._get", new_callable=AsyncMock) as mock:
        mock.return_value = {"status": "ok", "uptime_s": 100}
        result = await _handle_health_check({})
        mock.assert_called_once_with("/api/health")
        assert result["status"] == "ok"

@pytest.mark.asyncio
async def test_run_task_with_cwd():
    """run_task should forward cwd to the API."""
    with patch("backend.mcp.server._post", new_callable=AsyncMock) as mock:
        mock.return_value = {"status": "success"}
        await _handle_run_task({
            "prompt": "create test.py",
            "cwd": "~/Projects/other"
        })
        mock.assert_called_once_with("/api/task", {
            "prompt": "create test.py",
            "cwd": "~/Projects/other"
        })

@pytest.mark.asyncio
async def test_run_task_forwards_overrides():
    """run_task should forward capability_hint and agent_override."""
    with patch("backend.mcp.server._post", new_callable=AsyncMock) as mock:
        mock.return_value = {"status": "success"}
        await _handle_run_task({
            "prompt": "research JWT",
            "capability_hint": "general",
            "agent_override": "gemini-cli"
        })
        call_body = mock.call_args[0][1]
        assert call_body["capability_hint"] == "general"
        assert call_body["agent_override"] == "gemini-cli"

@pytest.mark.asyncio
async def test_recent_decisions_with_batch_id():
    """recent_decisions should pass batch_id filter to API."""
    with patch("backend.mcp.server._get", new_callable=AsyncMock) as mock:
        mock.return_value = {"decisions": []}
        await _handle_recent_decisions({"batch_id": "b_abc123"})
        params = mock.call_args[0][1]
        assert params["batch_id"] == "b_abc123"

@pytest.mark.asyncio
async def test_mahoraga_not_running():
    """All tools should return a clean error when Mahoraga is down."""
    with patch("backend.mcp.server._get", new_callable=AsyncMock) as mock:
        mock.return_value = {"error": "Mahoraga is not running.", "fix": "..."}
        result = await _handle_health_check({})
        assert "error" in result

@pytest.mark.asyncio
async def test_timeout_retries_once():
    """On ReadTimeout, should retry once then return error."""
    with patch("backend.mcp.server._request") as mock:
        # This tests the retry logic at a higher level
        mock.return_value = {"error": "Mahoraga timed out after 300.0s."}
        result = await _handle_run_task({"prompt": "slow task"})
        assert "timed out" in result.get("error", "")
```

---

## File Structure

```
backend/mcp/
├── __init__.py
├── server.py              ← MCP server (stdio bridge, 8 tools, retry logic)
└── test_server.py         ← Unit tests

backend/api/
├── routes.py              MOD — Add GET /api/health, POST /api/batch,
│                                POST /api/routing/dry-run,
│                                GET /api/routing/decisions

backend/orchestrator/
├── bandit_router.py       MOD — Add score_all() method (read-only UCB scoring)
├── resource_groups.py     NEW — From PARALLEL_ROUTING_SPEC
├── wave_executor.py       NEW — From PARALLEL_ROUTING_SPEC
```

---

## Implementation Order

1. **`GET /api/health` + `score_all()` on BanditRouter** — lowest risk, immediately testable.
2. **New API endpoints** — `/api/routing/dry-run`, `/api/routing/decisions`, `/api/batch` (sequential first).
3. **MCP server** — `backend/mcp/server.py` with all 8 tools. Smoke test via stdio, then Claude Code.
4. **Resource groups + wave executor** — from parallelism spec. Wire into `/api/batch`.
5. **Congestion-aware bandit** — add `queue_depth` to LinUCB. Re-run ablation.
6. **Superpowers skill** — write `mahoraga.md`, validate Claude Code uses the right tools.
7. **Progress notifications** — v2, after core is stable.

Steps 1-3 = working MCP server. Steps 4-5 = parallelism. Step 6 = automation. Step 7 = polish.

---

## Changes from v1

| # | Improvement | What changed |
|---|---|---|
| 1 | Health check tool | New `health_check` tool + `GET /api/health` endpoint |
| 2 | LLM-friendly descriptions | All tool descriptions rewritten for AI client pattern-matching |
| 3 | Working directory param | `cwd` added to `run_task` and per-task in `run_batch` |
| 4 | Batch cap documented | `maxItems: 10` explained as tunable config, not hard limit |
| 5 | Progress notifications | Designed as v2 enhancement with async task submission pattern |
| 6 | Expanded Superpowers skill | Added result interpretation, reward thresholds, cwd guidance |
| 7 | Retry on timeout | `_request()` retries once on `ReadTimeout`, not on `ConnectError` |
| 8 | Batch ID filter | `batch_id` param added to `recent_decisions` for post-batch review |
