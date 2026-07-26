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
    proc.wait = AsyncMock(return_value=returncode)
    return proc


def _sent_prompt(proc: MagicMock) -> str:
    """Prompt text the worker wrote to the CLI's stdin."""
    return proc.communicate.call_args.kwargs["input"].decode()


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


async def test_zero_reported_cost_falls_back_to_computed_cost():
    """Some CLI versions report total_cost_usd: 0.0 under subscription auth —
    a zero with real token counts must not zero the whole feature."""
    result = _cli_result(total_cost_usd=0.0, modelUsage={})
    result["usage"] = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
        "cache_creation_input_tokens": 34883,
    }
    proc = _make_proc(json.dumps(result).encode())
    with _patch_exec(proc):
        w = ClaudeCliWorker(model="claude-sonnet-4-6")
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    m = events[0].payload
    expected = calculate_cost("claude-sonnet-4-6", 1000, 500, 200, 34883)
    assert m["cost_usd"] == pytest.approx(expected)
    assert m["cost_usd"] > 0.0


async def test_fallback_cost_includes_cache_creation_tokens():
    """Cache creation (~35K tokens) dominates a CLI call's cost — the fallback
    must bill it, not ignore it."""
    result = _cli_result(modelUsage={})
    del result["total_cost_usd"]
    result["usage"] = {
        "input_tokens": 2,
        "output_tokens": 4,
        "cache_creation_input_tokens": 34883,
    }
    proc = _make_proc(json.dumps(result).encode())
    with _patch_exec(proc):
        w = ClaudeCliWorker(model="claude-sonnet-4-6")
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    m = events[0].payload
    without_cache = calculate_cost("claude-sonnet-4-6", 2, 4)
    assert m["cost_usd"] == pytest.approx(calculate_cost("claude-sonnet-4-6", 2, 4, 0, 34883))
    assert m["cost_usd"] > without_cache


async def test_malformed_usage_does_not_crash_task():
    """A malformed usage field must not crash a task whose money was already
    spent — telemetry degrades to zeros and the task still completes."""
    result = _cli_result(usage={"input_tokens": ["not", "an", "int"]})
    proc = _make_proc(json.dumps(result).encode())
    with _patch_exec(proc):
        w = ClaudeCliWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    assert [e.type for e in events] == ["metrics", "attempt.completed"]
    m = events[0].payload
    assert m["cost_usd"] == 0.0
    assert m["tokens"] == 0
    assert m["model"] == "claude-sonnet-4-6"


# ── command construction ──────────────────────────────────────────────────────

async def test_execute_passes_prompt_via_stdin_json_format_and_model():
    captured: dict = {}
    proc = _make_proc(json.dumps(_cli_result()).encode())

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        captured["stdin"] = kwargs.get("stdin")
        return proc

    with patch("backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
               side_effect=fake_exec):
        w = ClaudeCliWorker(model="claude-opus-4-6")
        task = make_task()
        _ = [ev async for ev in w.execute(make_attempt(), task)]

    cmd = captured["cmd"]
    assert cmd[1] == "-p"
    assert "--output-format" in cmd and "json" in cmd
    assert "--model" in cmd and "claude-opus-4-6" in cmd
    # Prompt goes over stdin, never argv (injection / ps-leak / ARG_MAX)
    assert captured["stdin"] == asyncio.subprocess.PIPE
    assert not any(task.goal in str(part) for part in cmd)
    assert task.goal in _sent_prompt(proc)


async def test_execute_disallows_tools_by_default():
    captured: dict = {}

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _make_proc(json.dumps(_cli_result()).encode())

    with patch("backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
               side_effect=fake_exec):
        w = ClaudeCliWorker()
        _ = [ev async for ev in w.execute(make_attempt(), make_task())]

    cmd = list(captured["cmd"])
    assert "--disallowedTools" in cmd
    disallowed = cmd[cmd.index("--disallowedTools") + 1]
    for tool in ("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"):
        assert tool in disallowed


async def test_empty_disallowed_tools_omits_flag():
    captured: dict = {}

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return _make_proc(json.dumps(_cli_result()).encode())

    with patch("backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
               side_effect=fake_exec):
        w = ClaudeCliWorker(disallowed_tools="")
        _ = [ev async for ev in w.execute(make_attempt(), make_task())]

    assert "--disallowedTools" not in captured["cmd"]


async def test_default_cwd_is_dedicated_empty_dir(tmp_path):
    captured: dict = {}
    dedicated = tmp_path / "claude-cli-cwd"

    async def fake_exec(*cmd, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return _make_proc(json.dumps(_cli_result()).encode())

    with (
        patch("backend.orchestrator.workers.claude_cli._DEFAULT_CWD", dedicated),
        patch("backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
              side_effect=fake_exec),
    ):
        w = ClaudeCliWorker()
        _ = [ev async for ev in w.execute(make_attempt(), make_task())]

    assert captured["cwd"] == str(dedicated)
    assert dedicated.is_dir()  # created lazily at spawn


async def test_explicit_cwd_overrides_default(tmp_path):
    captured: dict = {}

    async def fake_exec(*cmd, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return _make_proc(json.dumps(_cli_result()).encode())

    with patch("backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
               side_effect=fake_exec):
        w = ClaudeCliWorker(cwd=str(tmp_path))
        _ = [ev async for ev in w.execute(make_attempt(), make_task())]

    assert captured["cwd"] == str(tmp_path)


async def test_execute_strips_auth_env_keeps_oauth_token(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-dummy")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-dummy")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-legit")
    captured: dict = {}

    async def fake_exec(*cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return _make_proc(json.dumps(_cli_result()).encode())

    with patch("backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
               side_effect=fake_exec):
        w = ClaudeCliWorker()
        _ = [ev async for ev in w.execute(make_attempt(), make_task())]

    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in captured["env"]
    assert "ANTHROPIC_BASE_URL" not in captured["env"]
    assert captured["env"].get("CLAUDE_CODE_OAUTH_TOKEN") == "oauth-legit"


# ── retry with feedback ───────────────────────────────────────────────────────

async def test_retry_rebuilds_prompt_with_prior_output_and_feedback():
    procs: list[MagicMock] = []

    async def fake_exec(*cmd, **kwargs):
        proc = _make_proc(json.dumps(_cli_result(result="first output")).encode())
        procs.append(proc)
        return proc

    with patch("backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
               side_effect=fake_exec):
        w = ClaudeCliWorker()
        task = make_task()
        _ = [ev async for ev in w.execute(make_attempt(), task)]
        _ = [ev async for ev in w.execute(make_attempt(), task, feedback="Missing X, add Y")]

    prompts = [_sent_prompt(p) for p in procs]
    assert "first output" not in prompts[0]
    assert "first output" in prompts[1]
    assert "Missing X, add Y" in prompts[1]


async def test_clear_history_resets_retry_context():
    procs: list[MagicMock] = []

    async def fake_exec(*cmd, **kwargs):
        proc = _make_proc(json.dumps(_cli_result(result="prior output")).encode())
        procs.append(proc)
        return proc

    with patch("backend.orchestrator.workers.claude_cli.asyncio.create_subprocess_exec",
               side_effect=fake_exec):
        w = ClaudeCliWorker()
        task = make_task()
        _ = [ev async for ev in w.execute(make_attempt(), task)]
        w.clear_history(task.id)
        _ = [ev async for ev in w.execute(make_attempt(), task, feedback="ignored prior")]

    assert "prior output" not in _sent_prompt(procs[1])


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
    proc.wait = AsyncMock(return_value=-9)

    async def slow_communicate(input=None):
        await asyncio.sleep(5)

    proc.communicate = slow_communicate
    with _patch_exec(proc):
        w = ClaudeCliWorker(timeout=0.05)
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert failed[0].payload["error_code"] == "timeout"
    proc.kill.assert_called_once()
    proc.wait.assert_awaited()  # reaped, not left as a zombie


async def test_spawn_oserror_yields_spawn_error():
    with _patch_exec(OSError(7, "Argument list too long")):
        w = ClaudeCliWorker()
        events = [ev async for ev in w.execute(make_attempt(), make_task())]

    failed = [e for e in events if e.type == "attempt.failed"]
    assert len(failed) == 1
    assert failed[0].payload["error_code"] == "spawn_error"


async def test_cancel_kills_tracked_proc():
    started = asyncio.Event()
    release = asyncio.Event()

    proc = MagicMock()
    proc.returncode = None

    async def communicate(input=None):
        started.set()
        await release.wait()
        return b"", b"killed"

    def kill():
        proc.returncode = -9
        release.set()

    proc.communicate = communicate
    proc.kill = MagicMock(side_effect=kill)
    proc.wait = AsyncMock(return_value=-9)

    with _patch_exec(proc):
        w = ClaudeCliWorker(timeout=5)
        attempt = make_attempt()

        async def consume():
            return [ev async for ev in w.execute(attempt, make_task())]

        runner = asyncio.create_task(consume())
        await started.wait()
        await w.cancel(attempt.id)
        events = await runner

    proc.kill.assert_called_once()
    failed = [e for e in events if e.type == "attempt.failed"]
    assert failed[0].payload["error_code"] == "nonzero_exit"
    # proc tracking cleaned up after the attempt finished
    assert attempt.id not in w._procs


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
