import pytest
from backend.orchestrator.rankings.aggregator import wilson_interval, build_rankings_rows


def test_wilson_interval_majority_success():
    lo, hi = wilson_interval(80, 100)
    assert lo > 0.70
    assert hi < 1.0
    assert lo < hi


def test_wilson_interval_zero_samples():
    lo, hi = wilson_interval(0, 0)
    assert lo == 0.0
    assert hi == 0.0


def test_wilson_interval_all_fail():
    lo, hi = wilson_interval(0, 50)
    assert lo == pytest.approx(0.0, abs=0.01)
    assert hi < 0.10


def test_build_rankings_rows_sorts_by_reward():
    metrics = [
        {"agent": "a", "sample_count": 30, "success_count": 25,
         "mean_reward": 0.85, "median_latency_ms": 1200.0},
        {"agent": "b", "sample_count": 40, "success_count": 28,
         "mean_reward": 0.70, "median_latency_ms": 900.0},
        {"agent": "c", "sample_count": 10, "success_count": 9,
         "mean_reward": 0.90, "median_latency_ms": 2000.0},
    ]
    rows = build_rankings_rows(metrics)
    assert rows[0]["agent"] == "c"   # highest reward
    assert rows[1]["agent"] == "a"
    assert rows[2]["agent"] == "b"
    assert rows[0]["rank"] == 1
    assert "ci_low" in rows[0]
    assert "ci_high" in rows[0]
