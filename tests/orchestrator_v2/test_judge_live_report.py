"""Tests for the live judge-gate operating-point report (routing/judge_live_report.py)
and the judge_gate_events persistence it reads.
"""
import sqlite3

import pytest

from backend.orchestrator.routing.decision_log import DecisionLogger
from backend.orchestrator.routing.judge_live_report import (
    ERA14_ESCALATION_RATE,
    ERA14_NEEDLESS_RATE,
    Cell,
    as_dict,
    load_events,
    summarize,
)


def _row(**over) -> dict:
    base = dict(
        id=1, timestamp="2026-08-01T10:00:00+00:00", task_id="t1", bucket="code",
        judged_agent="ollama:granite4.1-8b", judge_worker_id="ollama:qwen3.5:general",
        verdict=1, escalated=0, served_fallback=0,
        final_agent="ollama:granite4.1-8b:coder", judge_ms=800.0, reason="judge: correct",
    )
    base.update(over)
    return base


# ── persistence ──────────────────────────────────────────────────────────────

@pytest.fixture
def logger(tmp_path):
    lg = DecisionLogger(db_path=tmp_path / "d.db")
    yield lg
    lg.close()


def test_log_judge_gate_roundtrips(logger, tmp_path):
    logger.log_judge_gate(
        task_id="task-1", bucket="general", judged_agent="ollama:qwen3.5",
        judge_worker_id="ollama:qwen3.5:general", verdict=False, escalated=True,
        served_fallback=False, final_agent="claude-cli:sonnet", judge_ms=1234.5,
        reason="judge: incorrect",
    )
    rows = load_events(tmp_path / "d.db")
    assert len(rows) == 1
    assert rows[0]["bucket"] == "general"
    assert rows[0]["verdict"] == 0
    assert rows[0]["escalated"] == 1
    assert rows[0]["judge_ms"] == pytest.approx(1234.5)


def test_log_judge_gate_stores_unparseable_verdict_as_null(logger, tmp_path):
    """None must not collapse to False — the report counts them separately."""
    logger.log_judge_gate(
        task_id="task-1", bucket="code", judged_agent="a", judge_worker_id="j",
        verdict=None, escalated=True, served_fallback=False, final_agent="b",
        judge_ms=None, reason="unparseable",
    )
    rows = load_events(tmp_path / "d.db")
    assert rows[0]["verdict"] is None
    assert rows[0]["judge_ms"] is None


def test_load_events_on_missing_db(tmp_path):
    assert load_events(tmp_path / "nope.db") == []


def test_load_events_on_db_without_the_table(tmp_path):
    """A DB predating the gate is the normal case, not an error."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE decisions (id INTEGER)")
    conn.commit()
    conn.close()
    assert load_events(path) == []


def test_load_events_filters(logger, tmp_path):
    for bucket, ts in (("code", "2026-08-01T00:00:00"), ("general", "2026-08-05T00:00:00")):
        logger.log_judge_gate(
            task_id="t", bucket=bucket, judged_agent="a", judge_worker_id="j",
            verdict=True, escalated=False, served_fallback=False, final_agent="a",
            judge_ms=1.0, reason="",
        )
    assert len(load_events(tmp_path / "d.db", bucket="code")) == 1
    assert len(load_events(tmp_path / "d.db")) == 2


# ── aggregation ──────────────────────────────────────────────────────────────

def test_empty_cell_rates_are_zero_not_nan():
    c = Cell()
    assert c.escalation_rate == 0.0
    assert c.fallback_rate == 0.0
    assert c.mean_judge_ms == 0.0
    assert c.p90_judge_ms() == 0.0


def test_escalation_rate():
    rows = [_row(verdict=1, escalated=0) for _ in range(8)]
    rows += [_row(verdict=0, escalated=1) for _ in range(2)]
    s = summarize(rows)
    assert s.overall.judged == 10
    assert s.overall.escalated == 2
    assert s.overall.escalation_rate == pytest.approx(0.2)


def test_verdict_classes_are_kept_apart():
    """correct / incorrect / unparseable / abstained are four different things."""
    rows = [
        _row(verdict=1, escalated=0),                      # correct
        _row(verdict=0, escalated=1),                      # incorrect
        _row(verdict=None, escalated=1),                   # unparseable → escalated
        _row(verdict=None, escalated=0),                   # judge errored → abstained
    ]
    o = summarize(rows).overall
    assert (o.verdict_correct, o.verdict_incorrect) == (1, 1)
    assert (o.verdict_unparseable, o.abstained) == (1, 1)
    assert o.escalated == 2


def test_abstain_is_not_counted_as_a_correct_vote():
    """A dead judge abstains; scoring that as 'correct' would hide an outage."""
    o = summarize([_row(verdict=None, escalated=0) for _ in range(5)]).overall
    assert o.verdict_correct == 0
    assert o.abstained == 5
    assert o.escalation_rate == 0.0


def test_fallback_rate_is_over_escalations_not_tasks():
    rows = [_row(verdict=1, escalated=0) for _ in range(6)]
    rows += [_row(verdict=0, escalated=1, served_fallback=0) for _ in range(3)]
    rows += [_row(verdict=0, escalated=1, served_fallback=1)]
    o = summarize(rows).overall
    assert o.escalated == 4
    assert o.served_fallback == 1
    assert o.fallback_rate == pytest.approx(0.25)


def test_per_bucket_split():
    rows = [_row(bucket="code", verdict=0, escalated=1) for _ in range(2)]
    rows += [_row(bucket="code", verdict=1, escalated=0) for _ in range(2)]
    rows += [_row(bucket="general", verdict=1, escalated=0) for _ in range(4)]
    s = summarize(rows)
    assert s.per_bucket["code"].escalation_rate == pytest.approx(0.5)
    assert s.per_bucket["general"].escalation_rate == 0.0


def test_per_agent_split():
    rows = [_row(judged_agent="a", verdict=0, escalated=1)]
    rows += [_row(judged_agent="b", verdict=1, escalated=0)]
    s = summarize(rows)
    assert s.per_agent["a"].escalated == 1
    assert s.per_agent["b"].escalated == 0


def test_latency_mean_and_p90():
    rows = [_row(judge_ms=float(x)) for x in (100, 200, 300, 400, 1000)]
    o = summarize(rows).overall
    assert o.mean_judge_ms == pytest.approx(400.0)
    assert o.p90_judge_ms() == pytest.approx(1000.0)


def test_missing_latency_is_skipped_not_zeroed():
    """A NULL judge_ms must not drag the mean toward zero."""
    rows = [_row(judge_ms=None), _row(judge_ms=500.0)]
    assert summarize(rows).overall.mean_judge_ms == pytest.approx(500.0)


def test_window_bounds():
    rows = [
        _row(timestamp="2026-08-01T10:00:00+00:00"),
        _row(timestamp="2026-08-03T10:00:00+00:00"),
    ]
    s = summarize(rows)
    assert s.first_seen.startswith("2026-08-01")
    assert s.last_seen.startswith("2026-08-03")


def test_missing_bucket_becomes_a_placeholder_not_a_crash():
    s = summarize([_row(bucket="")])
    assert "?" in s.per_bucket


# ── the Era-14 comparison ────────────────────────────────────────────────────

def test_delta_is_zero_when_live_matches_the_bank():
    rows = [_row(verdict=1, escalated=0) for _ in range(40)]
    rows += [_row(verdict=0, escalated=1) for _ in range(10)]
    assert summarize(rows).escalation_delta == pytest.approx(0.0)


def test_delta_positive_when_escalating_more_than_the_bank():
    rows = [_row(verdict=0, escalated=1) for _ in range(10)]
    assert summarize(rows).escalation_delta > 0


def test_delta_negative_when_escalating_less_than_the_bank():
    rows = [_row(verdict=1, escalated=0) for _ in range(10)]
    assert summarize(rows).escalation_delta == pytest.approx(-ERA14_ESCALATION_RATE)


def test_as_dict_carries_the_baseline_and_the_no_oracle_caveat():
    d = as_dict(summarize([_row()]))
    assert d["baseline"]["escalation_rate"] == pytest.approx(ERA14_ESCALATION_RATE)
    assert d["baseline"]["needless_rate"] == pytest.approx(ERA14_NEEDLESS_RATE)
    # The report must never be mistaken for an accuracy measurement.
    assert "ground truth" in d["caveat"]
    assert "accuracy" not in d["overall"]


def test_as_dict_is_json_serializable():
    import json
    json.dumps(as_dict(summarize([_row(), _row(bucket="general", judge_ms=None)])))
