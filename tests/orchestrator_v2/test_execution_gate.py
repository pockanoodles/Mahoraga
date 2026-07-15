"""Tests for the live verifiable-reward execution gate (execution_gate.py)."""
from __future__ import annotations

import pytest

from backend.orchestrator.routing.execution_gate import (
    EXEC_GATE_BUCKETS,
    check_executes,
    exec_gate_enabled,
)


def test_gate_buckets_are_code_producing():
    assert EXEC_GATE_BUCKETS == {"code", "test", "refactor", "debug"}


def test_exec_gate_enabled_default_on(monkeypatch):
    monkeypatch.delenv("MAHORAGA_EXEC_GATE", raising=False)
    assert exec_gate_enabled() is True


@pytest.mark.parametrize("val", ["off", "0", "false", "NO", "Off"])
def test_exec_gate_disabled_by_env(monkeypatch, val):
    monkeypatch.setenv("MAHORAGA_EXEC_GATE", val)
    assert exec_gate_enabled() is False


async def test_check_executes_clean_code():
    ran, err = await check_executes("def add(a, b):\n    return a + b\n\nassert add(1, 2) == 3")
    assert ran is True
    assert err is None


async def test_check_executes_fenced_code():
    out = "```python\ndef f():\n    return 1\nprint(f())\n```"
    ran, err = await check_executes(out)
    assert ran is True


async def test_check_executes_syntax_error():
    ran, err = await check_executes("def broken(:\n    return 1")
    assert ran is False
    assert "SyntaxError" in err


async def test_check_executes_runtime_error():
    ran, err = await check_executes("print(undefined_name)")
    assert ran is False
    assert "NameError" in err


async def test_check_executes_bad_import():
    ran, err = await check_executes("import a_package_that_does_not_exist_xyz")
    assert ran is False
    assert "Error" in err  # ModuleNotFoundError / ImportError


async def test_check_executes_empty_output():
    ran, err = await check_executes("")
    assert ran is False
    assert err == "no code produced"


async def test_check_executes_timeout():
    ran, err = await check_executes("import time\ntime.sleep(5)", timeout=1)
    assert ran is False
    assert "timeout" in err
