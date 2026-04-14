"""
Resource group registry for Mahoraga's wave executor.

A resource group represents a physical backend shared by one or more agents.
The wave executor enforces max_concurrent per group to prevent overloading
shared infrastructure (e.g., the single local GPU serving both ollama and aider).

To add a second Ollama instance: split local_ollama into local_ollama_0 and
local_ollama_1, reassign agents. No executor changes needed.
"""

RESOURCE_GROUPS: dict[str, dict] = {
    "local_ollama": {
        "agents": ["ollama", "aider"],
        "max_concurrent": 1,
        "description": "Single local GPU, shared Ollama server",
    },
    "openai_api": {
        "agents": ["codex-cli"],
        "max_concurrent": 2,
        "description": "OpenAI cloud API, rate-limited",
    },
    "google_api": {
        "agents": ["gemini-cli"],
        "max_concurrent": 3,
        "description": "Google cloud API, rate-limited",
    },
    "anthropic_api": {
        "agents": ["claude"],
        "max_concurrent": 2,
        "description": "Anthropic cloud API, rate-limited",
    },
    "unknown": {
        "agents": ["goose", "opencode"],
        "max_concurrent": 1,
        "description": "Conservative default for uncharacterized agents",
    },
}


def get_resource_group(agent_name: str) -> str:
    """Return the resource group an agent belongs to."""
    for group_name, group in RESOURCE_GROUPS.items():
        if agent_name in group["agents"]:
            return group_name
    return "unknown"


def get_group_concurrency(group_name: str) -> int:
    """Return the max concurrent tasks for a resource group."""
    return RESOURCE_GROUPS.get(group_name, RESOURCE_GROUPS["unknown"])["max_concurrent"]
