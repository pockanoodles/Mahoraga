"""
execution_gate.py — live "verifiable reward" gate for code-producing buckets.

For code/test/refactor/debug tasks, model output that doesn't even execute
(syntax error, unresolved import, crash on load) is broken regardless of how
structured it looks — and the heuristic quality scorer can't tell (it rewards
structure/length, not correctness; see brain/state/findings.md Era 9). This
gate runs the extracted code and, if it fails, lets the serving path mark the
outcome failed so the bandit stops rewarding non-running code.

It is a *conservative* gate: organic traffic has no gold tests, so this only
catches "does not run", NOT "runs but is wrong". Wrong-but-runnable code still
passes — the full correctness signal only exists in the offline benchmark
(`orch bench report verify`).

SECURITY: this executes model-generated code in a subprocess with a short
timeout — the same posture as tools/code_exec.py, but on every code-bucket
task rather than only when a tool is explicitly invoked. Single-user local use
is the intended context. Disable with MAHORAGA_EXEC_GATE=off.
"""
from __future__ import annotations

import ast
import asyncio
import os

from ..workers.postprocess import extract_code

# Buckets whose outputs are code and therefore executable. Mirrors
# quality.CODE_LIKE_BUCKETS ∪ DEBUG_BUCKETS.
EXEC_GATE_BUCKETS: frozenset[str] = frozenset({"code", "test", "refactor", "debug"})

_TIMEOUT_SECONDS = 8  # short: this runs inline in the request path


def exec_gate_enabled() -> bool:
    """On by default; MAHORAGA_EXEC_GATE=off|0|false|no disables it."""
    return os.getenv("MAHORAGA_EXEC_GATE", "on").strip().lower() not in ("0", "off", "false", "no")


async def check_executes(output: str, timeout: int = _TIMEOUT_SECONDS) -> tuple[bool, str | None]:
    """Return (ran_ok, error). Extracts code from `output`, rejects it if it
    doesn't parse, otherwise runs it under python3 and reports whether it exited
    cleanly. `error` is a short reason string on failure, None on success.
    """
    code = extract_code(output).strip()
    if not code:
        return False, "no code produced"

    # Parse first — fail fast on syntax errors without spawning a process, and
    # avoid handing obviously-malformed text to the interpreter.
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc.msg}"

    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:  # pragma: no cover - spawn failure is environmental
        return False, f"spawn failed: {exc}"

    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return False, f"timeout after {timeout}s"

    if proc.returncode == 0:
        return True, None
    tail = (err.decode(errors="replace").strip().splitlines() or ["nonzero exit"])[-1]
    return False, tail[:200]
