"""Tests for backend.orchestrator.tracking — pricing and ledger."""
from __future__ import annotations

import time

import aiosqlite
import pytest

from backend.orchestrator.tracking import CostLedger, calculate_cost, format_cost


# ---------------------------------------------------------------------------
# pricing.py
# ---------------------------------------------------------------------------

def test_calculate_cost_haiku():
    cost = calculate_cost("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=500)
    expected = (1000 / 1_000_000) * 1.00 + (500 / 1_000_000) * 5.00
    assert cost == pytest.approx(expected, abs=1e-6)


def test_calculate_cost_sonnet():
    cost = calculate_cost("claude-sonnet-4-6", input_tokens=2000, output_tokens=1000)
    expected = (2000 / 1_000_000) * 3.00 + (1000 / 1_000_000) * 15.00
    assert cost == pytest.approx(expected, abs=1e-6)


def test_calculate_cost_opus():
    cost = calculate_cost("claude-opus-4-6", input_tokens=1000, output_tokens=1000)
    expected = (1000 / 1_000_000) * 5.00 + (1000 / 1_000_000) * 25.00
    assert cost == pytest.approx(expected, abs=1e-6)


def test_calculate_cost_cache_read():
    cost = calculate_cost(
        "claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=2000,
    )
    expected = (
        (1000 / 1_000_000) * 3.00
        + (500 / 1_000_000) * 15.00
        + (2000 / 1_000_000) * 0.30
    )
    assert cost == pytest.approx(expected, abs=1e-6)


def test_calculate_cost_unknown_model_falls_back_to_sonnet():
    cost_unknown = calculate_cost("unknown-model", input_tokens=1000, output_tokens=500)
    cost_sonnet = calculate_cost("claude-sonnet-4-6", input_tokens=1000, output_tokens=500)
    assert cost_unknown == cost_sonnet


def test_calculate_cost_dated_id_resolves_by_prefix():
    """Dated IDs like claude-sonnet-5-20260203 must price at claude-sonnet-5
    rates, not silently fall back."""
    dated = calculate_cost("claude-sonnet-5-20260203", input_tokens=1000, output_tokens=500)
    base = calculate_cost("claude-sonnet-5", input_tokens=1000, output_tokens=500)
    assert dated == base

    dated_opus = calculate_cost("claude-opus-4-8-20260401", input_tokens=1000, output_tokens=500)
    base_opus = calculate_cost("claude-opus-4-8", input_tokens=1000, output_tokens=500)
    assert dated_opus == base_opus


def test_calculate_cost_prefix_match_prefers_longest_prefix():
    # claude-haiku-4-5-20251001 is itself a key; a longer dated suffix must
    # match the most specific known prefix, not a shorter one.
    cost = calculate_cost("claude-haiku-4-5-20251001-extra", input_tokens=1_000_000, output_tokens=0)
    assert cost == pytest.approx(1.00, abs=1e-6)


def test_calculate_cost_unknown_model_warns(caplog):
    import logging as _logging
    with caplog.at_level(_logging.WARNING, logger="backend.orchestrator.tracking.pricing"):
        calculate_cost("gpt-9-mega", input_tokens=100, output_tokens=100)
    assert any("unknown model" in r.message for r in caplog.records)


def test_calculate_cost_known_prefix_does_not_warn(caplog):
    import logging as _logging
    with caplog.at_level(_logging.WARNING, logger="backend.orchestrator.tracking.pricing"):
        calculate_cost("claude-sonnet-5-20260203", input_tokens=100, output_tokens=100)
    assert not any("unknown model" in r.message for r in caplog.records)


def test_calculate_cost_cache_creation_billed_at_input_premium():
    """Cache writes bill at 1.25× the input rate (5-minute-TTL premium)."""
    cost = calculate_cost(
        "claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=200,
        cache_creation_tokens=34883,
    )
    expected = (
        (1000 / 1_000_000) * 3.00
        + (500 / 1_000_000) * 15.00
        + (200 / 1_000_000) * 0.30
        + (34883 / 1_000_000) * 3.00 * 1.25
    )
    assert cost == pytest.approx(expected, abs=1e-6)


def test_calculate_cost_cache_creation_default_zero():
    """Existing callers (no cache_creation_tokens) are unaffected."""
    with_default = calculate_cost("claude-sonnet-4-6", 1000, 500, 200)
    explicit_zero = calculate_cost("claude-sonnet-4-6", 1000, 500, 200, 0)
    assert with_default == explicit_zero


def test_format_cost():
    result = format_cost(0.003, {"Haiku": 1200, "Sonnet": 3400})
    assert "$0.0030" in result
    assert "Haiku" in result
    assert "1,200 tok" in result


def test_format_cost_no_breakdown():
    result = format_cost(0.0015)
    assert "$0.0015" in result
    assert "(" not in result


def test_format_cost_skips_zero_tokens():
    result = format_cost(0.005, {"Haiku": 0, "Sonnet": 500})
    assert "Haiku" not in result
    assert "Sonnet" in result


# ---------------------------------------------------------------------------
# ledger.py — async fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def ledger(tmp_path):
    db_path = str(tmp_path / "test_ledger.db")
    async with aiosqlite.connect(db_path) as conn:
        lg = CostLedger(conn)
        await lg.migrate()
        yield lg


@pytest.mark.asyncio
async def test_ledger_record_and_total(ledger):
    await ledger.record(
        user_id="user-1",
        mission_id="mission-a",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cost_usd=0.0105,
    )
    await ledger.record(
        user_id="user-1",
        mission_id="mission-b",
        model="claude-haiku-4-5-20251001",
        input_tokens=2000,
        output_tokens=800,
        cache_read_tokens=0,
        cost_usd=0.0048,
    )

    total = await ledger.total_cost("user-1")
    assert total == pytest.approx(0.0153, abs=1e-6)


@pytest.mark.asyncio
async def test_ledger_total_cost_other_user_isolated(ledger):
    await ledger.record(
        user_id="user-1",
        mission_id="mission-a",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cost_usd=0.01,
    )
    total_user2 = await ledger.total_cost("user-2")
    assert total_user2 == 0.0


@pytest.mark.asyncio
async def test_ledger_cost_by_period(ledger):
    before = time.time() - 10  # 10 seconds ago

    await ledger.record(
        user_id="user-1",
        mission_id="mission-a",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cost_usd=0.02,
    )

    cost = await ledger.cost_since("user-1", since=before)
    assert cost == pytest.approx(0.02, abs=1e-6)

    future = time.time() + 3600
    cost_future = await ledger.cost_since("user-1", since=future)
    assert cost_future == 0.0


@pytest.mark.asyncio
async def test_ledger_mission_cost(ledger):
    await ledger.record(
        user_id="user-1",
        mission_id="mission-x",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cost_usd=0.005,
    )
    await ledger.record(
        user_id="user-2",
        mission_id="mission-x",
        model="claude-haiku-4-5-20251001",
        input_tokens=500,
        output_tokens=200,
        cache_read_tokens=0,
        cost_usd=0.001,
    )
    await ledger.record(
        user_id="user-1",
        mission_id="mission-y",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cost_usd=0.009,
    )

    cost_x = await ledger.mission_cost("mission-x")
    assert cost_x == pytest.approx(0.006, abs=1e-6)

    cost_y = await ledger.mission_cost("mission-y")
    assert cost_y == pytest.approx(0.009, abs=1e-6)
