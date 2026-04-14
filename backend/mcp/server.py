"""
Mahoraga MCP Server — stdio bridge to Mahoraga's orchestration API.

Usage:
    python -m backend.mcp.server

Claude Code config (~/.claude/settings.json):
    {
        "mcpServers": {
            "mahoraga": {
                "command": "python",
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
_NOT_RUNNING = (
    "Mahoraga is not running. "
    "Start it with: cd ~/Projects/Mahoraga && python -m backend.main"
)

server = Server("mahoraga")


async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(base_url=MAHORAGA_BASE, timeout=TIMEOUT) as client:
        try:
            resp = await client.post(path, json=body)
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            return {"error": _NOT_RUNNING}
        except httpx.HTTPStatusError as e:
            return {"error": f"Mahoraga {e.response.status_code}: {e.response.text[:200]}"}


async def _get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(base_url=MAHORAGA_BASE, timeout=TIMEOUT) as client:
        try:
            resp = await client.get(path, params=params or {})
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            return {"error": _NOT_RUNNING}
        except httpx.HTTPStatusError as e:
            return {"error": f"Mahoraga {e.response.status_code}: {e.response.text[:200]}"}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="run_task",
            description=(
                "Route and execute a single task through Mahoraga. The bandit selects the best "
                "available agent based on task type and learned performance. Returns the agent's "
                "output, routing decision, and execution metadata."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "capability_hint": {
                        "type": "string",
                        "enum": ["code", "plan", "general"],
                        "description": "Optional. Override keyword classification.",
                    },
                    "agent_override": {
                        "type": "string",
                        "description": "Optional. Force a specific agent (e.g. 'aider', 'codex-cli').",
                    },
                },
                "required": ["prompt"],
            },
        ),
        Tool(
            name="run_batch",
            description=(
                "Execute multiple tasks concurrently through Mahoraga. Tasks are routed through "
                "the bandit, grouped into resource-aware execution waves, and run in parallel where "
                "safe. Supports dependency chains via depends_on (0-indexed). Returns all results "
                "in a single response."
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
                                },
                                "expected_files": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "default": [],
                                },
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
                    },
                },
                "required": ["tasks"],
            },
        ),
        Tool(
            name="route_task",
            description=(
                "Dry-run: show which agent Mahoraga would select for a task without executing it. "
                "Returns keyword classification and UCB scores for all candidate agents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                },
                "required": ["prompt"],
            },
        ),
        Tool(
            name="agent_status",
            description=(
                "Show the status of all registered agents: online/offline, model info, "
                "resource group, and current queue depth."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="routing_stats",
            description=(
                "Get aggregate routing statistics: selection counts, reward distributions, "
                "and strategy performance."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "enum": ["1h", "24h", "7d", "30d", "all"],
                        "default": "24h",
                    }
                },
                "required": [],
            },
        ),
        Tool(
            name="switch_strategy",
            description="Switch Mahoraga's routing strategy at runtime (linucb, ucb1, thompson, static).",
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
            description="Retrieve recent routing decisions from Mahoraga's decision log.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "agent_filter": {"type": "string"},
                    "capability_filter": {
                        "type": "string",
                        "enum": ["code", "plan", "general"],
                    },
                },
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handlers = {
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


async def _handle_run_task(args: dict) -> dict:
    body = {"prompt": args["prompt"]}
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


async def _handle_recent_decisions(args: dict) -> dict:
    params: dict = {"limit": args.get("limit", 10)}
    if "agent_filter" in args:
        params["agent"] = args["agent_filter"]
    if "capability_filter" in args:
        params["capability"] = args["capability_filter"]
    return await _get("/api/routing/decisions", params)


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
