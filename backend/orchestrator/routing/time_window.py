"""time_window.py — one definition of the inclusive `--since`/`--until` window
the JSONL-backed reports share.

Both `funnel_report` and `resource_report` read append-only JSONL logs whose
`ts` field is a lexicographically-sortable ISO-8601 string, so windowing is a
string comparison rather than date parsing. The subtlety worth having in one
place: `--until 2026-09-10` means "through the end of that day", but the rows
are full timestamps (`2026-09-10T14:22:01+00:00`) which sort *after* the bare
date. Comparing only the prefix `until` actually specifies is what makes a
partial bound inclusive, at whatever precision the caller gave.
"""
from __future__ import annotations

from typing import Optional


def within_window(
    ts: str,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> bool:
    """Is `ts` inside the inclusive [since, until] window?

    Both bounds are optional and may be given at any precision — a date, or a
    full timestamp. An empty `ts` (a row with no usable timestamp) is only
    excluded when a bound is actually set.
    """
    if since and ts < since:
        return False
    if until and ts[: len(until)] > until:
        return False
    return True
