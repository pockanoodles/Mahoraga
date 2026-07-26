"""Tests for ClaudeCliWorker — subprocess-based Claude Code CLI arm."""
from __future__ import annotations
import asyncio
import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.orchestrator.domain.models import Task, TaskAttempt
from backend.orchestrator.tracking.pricing import calculate_cost
from backend.orchestrator.workers.claude_cli import ClaudeCliWorker


def make_task(**kwargs) -> Task:
    t = Task.new(run_id="r1", title="Fix auth", goal="Fix the login bug")
    return dataclasses.replace(t, **kwargs) if kwargs else t


def make_attempt(worker_id: str = "claude-cli:sonnet") -> TaskAttempt:
    return TaskAttempt.new(task_id="t1", worker_id=worker_id)


def _cli_result(**overrides) -> dict:
    """Real CLI output shape (claude -p ... --output-format json)."""
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 1,
        "session_id": "ae08d59d-0000-0000-0000-000000000000",
        "result": "OK — here is the fix",
        "total_cost_usd": 0.209364,
        "duration_ms": 1637,
        "usage": {
            "input_tokens": 2,
            "output_tokens": 4,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 34883,
        },
        "modelUsage": {"claude-sonnet-5": {"inputTokens": 2, "outputTokens": 4, "costUSD": 0.209364}},
    }
    result.update(overrides)
    return result


def _make_proc(stdout: bytes, returncode: int = 0, stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


def _patch_exec(proc_or_side_effect):
    if isinstance(proc_or_side_effect, Exception):
        return patch(
            "backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
            side_effect=proc_or_side_effect,
        )
    return patch(
        "backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc_or_side_effect),
    )


# ── identity ──────────────────────────────────────────────────────────────────

def test_worker_id():
    w = ClaudeCliWorker()
    assert w.id == "claude-cli:sonnet"


def test_default_capabilities():
    w = ClaudeCliWorker()
    assert "code" in w.capabilities
    assert "general" in w.capabilities


# ── happy path ────────────────────────────────────────────────────────────────

async def test_execute_yields_metrics_then_completed():
    proc = _make_proc(json.dumps(_cli_result()).encode())
    with _patch_exec(proc):
        w = ClaudeCliWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    assert [e.type for e in events] == ["metrics", "attempt.completed"]
    assert "here is the fix" in events[1].payload["summary"]


async def test_metrics_payload_carries_cli_cost_and_tokens():
    proc = _make_proc(json.dumps(_cli_result()).encode())
    with _patch_exec(proc):
        w = ClaudeCliWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    m = events[0].payload
    assert m["cost_usd"] == pytest.approx(0.209364)
    assert m["tokens"] == 4
    assert m["prompt_tokens"] == 2
    assert m["cache_read_tokens"] == 10
    assert m["cache_creation_tokens"] == 34883
    # Model name resolved from modelUsage (CLI expands aliases)
    assert m["model"] == "claude-sonnet-5"
    assert m["elapsed_s"] == pytest.approx(1.64, abs=0.01)


async def test_missing_cost_computed_from_usage():
    result = _cli_result(modelUsage={})
    del result["total_cost_usd"]
    result["usage"] = {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 200}
    proc = _make_proc(json.dumps(result).encode())
    with _patch_exec(proc):
        w = ClaudeCliWorker(model="claude-sonnet-4-6")
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    m = events[0].payload
    expected = calculate_cost("claude-sonnet-4-6", 1000, 500, 200)
    assert m["cost_usd"] == pytest.approx(expected)
    assert m["model"] == "claude-sonnet-4-6"


async def test_missing_usage_and_cost_falls_back_to_zero():
    result = _cli_result(modelUsage={})
    del result["total_cost_usd"]
    del result["usage"]
    proc = _make_proc(json.dumps(result).encode())
    with _patch_exec(proc):
        w = ClaudeCliWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    completed = [e for e in events if e.type == "attempt.completed"]
    assert len(completed) == 1
    m = events[0].payload
    assert m["cost_usd"] == 0.0
    assert m["tokens"] == 0


# ── command construction ──────────────────────────────────────────────────────

async def test_execute_passes_prompt_json_format_and_model():
    captured: dict = {}

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return _make_proc(json.dumps(_cli_result()).encode())

    with patch("backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
               side_effect=fake_exec):
        w = ClaudeCliWorker(model="claude-opus-4-6")
        task = make_task()
        _ = [ev async for ev in w.execute(make_attempt(), task)]

    cmd = captured["cmd"]
    assert cmd[1] == "-p"
    assert task.goal in cmd[2]
    assert "--output-format" in cmd and "json" in cmd
    assert "--model" in cmd and "claude-opus-4-6" in cmd


async def test_execute_strips_api_key_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-dummy")
    captured: dict = {}

    async def fake_exec(*cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return _make_proc(json.dumps(_cli_result()).encode())

    with patch("backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
               side_effect=fake_exec):
        w = ClaudeCliWorker()
        _ = [ev async for ev in w.execute(make_attempt(), make_task())]

    assert "ANTHROPIC_API_KEY" not in captured["env"]


# ── retry with feedback ───────────────────────────────────────────────────────

async def test_retry_rebuilds_prompt_with_prior_output_and_feedback():
    prompts: list[str] = []

    async def fake_exec(*cmd, **kwargs):
        prompts.append(cmd[2])
        return _make_proc(json.dumps(_cli_result(result="first output")).encode())

    with patch("backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
               side_effect=fake_exec):
        w = ClaudeCliWorker()
        task = make_task()
        _ = [ev async for ev in w.execute(make_attempt(), task)]
        _ = [ev async for ev in w.execute(make_attempt(), task, feedback="Missing X, add Y")]

    assert "first output" not in prompts[0]
    assert "first output" in prompts[1]
    assert "Missing X, add Y" in prompts[1]


async def test_clear_history_resets_retry_context():
    prompts: list[str] = []

    async def fake_exec(*cmd, **kwargs):
        prompts.append(cmd[2])
        return _make_proc(json.dumps(_cli_result(result="prior output")).encode())

    with patch("backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
               side_effect=fake_exec):
        w = ClaudeCliWorker()
        task = make_task()
        _ = [ev async for ev in w.execute(make_attempt(), task)]
        w.clear_history(task.id)
        _ = [ev async for ev in w.execute(make_attempt(), task, feedback="ignored prior")]

    assert "prior output" not in prompts[1]


# ── error paths ───────────────────────────────────────────────────────────────

async def test_binary_not_found():
    with _patch_exec(FileNotFoundError("no claude")):
        w = ClaudeCliWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].payload["error_code"] == "binary_not_found"


async def test_timeout_kills_process():
    proc = MagicMock()
    proc.kill = MagicMock()

    async def slow_communicate():
        await asyncio.sleep(5)

    proc.communicate = slow_communicate
    with _patch_exec(proc):
        w = ClaudeCliWorker(timeout=0.05)
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert failed[0].payload["error_code"] == "timeout"
    proc.kill.assert_called_once()


async def test_nonzero_exit():
    proc = _make_proc(b"", returncode=1, stderr=b"auth failure")
    with _patch_exec(proc):
        w = ClaudeCliWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert failed[0].payload["error_code"] == "nonzero_exit"
    assert "auth failure" in failed[0].payload["error"]


async def test_malformed_json():
    proc = _make_proc(b"not json at all")
    with _patch_exec(proc):
        w = ClaudeCliWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert failed[0].payload["error_code"] == "malformed_json"


async def test_cli_error_result():
    result = _cli_result(is_error=True, subtype="error_during_execution")
    proc = _make_proc(json.dumps(result).encode())
    with _patch_exec(proc):
        w = ClaudeCliWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert failed[0].payload["error_code"] == "cli_error"


async def test_empty_result():
    proc = _make_proc(json.dumps(_cli_result(result="  ")).encode())
    with _patch_exec(proc):
        w = ClaudeCliWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert failed[0].payload["error_code"] == "empty_response"


# ── health ────────────────────────────────────────────────────────────────────

async def test_health_installed():
    with patch("backend.orchestrator.workers.claude_cli.shutil.which",
               return_value="/usr/local/bin/claude"):
        w = ClaudeCliWorker()
        h = await w.health()
    assert h.healthy is True
    assert h.worker_id == "claude-cli:sonnet"


async def test_health_not_installed():
    with patch("backend.orchestrator.workers.claude_cli.shutil.which", return_value=None):
        w = ClaudeCliWorker()
        h = await w.health()
    assert h.healthy is False
    assert "claude" in h.detail.lower()
