"""Tests for the funnel's tool-liveness signal.

The delegation funnel's first real reading was **0.0% over 170 delegable
actions**, and it was worthless: the MCP server had never started, because
`~/.claude.json` pointed it at a bare `python` that does not exist on this
machine. Four weeks of "you never delegate" was four weeks of a dead
interpreter path.

The property under test is that those two states are now distinguishable. A
rate with no liveness evidence behind it must refuse to be read as a
behavioural finding, and a rate *with* liveness evidence must be allowed to say
what it means.
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.mcp.funnel_liveness import ALIVE_EVENT as WRITER_EVENT
from backend.mcp.funnel_liveness import LOG_PATH as WRITER_LOG
from backend.mcp.funnel_liveness import record_alive
from backend.orchestrator.routing.funnel_report import (
    ALIVE_EVENT as READER_EVENT,
)
from backend.orchestrator.routing.funnel_report import (
    DEFAULT_LOG as READER_LOG,
)
from backend.orchestrator.routing.funnel_report import compute_funnel, render_funnel


ALIVE_EVENT_NAME = "mcp-alive"


def _log(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "funnel.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def _inline(candidate=True, reason="", ts="2026-09-11T10:00:00+00:00"):
    row = {"ts": ts, "session": "s1", "event": "inline", "candidate": candidate}
    if reason:
        row["reason"] = reason
    return row


def _delegated(ts="2026-09-11T10:00:00+00:00"):
    return {"ts": ts, "session": "s1", "event": "delegated"}


def _alive(ts="2026-09-11T09:00:00+00:00", tools=9):
    return {"ts": ts, "session": "", "event": ALIVE_EVENT_NAME, "tools": tools}


# ── the duplication must not drift ───────────────────────────────────────────


def test_writer_and_reader_agree_on_the_event_name_and_log_path():
    """The writer deliberately does not import the orchestrator package, so the
    event name and log path exist in two files. If they drift, liveness rows are
    written and silently never counted — which is the original failure mode
    wearing a different hat."""
    assert WRITER_EVENT == READER_EVENT == ALIVE_EVENT_NAME
    assert WRITER_LOG == READER_LOG


# ── the three states ─────────────────────────────────────────────────────────


def test_no_delegable_work_is_not_a_zero_rate(tmp_path):
    log = _log(tmp_path, [_inline(candidate=False, reason="edit-in-place")])
    r = compute_funnel(log)
    assert r.delegation_rate is None
    assert r.interpretation == "no-delegable-work"


def test_delegable_work_with_no_liveness_is_uninterpretable(tmp_path):
    """The 2026-09-10 case: plenty of delegable work, zero delegations, and no
    evidence the tool ever existed."""
    log = _log(tmp_path, [_inline() for _ in range(5)])
    r = compute_funnel(log)
    assert r.delegation_rate == 0.0
    assert r.interpretation == "tool-liveness-unknown"
    text = render_funnel(r)
    assert "NOT READABLE" in text
    assert "0.0%" in text  # the number is still shown, just not endorsed


def test_liveness_plus_zero_delegations_is_a_real_finding(tmp_path):
    log = _log(tmp_path, [_alive()] + [_inline() for _ in range(5)])
    r = compute_funnel(log)
    assert r.mcp_alive == 1
    assert r.delegation_rate == 0.0
    assert r.interpretation == "tool-available-unused"
    text = render_funnel(r)
    assert "NOT READABLE" not in text
    assert "means what it" in text


def test_liveness_plus_delegations_is_measured(tmp_path):
    log = _log(tmp_path, [_alive(), _delegated()] + [_inline() for _ in range(3)])
    r = compute_funnel(log)
    assert r.interpretation == "measured"
    assert r.delegation_rate == 0.25
    assert "NOT READABLE" not in render_funnel(r)


# ── liveness must not touch either half of the ratio ─────────────────────────


def test_liveness_rows_change_no_counter_but_the_interpretation(tmp_path):
    rows = [_delegated()] + [_inline() for _ in range(3)]
    without = compute_funnel(_log(tmp_path, rows))
    with_alive = compute_funnel(_log(tmp_path, rows + [_alive() for _ in range(20)]))
    for attr in ("delegated", "candidates", "inline_total", "delegable"):
        assert getattr(without, attr) == getattr(with_alive, attr)
    assert without.delegation_rate == with_alive.delegation_rate
    assert without.interpretation != with_alive.interpretation


def test_liveness_rows_do_not_inflate_the_session_count(tmp_path):
    """Liveness rows carry an empty session, so they must not be counted as
    sessions — otherwise the per-session denominator drifts upward every time
    an editor restarts."""
    log = _log(tmp_path, [_delegated(), _alive(), _alive()])
    assert compute_funnel(log).sessions == 1


def test_last_alive_ts_tracks_the_most_recent_observation(tmp_path):
    log = _log(tmp_path, [
        _alive(ts="2026-09-11T08:00:00+00:00"),
        _alive(ts="2026-09-11T12:00:00+00:00"),
        _alive(ts="2026-09-11T10:00:00+00:00"),
    ])
    assert compute_funnel(log).last_alive_ts == "2026-09-11T12:00:00+00:00"


def test_window_filter_applies_to_liveness_too(tmp_path):
    """A tool that was alive last month says nothing about this month."""
    log = _log(tmp_path, [
        _alive(ts="2026-08-01T10:00:00+00:00"),
        _inline(ts="2026-09-11T10:00:00+00:00"),
    ])
    r = compute_funnel(log, since="2026-09-01")
    assert r.mcp_alive == 0
    assert r.interpretation == "tool-liveness-unknown"


def test_interpretation_is_in_the_json_payload(tmp_path):
    log = _log(tmp_path, [_alive(), _delegated(), _inline()])
    d = compute_funnel(log).to_dict()
    assert d["interpretation"] == "measured"
    assert d["mcp_alive_observations"] == 1
    assert d["last_alive_ts"]


# ── the writer ───────────────────────────────────────────────────────────────


def test_record_alive_writes_a_row_the_reader_counts(tmp_path):
    log = tmp_path / "funnel.jsonl"
    record_alive(9, log_path=log)
    assert compute_funnel(log).mcp_alive == 1
    row = json.loads(log.read_text().strip())
    assert row["event"] == ALIVE_EVENT_NAME
    assert row["tools"] == 9
    assert row["session"] == ""  # must not create a phantom session


def test_record_alive_never_raises_on_an_unwritable_path(tmp_path):
    """This runs on the tool-discovery path. A liveness probe that can break
    tool discovery is worse than no liveness probe."""
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory")
    record_alive(9, log_path=blocked / "nested" / "funnel.jsonl")  # must not raise


def test_record_alive_can_be_disabled(tmp_path, monkeypatch):
    log = tmp_path / "funnel.jsonl"
    monkeypatch.setenv("MAHORAGA_FUNNEL_LIVENESS", "off")
    record_alive(9, log_path=log)
    assert not log.exists()
