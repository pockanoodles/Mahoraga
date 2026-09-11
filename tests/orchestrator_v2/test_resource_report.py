"""Tests for the resource observability reader (routing/resource_report.py)
and sampler (routing/resource_sampler.py).

This is not a memory-pressure metric — high RAM while local models are warm
is expected for this workload. The property that matters is that the report
reduces raw CPU/thermal samples to a single plain-language read (fine /
strained / critical) that a non-sysadmin can act on without interpreting
percentages, and that the sampler can never break `orch serve`.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.orchestrator.routing.resource_report import (
    HIGH_CPU_PERCENT,
    compute_resource_report,
    render_resource_report,
)
from backend.orchestrator.routing.resource_sampler import (
    _thermal_level,
    sample_once,
    sampler_loop,
)


def _log(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "resource.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def _sample(cpu=10.0, thermal="nominal", ts="2026-08-17T10:00:00+00:00", warm=None):
    return {
        "ts": ts, "cpu_percent": cpu, "thermal_level": thermal,
        "warm_models": warm or [],
    }


# ── the reader ────────────────────────────────────────────────────────────────


def test_missing_log_is_an_empty_report_not_an_error(tmp_path):
    r = compute_resource_report(tmp_path / "nope.jsonl")
    assert r.samples == 0
    assert r.level == "unknown"
    assert "No resource samples recorded yet" in render_resource_report(r)


def test_torn_line_does_not_discard_the_rest(tmp_path):
    p = tmp_path / "resource.jsonl"
    p.write_text(json.dumps(_sample()) + "\n{ broken\n" + json.dumps(_sample()) + "\n")
    r = compute_resource_report(p)
    assert r.samples == 2


def test_low_cpu_and_nominal_thermal_is_fine(tmp_path):
    log = _log(tmp_path, [_sample(cpu=10.0), _sample(cpu=20.0), _sample(cpu=15.0)])
    r = compute_resource_report(log)
    assert r.level == "fine"
    assert r.elevated_thermal_samples == 0


def test_sustained_high_cpu_is_strained(tmp_path):
    """A single spike shouldn't tip the read; a sustained share should."""
    rows = [_sample(cpu=95.0)] * 3 + [_sample(cpu=10.0)] * 7
    r = compute_resource_report(_log(tmp_path, rows))
    assert r.high_cpu_samples == 3
    assert r.high_cpu_share == pytest.approx(0.3)
    assert r.level == "strained"


def test_a_single_spike_alone_is_still_fine(tmp_path):
    rows = [_sample(cpu=95.0)] + [_sample(cpu=10.0)] * 9
    r = compute_resource_report(_log(tmp_path, rows))
    assert r.level == "fine"


def test_any_elevated_thermal_sample_is_critical_even_with_low_cpu(tmp_path):
    """macOS only ever records a thermal warning under real pressure, so one
    occurrence outranks the CPU-share heuristic entirely."""
    rows = [_sample(cpu=5.0)] * 9 + [_sample(cpu=5.0, thermal="elevated")]
    r = compute_resource_report(_log(tmp_path, rows))
    assert r.level == "critical"


def test_high_cpu_threshold_boundary(tmp_path):
    log = _log(tmp_path, [_sample(cpu=HIGH_CPU_PERCENT)])
    r = compute_resource_report(log)
    assert r.high_cpu_samples == 1


def test_window_filters_by_date(tmp_path):
    log = _log(tmp_path, [
        _sample(ts="2026-08-01T10:00:00+00:00"),
        _sample(ts="2026-08-10T10:00:00+00:00"),
        _sample(ts="2026-08-20T10:00:00+00:00"),
    ])
    assert compute_resource_report(log, since="2026-08-05").samples == 2
    assert compute_resource_report(log, until="2026-08-10").samples == 2


def test_warm_models_are_counted(tmp_path):
    log = _log(tmp_path, [
        _sample(warm=["qwen3.5"]),
        _sample(warm=["qwen3.5", "granite4.1-8b"]),
    ])
    r = compute_resource_report(log)
    assert r.warm_model_counts == {"qwen3.5": 2, "granite4.1-8b": 1}


def test_render_does_not_expose_raw_percent_without_the_level_line(tmp_path):
    """The headline must be the plain-language read, not just a number."""
    text = render_resource_report(compute_resource_report(_log(tmp_path, [_sample()])))
    assert "Machine state" in text
    assert "fine" in text


def test_render_states_the_log_only_scope(tmp_path):
    text = render_resource_report(compute_resource_report(_log(tmp_path, [_sample()])))
    assert "Log-only" in text
    assert "does not" not in text  # sanity: no stray placeholder text


# ── the sampler: it must never break serving ─────────────────────────────────


def test_thermal_level_reports_unknown_on_missing_pmset(monkeypatch):
    import subprocess

    def _raise(*a, **kw):
        raise FileNotFoundError("no pmset on this platform")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert _thermal_level() == "unknown"


class _Proc:
    """Stand-in for the CompletedProcess `pmset -g therm` returns."""

    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def _pmset(monkeypatch, stdout: str, returncode: int = 0) -> None:
    import subprocess

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _Proc(stdout, returncode)
    )


def test_thermal_level_reads_the_idle_line_as_nominal(monkeypatch):
    """The absence of a warning level is the healthy signal, and macOS states
    it as a sentence rather than a value — so this parse is the whole read."""
    _pmset(monkeypatch, "Note: No thermal warning level has been recorded\n")
    assert _thermal_level() == "nominal"


def test_thermal_level_reads_a_recorded_warning_as_elevated(monkeypatch):
    """Under real pressure the idle sentence is replaced by limit values; any
    other output therefore means a warning level exists."""
    _pmset(monkeypatch, "CPU_Speed_Limit \t= 80\nCPU_Available_CPUs \t= 8\n")
    assert _thermal_level() == "elevated"


def test_thermal_level_does_not_read_a_failed_pmset_as_elevated(monkeypatch):
    """A non-zero exit prints nothing useful; calling that 'elevated' would
    report the machine as critical on every non-macOS host."""
    _pmset(monkeypatch, "", returncode=1)
    assert _thermal_level() == "unknown"


async def test_sampler_loop_sleeps_the_full_interval_before_each_sample(
    monkeypatch, tmp_path
):
    """The first `cpu_percent(interval=None)` reading is meaningless, so the
    loop must sleep first and sample second — one sample per interval, none at
    t=0."""
    import backend.orchestrator.routing.resource_sampler as rs

    slept: list[float] = []
    sampled: list[Path] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) >= 3:
            raise asyncio.CancelledError

    async def fake_sample_once(base_url, log_path=rs.DEFAULT_LOG):
        sampled.append(log_path)

    monkeypatch.setattr(rs.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(rs, "sample_once", fake_sample_once)

    log = tmp_path / "resource.jsonl"
    await rs.sampler_loop("http://localhost:11434", 17.5, log_path=log)

    assert slept == [17.5, 17.5, 17.5]
    assert sampled == [log, log]  # two sleeps completed, two samples


async def test_sampler_loop_returns_cleanly_on_cancellation(tmp_path):
    """`orch serve`'s shutdown cancels this task; it must end without raising
    so a dangling CancelledError can't surface as a shutdown error."""
    task = asyncio.create_task(
        sampler_loop("http://localhost:1", 3600.0, tmp_path / "resource.jsonl")
    )
    await asyncio.sleep(0)  # let the loop reach its first await
    task.cancel()
    await task  # must not raise
    assert task.done()


async def test_sample_once_never_raises_when_ollama_is_unreachable(tmp_path):
    log = tmp_path / "resource.jsonl"
    # Port 1 is not Ollama; the sampler must swallow the connection failure.
    await sample_once("http://localhost:1", log_path=log)
    assert log.is_file()
    row = json.loads(log.read_text().strip())
    assert row["warm_models"] == []
