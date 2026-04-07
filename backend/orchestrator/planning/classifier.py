from __future__ import annotations

TIER3_KEYWORDS = frozenset({
    "architect", "architecture", "migrate", "migration",
    "security", "optimize", "refactor", "redesign", "system design",
})

_COMPLEX_SIGNALS = frozenset({
    "component", "module", "service", "endpoint", "database",
    "authentication", "integration", "workflow", "pipeline",
})


def classify_tier(title: str, goal: str) -> int:
    """Classify a mission into a complexity tier using keyword heuristics.

    Returns:
        1 — Simple. Single worker call, minimal prompt.
        2 — Medium. Single worker call, full context.
        3 — Complex. Run Haiku planner for decomposition.
    """
    text = f"{title} {goal}".lower()
    words = text.split()

    # Tier 3: explicitly complex keywords
    if any(kw in text for kw in TIER3_KEYWORDS):
        return 3

    # Tier 3: long goal or complex signals on a sizeable task
    if len(words) > 60:
        return 3
    if any(sig in text for sig in _COMPLEX_SIGNALS) and len(words) > 30:
        return 3

    # Tier 2: moderate complexity
    if len(words) > 20 or any(sig in text for sig in _COMPLEX_SIGNALS):
        return 2

    return 1
