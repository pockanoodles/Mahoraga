"""Tests for routing/time_window.py — the inclusive [since, until] window the
JSONL-backed reports (`metrics funnel`, `metrics resource`) share.

The property under test is that a bound given at *date* precision includes the
whole day, even though the rows carry full timestamps that sort after the bare
date. Both callers previously appended a U+FFFF sentinel to fake that; this
module does it by comparing only the prefix the bound actually specifies.
"""
from __future__ import annotations

from backend.orchestrator.routing.time_window import within_window

TS = "2026-09-10T14:22:01+00:00"


def test_no_bounds_admits_everything():
    assert within_window(TS)
    assert within_window("")


def test_since_is_inclusive_at_date_precision():
    assert within_window(TS, since="2026-09-10")
    assert within_window(TS, since="2026-09-01")
    assert not within_window(TS, since="2026-09-11")


def test_until_at_date_precision_includes_the_whole_day():
    """The regression this module exists for: 'until 2026-09-10' must keep a
    14:22 row on that date, not drop it for sorting after the bare date."""
    assert within_window(TS, until="2026-09-10")
    assert not within_window(TS, until="2026-09-09")


def test_until_at_full_timestamp_precision_is_still_inclusive():
    assert within_window(TS, until=TS)
    assert not within_window(TS, until="2026-09-10T14:22:00+00:00")


def test_a_closed_window_admits_only_its_interior():
    assert within_window(TS, since="2026-09-01", until="2026-09-30")
    assert not within_window(TS, since="2026-09-11", until="2026-09-30")
    assert not within_window(TS, since="2026-08-01", until="2026-08-31")


def test_an_empty_timestamp_is_excluded_only_when_a_bound_is_set():
    assert within_window("")
    assert not within_window("", since="2026-09-01")
