"""ClaudeCliWorker — subprocess-based WorkerAdapter for the Claude Code CLI.

Runs `claude -p --output-format json --model <model>` in non-interactive
print mode, with the prompt written to stdin (never argv: prompts are
user-derived text, so argv would risk option injection for prompts starting
with `-`, leak the prompt to `ps`, and hit ARG_MAX on large prompts). Auth
comes from the local Claude subscription login (`claude login`) — no
ANTHROPIC_API_KEY needed. That variable — plus ANTHROPIC_AUTH_TOKEN and
ANTHROPIC_BASE_URL, which can silently redirect auth/endpoint — is stripped
from the subprocess env so stale .env values can never shadow subscription
auth (CLAUDE_CODE_OAUTH_TOKEN is kept: legitimate subscription auth).

Sandbox: the CLI runs with --disallowedTools "Bash,Edit,Write,..." and a
dedicated empty cwd (~/.mahoraga-v2/claude-cli-cwd) by default. Prompt text
is user-derived; a text-only bench arm must not inherit pre-authorized tools
from a user project directory's CLAUDE.md / .claude/settings.json allowlists.

Retry/feedback: the CLI's `--resume <session_id>` was considered but rejected —
it couples retries to CLI session state on disk, which can be cleared between
attempts. Instead retries rebuild the prompt inline with the prior output and
verifier feedback (same information, stateless, robust).

Telemetry: on success a `metrics` WorkerEvent is emitted with the same payload
field names OllamaWorker uses (`elapsed_s`, `tokens`, `throughput_tps`) plus
`prompt_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `cost_usd`, and
`model` — the executor side-channel carries these to the recording path in
app.py/gateway unchanged. Cost prefers the CLI's authoritative
`total_cost_usd`; falls back to calculate_cost() from token counts, then 0.0.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import AsyncGenerator

from .base import WorkerAdapter, WorkerEvent, WorkerHealth, _build_prompt
from ..domain.models import Task, TaskAttempt
from ..tracking.pricing import calculate_cost

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300  # seconds — cloud call incl. cold session-cache write

# Text-only arm: no shell, no file writes, no web tools. See module docstring.
_DEFAULT_DISALLOWED_TOOLS = "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch"

# Dedicated empty cwd so the CLI never loads a user project's CLAUDE.md or
# .claude/settings.json permission allowlists (created lazily at spawn).
_DEFAULT_CWD = Path.home() / ".mahoraga-v2" / "claude-cli-cwd"

# Env vars that can redirect auth or endpoint — never inherited by the CLI.
_STRIPPED_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")


class ClaudeCliWorker(WorkerAdapter):
    """Cloud arm via the Claude Code CLI (Max subscription, no API key)."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        worker_id: str = "claude-cli:sonnet",
        binary_path: str = "claude",
        timeout: float = _DEFAULT_TIMEOUT,
        cwd: str | None = None,
        capabilities: list[str] | None = None,
        disallowed_tools: str = _DEFAULT_DISALLOWED_TOOLS,
    ) -> None:
        self._model = model
        self._worker_id = worker_id
        self._binary = binary_path
        self._timeout = timeout
        self._cwd = cwd  # None → dedicated empty dir resolved at spawn
        self._capabilities = capabilities or ["general", "code", "plan", "deep_reasoning"]
        self._disallowed_tools = disallowed_tools  # empty string → flag omitted
        # Per-task last output so retries can inline prior attempt + feedback
        self._last_output: dict[str, str] = {}
        # Live subprocesses keyed by attempt.id, so cancel() can kill them
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    @property
    def id(self) -> str:
        return self._worker_id

    @property
    def capabilities(self) -> list[str]:
        return self._capabilities

    async def execute(
        self,
        attempt: TaskAttempt,
        task: Task,
        feedback: str | None = None,
    ) -> AsyncGenerator[WorkerEvent, None]:
        binary = shutil.which(self._binary) or self._binary
        prompt = _build_prompt(task)
        if feedback:
            prior = self._last_output.get(task.id, "")
            if prior:
                prompt += f"\n\n## Previous Attempt Output\n{prior}"
            prompt += f"\n\n## Feedback on Previous Attempt\n{feedback}"

        # Prompt goes over stdin, never argv (option injection / ps leak / ARG_MAX).
        cmd = [binary, "-p", "--output-format", "json", "--model", self._model]
        if self._disallowed_tools:
            cmd += ["--disallowedTools", self._disallowed_tools]
        # Subscription auth only — never let env auth/endpoint overrides shadow it.
        env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV_VARS}

        # Default cwd is a dedicated empty dir: the prompt is user-derived text,
        # and a text-only bench arm must not inherit pre-authorized tools from a
        # project directory's CLAUDE.md / .claude/settings.json allowlists.
        cwd = self._cwd
        if cwd is None:
            _DEFAULT_CWD.mkdir(parents=True, exist_ok=True)
            cwd = str(_DEFAULT_CWD)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except FileNotFoundError:
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "binary_not_found", "error": f"claude binary not found at {self._binary!r}. Install: npm install -g @anthropic-ai/claude-code"},
            )
            return
        except OSError as exc:  # E2BIG / EACCES / ENOEXEC etc.
            yield WorkerEvent(
                type="attempt.failed",
                payload={"error_code": "spawn_error", "error": f"failed to spawn claude: {exc}"},
            )
            return

        self._procs[attempt.id] = proc
        try:
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=prompt.encode()), timeout=self._timeout
                )
            except (asyncio.TimeoutError, TimeoutError):
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
                yield WorkerEvent(
                    type="attempt.failed",
                    payload={"error_code": "timeout", "error": f"claude timed out after {self._timeout}s"},
                )
                return

            if proc.returncode != 0:
                yield WorkerEvent(
                    type="attempt.failed",
                    payload={
                        "error_code": "nonzero_exit",
                        "error": f"claude exited {proc.returncode}: {stderr.decode(errors='replace')[:200]}",
                    },
                )
                return

            try:
                result = json.loads(stdout.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as exc:
                yield WorkerEvent(
                    type="attempt.failed",
                    payload={"error_code": "malformed_json", "error": f"claude output was not valid JSON: {exc}"},
                )
                return

            if not isinstance(result, dict) or result.get("is_error") or result.get("subtype", "success") != "success":
                yield WorkerEvent(
                    type="attempt.failed",
                    payload={
                        "error_code": "cli_error",
                        "error": f"claude reported an error result: {str(result)[:200]}",
                    },
                )
                return

            summary = (result.get("result") or "").strip()
            if not summary:
                yield WorkerEvent(
                    type="attempt.failed",
                    payload={"error_code": "empty_response", "error": "claude returned empty result"},
                )
                return

            self._last_output[task.id] = summary
            yield WorkerEvent(type="metrics", payload=self._telemetry(result))
            yield WorkerEvent(type="attempt.completed", payload={"summary": summary})
        finally:
            # Never leave a live subprocess behind (cancelled generator, early
            # return, unexpected exception — the CLI call costs real money).
            self._procs.pop(attempt.id, None)
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()

    def _telemetry(self, result: dict) -> dict:
        """Extract token/cost telemetry from a CLI result object, defensively.

        Never raises: the money for the call was already spent by the time this
        runs, so a malformed usage field degrades to zeroed telemetry instead
        of crashing the task.
        """
        try:
            usage = result.get("usage") or {}
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
            cache_creation_tokens = int(usage.get("cache_creation_input_tokens") or 0)

            # The CLI resolves aliases ("sonnet" → "claude-sonnet-5"); prefer the
            # resolved name from modelUsage over the constructor-provided string.
            model = self._model
            model_usage = result.get("modelUsage") or {}
            if isinstance(model_usage, dict) and len(model_usage) == 1:
                model = next(iter(model_usage))

            # Some CLI versions report total_cost_usd: 0.0 under subscription
            # auth — treat 0/None as missing and compute from token counts.
            # Cache creation matters here: it dominates the cost of a CLI call.
            cost_usd = result.get("total_cost_usd")
            if not cost_usd and (input_tokens or output_tokens):
                cost_usd = calculate_cost(
                    model, input_tokens, output_tokens,
                    cache_read_tokens, cache_creation_tokens,
                )

            elapsed_s = (result.get("duration_ms") or 0) / 1000
            tps = round(output_tokens / elapsed_s, 1) if elapsed_s > 0 and output_tokens else 0.0

            return {
                "elapsed_s": round(elapsed_s, 2),
                "tokens": output_tokens,
                "throughput_tps": tps,
                "prompt_tokens": input_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_creation_tokens": cache_creation_tokens,
                "cost_usd": float(cost_usd or 0.0),
                "model": model,
            }
        except Exception as exc:
            logger.warning("claude-cli: telemetry extraction failed (%s) — recording zeros", exc)
            return {
                "elapsed_s": 0.0,
                "tokens": 0,
                "throughput_tps": 0.0,
                "prompt_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd": 0.0,
                "model": self._model,
            }

    def clear_history(self, task_id: str) -> None:
        """Clear per-task retry context after the task reaches terminal state."""
        self._last_output.pop(task_id, None)

    async def cancel(self, attempt_id: str) -> None:
        """Kill the live subprocess for an attempt, if one is still running."""
        proc = self._procs.get(attempt_id)
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def health(self) -> WorkerHealth:
        binary = shutil.which(self._binary)
        if not binary:
            return WorkerHealth(
                worker_id=self._worker_id,
                healthy=False,
                detail="claude not found in PATH. Install: npm install -g @anthropic-ai/claude-code",
            )
        return WorkerHealth(worker_id=self._worker_id, healthy=True, detail=f"binary={binary}")
