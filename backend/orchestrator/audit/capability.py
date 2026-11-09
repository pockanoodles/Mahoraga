"""Whether local has caught up with a cloud tier on a row of the map, by
capability rather than price (ADR brain/decisions/2026-11-08-caught-up-by-capability.md).

The gap is the cloud's resolve rate minus local's on the same tasks, with its
95% interval (stats.paired_difference). A row reads one of four standings
against a margin fixed before any run:

    behind     the whole interval is above the margin
    open       the interval straddles the margin
    caught up  the whole interval is below the margin
    ahead      the whole interval is below zero

"Not significantly worse" would have been simpler, and wrong: it rewards a
small row, since a handful of tasks can't show any gap. Here a row has to show
that the gap is small.
"""
from __future__ import annotations

from backend.orchestrator.agentbench.stats import paired_difference

# Ten percentage points, for rows graded pass or fail. Fixed for the life of
# the map; changing it takes a new ADR. Sonnet 5.5 trails Opus 5.5 by 8.5
# points on the 47 published tasks, so this is about how close the cloud's own
# tiers are to each other.
MARGIN = 0.10

STANDINGS = ("behind", "open", "caught up", "ahead")


def standing(lo: float, hi: float, margin: float = MARGIN) -> str:
    """The standing of a gap (cloud minus local) whose interval is [lo, hi]."""
    if hi < 0:
        return "ahead"
    if hi < margin:
        return "caught up"
    if lo > margin:
        return "behind"
    return "open"


def capability(only_local: int, only_cloud: int, pairs: int,
               margin: float = MARGIN) -> dict[str, float | str]:
    """The gap, its interval and the standing, from a paired comparison."""
    gap, lo, hi = paired_difference(only_cloud, only_local, pairs)
    return {"gap": gap, "gap_lo": lo, "gap_hi": hi, "margin": margin,
            "standing": standing(lo, hi, margin)}
