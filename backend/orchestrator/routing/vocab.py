# backend/orchestrator/routing/vocab.py
"""
Single source of truth for routing identifiers.

Any string used as a bucket name, agent ID, or capability tag MUST be
defined here and imported from here. No module outside this file should
contain string literals that equal any value in BUCKETS or ENABLED_AGENTS.

Bucket groupings (CODE_LIKE_BUCKETS etc.) are also defined here so
quality.py, reward.py, and other consumers import them rather than
redeclaring the same strings.
"""
from __future__ import annotations

BUCKETS: tuple[str, ...] = (
    "code",
    "debug",
    "plan",
    "research",
    "review",
    "refactor",
    "security",
    "test",
    "general",
)

ENABLED_AGENTS: tuple[str, ...] = (
    "ollama:qwen3.5",
    "ollama:granite4.1-8b",
)

DISABLED_AGENTS: tuple[str, ...] = (
    "ollama:gemma4-e4b",
    "ollama:lfm2",
    "ollama:deepseek-r1",
    "claude",
    "codex-cli",
    "gemini-cli",
    "aider",
    "goose",
    "opencode",
)

# CAPABILITY_TAGS may include values not in BUCKETS (e.g. "explain") — intentional.
# Capability tags govern adapter compatibility checks (router.py _capable());
# bucket names govern routing and scoring. They are not required to be identical sets.
CAPABILITY_TAGS: tuple[str, ...] = (
    "code", "debug", "plan", "research", "review",
    "refactor", "security", "test", "general", "explain",
)

# ── Bucket groupings ───────────────────────────────────────────────────────────
# Consumed by quality.py, reward.py. Defined here to avoid duplicating
# bucket-name string literals across modules.

# Buckets whose output is primarily code; structural/syntax checks apply.
CODE_LIKE_BUCKETS: frozenset[str] = frozenset({"code", "test", "refactor"})

# Buckets whose output is a diagnostic/fix; hybrid code+prose scoring.
DEBUG_BUCKETS: frozenset[str] = frozenset({"debug"})

# Buckets whose output discusses vulnerabilities, mitigations, threat models.
SECURITY_BUCKETS: frozenset[str] = frozenset({"security"})

# Buckets whose output is a structured plan; no code-syntax penalty.
PLAN_LIKE_BUCKETS: frozenset[str] = frozenset({"plan"})
