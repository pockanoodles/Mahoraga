from __future__ import annotations

# Only these explicitly complex keywords warrant full decomposition
TIER3_KEYWORDS = frozenset({
    "architect", "architecture", "migrate", "migration",
    "security audit", "optimize", "refactor", "redesign", "system design",
})


def classify_tier(title: str, goal: str) -> int:
    """Classify a mission into a complexity tier using keyword heuristics.

    Returns:
        1 — Simple/Medium. Single worker call, no decomposition.
        3 — Complex. Run planner for decomposition.

    Fix #4: any task under 60 words without explicit TIER3_KEYWORDS gets
    tier=1 and goes straight to a single worker call. This prevents
    medium-complexity tasks (e.g. "Write a REST API endpoint in Express.js")
    from being over-decomposed into trivial subtasks.
    """
    text = f"{title} {goal}".lower()
    words = text.split()

    # Tier 3: explicitly complex multi-step keywords
    if any(kw in text for kw in TIER3_KEYWORDS):
        return 3

    # Tier 3: genuinely long / multi-requirement goals
    if len(words) > 60:
        return 3

    # Everything else: single worker call
    return 1
