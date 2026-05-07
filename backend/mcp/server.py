"""
Mahoraga MCP Server — stdio bridge to Mahoraga's orchestration API.

Usage:
    python -m backend.mcp.server

Claude Code config (~/.claude/settings.json):
    {
        "mcpServers": {
            "mahoraga": {
                "command": "/Users/kaitosoeno/Projects/Mahoraga/.venv/bin/python",
                "args": ["-m", "backend.mcp.server"],
                "cwd": "/Users/kaitosoeno/Projects/Mahoraga"
            }
        }
    }
"""
from __future__ import annotations
import asyncio
import json
import os

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

MAHORAGA_BASE = os.environ.get("MAHORAGA_BASE", "http://localhost:8000")
TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)
# R1.1 — bumped from 1 retry to 2 with longer second backoff per spec
# (1s → 3s) so transient hiccups during model warmup or cloud-API flakes
# don't surface as user-visible failures. Total worst-case added latency
# is ~4s before the MCP returns an error and Claude falls back inline.
_MAX_RETRIES = int(os.environ.get("MAHORAGA_MCP_RETRIES", "2"))
_RETRY_DELAYS = [1.0, 3.0]
_NOT_RUNNING = (
    "Mahoraga is not running. "
    "Start it with: cd ~/Projects/Mahoraga && python -m backend.main"
)


def _retry_delay(attempt: int) -> float:
    """Pick the backoff for retry attempt N (0-indexed)."""
    if attempt < len(_RETRY_DELAYS):
        return _RETRY_DELAYS[attempt]
    return _RETRY_DELAYS[-1]

server = Server("mahoraga")


async def _post(path: str, body: dict) -> dict:
    for attempt in range(_MAX_RETRIES + 1):
        async with httpx.AsyncClient(base_url=MAHORAGA_BASE, timeout=TIMEOUT) as client:
            try:
                resp = await client.post(path, json=body)
                resp.raise_for_status()
                return resp.json()
            except httpx.ConnectError:
                return {"error": _NOT_RUNNING}
            except httpx.ReadTimeout:
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                return {
                    "error": f"Mahoraga timed out after {TIMEOUT.read}s.",
                    "suggestion": "Try again, or use a faster agent with agent_override.",
                }
            except httpx.HTTPStatusError as e:
                return {"error": f"Mahoraga {e.response.status_code}: {e.response.text[:200]}"}
    return {"error": "Unexpected retry exhaustion"}


async def _get(path: str, params: dict | None = None) -> dict:
    for attempt in range(_MAX_RETRIES + 1):
        async with httpx.AsyncClient(base_url=MAHORAGA_BASE, timeout=TIMEOUT) as client:
            try:
                resp = await client.get(path, params=params or {})
                resp.raise_for_status()
                return resp.json()
            except httpx.ConnectError:
                return {"error": _NOT_RUNNING}
            except httpx.ReadTimeout:
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                return {
                    "error": f"Mahoraga timed out after {TIMEOUT.read}s.",
                    "suggestion": "Try again.",
                }
            except httpx.HTTPStatusError as e:
                return {"error": f"Mahoraga {e.response.status_code}: {e.response.text[:200]}"}
    return {"error": "Unexpected retry exhaustion"}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="health_check",
            description=(
                "Check if Mahoraga is running and responsive. Returns status, uptime, version, "
                "and number of registered agents. Use this before running tasks to verify the "
                "backend is available."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="run_task",
            description=(
                "Send a task to Mahoraga for execution. Mahoraga automatically picks the best "
                "available AI agent for the job based on the task type — code tasks go to coding "
                "agents, research tasks go to research agents. Use this for tasks like creating "
                "files, refactoring code, writing tests, running shell commands, or researching a "
                "topic. Returns the result, which agent handled it, and how well it performed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "What you want done."},
                    "cwd": {
                        "type": "string",
                        "description": "Optional. Working directory for the agent. Defaults to Mahoraga's configured project directory.",
                    },
                    "capability_hint": {
                        "type": "string",
                        "enum": ["code", "plan", "general"],
                        "description": "Optional. Override automatic task classification.",
                    },
                    "agent_override": {
                        "type": "string",
                        "description": "Optional. Force a specific agent. e.g. 'aider', 'ollama', 'codex-cli'.",
                    },
                },
                "required": ["prompt"],
            },
        ),
        Tool(
            name="run_batch",
            description=(
                "Send multiple tasks to Mahoraga at once. Tasks run in parallel where safe — "
                "Mahoraga groups them into waves based on which agents share hardware and which "
                "files overlap. Use this when you have 3+ independent subtasks. Returns all "
                "results together with timing comparison vs sequential execution."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string"},
                                "depends_on": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "default": [],
                                    "description": "Indices of tasks that must complete first.",
                                },
                                "expected_files": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "default": [],
                                    "description": "File paths this task will write. Tasks with overlapping files run sequentially.",
                                },
                                "cwd": {"type": "string"},
                                "capability_hint": {
                                    "type": "string",
                                    "enum": ["code", "plan", "general"],
                                },
                            },
                            "required": ["prompt"],
                        },
                        "minItems": 1,
                        "maxItems": 10,
                    },
                    "parallel": {"type": "boolean", "default": True},
                    "max_concurrent": {
                        "type": "integer",
                        "default": 2,
                        "minimum": 1,
                        "maximum": 5,
                        "description": "Maximum tasks running simultaneously. Raise only if tasks hit different backends.",
                    },
                },
                "required": ["tasks"],
            },
        ),
        Tool(
            name="route_task",
            description=(
                "Preview which agent Mahoraga would pick for a task without actually running it. "
                "Shows task classification and all candidate agents with their scores. Use this "
                "to understand routing behavior before committing."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "The task to classify and route."},
                },
                "required": ["prompt"],
            },
        ),
        Tool(
            name="agent_status",
            description=(
                "Show all registered AI agents, whether they're online, what model they run, "
                "and how busy they are. Use this to check what's available before sending tasks."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="routing_stats",
            description=(
                "Get performance statistics for Mahoraga's routing: how often each agent is "
                "selected, average reward scores, success rates, and whether the system is "
                "improving over time."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "enum": ["1h", "24h", "7d", "30d", "all"],
                        "default": "24h",
                        "description": "Time window for statistics.",
                    }
                },
                "required": [],
            },
        ),
        Tool(
            name="switch_strategy",
            description=(
                "Change how Mahoraga picks agents. 'linucb' learns which agent is best per task "
                "type (recommended). 'ucb1' is simpler. 'thompson' explores more. 'static' always "
                "picks the same agent. Changes take effect immediately."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "strategy": {
                        "type": "string",
                        "enum": ["linucb", "ucb1", "thompson", "static"],
                    }
                },
                "required": ["strategy"],
            },
        ),
        Tool(
            name="recent_decisions",
            description=(
                "See Mahoraga's recent routing decisions: what tasks were sent, which agent "
                "handled each one, how well it performed, and how long it took. Use this to "
                "review batch results or debug routing issues."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "agent_filter": {
                        "type": "string",
                        "description": "Only show decisions for this agent.",
                    },
                    "capability_filter": {
                        "type": "string",
                        "enum": ["code", "plan", "general"],
                    },
                    "batch_id": {
                        "type": "string",
                        "description": "Only show decisions from a specific batch. Use the batch_id from a run_batch response.",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="switch_routing_mode",
            description=(
                "Set Mahoraga's routing mode preference. 'local_first' restricts routing to free "
                "agents (ollama, aider, gemini-cli) when any are available — good for budget-conscious "
                "use. 'balanced' lets the bandit decide based on composite reward (default). "
                "'quality_first' routes to the highest-reward agent regardless of cost. "
                "Changes take effect on the next routing decision."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["local_first", "balanced", "quality_first"],
                        "description": "The routing mode to activate.",
                    }
                },
                "required": ["mode"],
            },
        ),
    ]


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
        "switch_routing_mode": _handle_switch_routing_mode,
    }
    handler = handlers.get(name)
    if not handler:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    result = await handler(arguments)
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_health_check(args: dict) -> dict:
    """R1.1 health_check upgrade — returns the degradation-ladder snapshot.

    Composes three endpoints:
      /api/health           — basic uptime + agents online
      /api/health/routing   — F1.4 routing snapshot (budget, queue, etc.)
      /api/agents/status    — per-agent reachability

    Skill consumers can use any of:
      - `result["status"]` — overall verdict ("ok" / "degraded" / "down")
      - `result["degradation_level"]` — discrete ladder rung (0=ok, 1=agent
        drift, 2=local down, 3=fastapi down, 4=all down)
      - `result["routing_health"]` — full F1.4 snapshot
      - `result["error"]` (only on outright failure)

    On any single endpoint failure, return a degraded-but-useful
    response rather than propagating the error — the skill should
    still see SOME context for its delegation decision.
    """
    base = await _get("/api/health")
    if isinstance(base, dict) and "error" in base:
        # FastAPI is the worst-case down state — nothing else can be
        # meaningfully checked.
        return {
            "status": "down",
            "degradation_level": 3,
            "level_name": "fastapi_unreachable",
            **base,
        }

    routing = await _get("/api/health/routing")
    agents = await _get("/api/agents/status")
    routing_ok = isinstance(routing, dict) and "error" not in routing
    agents_ok = isinstance(agents, dict) and "error" not in agents

    # Derive degradation level from the composed signals.
    quarantined: list[str] = []
    drift_unresolved = 0
    budget_avg = None
    budget_ceiling = None
    queue_depth_norm = None
    if routing_ok:
        q = routing.get("quarantine") or {}
        quarantined = [
            f"{e['bucket']}/{e['agent']}" for e in (q.get("entries") or [])
        ]
        drift_unresolved = int(q.get("n_drift_events_unresolved", 0))
        bp = routing.get("budget_pacer") or {}
        budget_avg = bp.get("avg_cost")
        budget_ceiling = bp.get("ceiling")
        ep = routing.get("execution_pool") or {}
        queue_depth_norm = ep.get("depth_norm")

    agents_online = int(base.get("agents_online", 0))
    agents_total = int(base.get("agents_registered", 0))

    if agents_total == 0 or (agents_total > 0 and agents_online == 0):
        level = 4
        level_name = "all_agents_down"
        status = "down"
    elif agents_online < agents_total:
        # At least one agent unreachable. Distinguish "local down"
        # (worst) from generic degradation by checking if any
        # local-Ollama agent is online.
        level = 2
        level_name = "agents_partially_down"
        status = "degraded"
    elif quarantined:
        level = 1
        level_name = "agent_drift"
        status = "degraded"
    else:
        level = 0
        level_name = "ok"
        status = "ok"

    # Spread base FIRST so derived fields win on key collisions.
    # /api/health returns its own `status` ("ok") but we want our
    # composed `status` ("degraded" when partially down) to be the
    # one consumers see. Same for `agents_online`/`agents_registered`.
    return {
        # Carry the original /api/health fields verbatim for callers
        # that already consumed them — uptime_s, strategy, etc.
        **base,
        # Derived fields (overriding any collisions with base).
        "status": status,
        "degradation_level": level,
        "level_name": level_name,
        "agents_online": agents_online,
        "agents_total": agents_total,
        "quarantined_agents": quarantined,
        "drift_alerts_active": drift_unresolved,
        "budget_avg_cost": budget_avg,
        "budget_ceiling": budget_ceiling,
        "queue_depth_norm": queue_depth_norm,
        # Embed the full routing snapshot for callers that want richer
        # detail; cheap to include since we just fetched it.
        "routing_health": routing if routing_ok else None,
    }


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
    agents = await _get("/api/agents/status")
    if isinstance(agents, dict) and "error" in agents:
        return agents
    groups = await _get("/api/resource-groups")
    return {"agents": agents, "resource_groups": groups}


async def _handle_routing_stats(args: dict) -> dict:
    return await _get("/api/routing/stats", {"window": args.get("window", "24h")})


async def _handle_switch_strategy(args: dict) -> dict:
    return await _post("/api/routing/strategy", {"strategy": args["strategy"]})


async def _handle_switch_routing_mode(args: dict) -> dict:
    return await _post("/api/routing/mode", {"mode": args["mode"]})


async def _handle_recent_decisions(args: dict) -> dict:
    params: dict = {"limit": args.get("limit", 10)}
    if "agent_filter" in args:
        params["agent"] = args["agent_filter"]
    if "capability_filter" in args:
        params["capability"] = args["capability_filter"]
    if "batch_id" in args:
        params["batch_id"] = args["batch_id"]
    return await _get("/api/routing/decisions", params)


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
