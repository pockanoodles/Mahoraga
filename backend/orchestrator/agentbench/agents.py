"""Coding agents the bench can drive.

An arm is `<agent>:<model>`, e.g. `aider:qwen3.5:latest`. An agent gets a
clean checkout, the task prompt, and — in the feedback condition — the
command that runs the task's tests. It edits files in place; scoring reads
the result from git, so an agent never reports its own success.

Models are local Ollama models only. Driving a cloud model through a
third-party agent would send code to a provider without passing through the
repo's single audited egress client, so it is refused, not configured.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class AgentRun:
    log: str
    secs: float
    timed_out: bool
    llm_calls: int = 0
    tokens_sent: float = 0
    tokens_recv: float = 0
    # Agent-specific diagnostics, e.g. aider's edit-format failures.
    extra: dict[str, int] = field(default_factory=dict)


class Agent(Protocol):
    name: str
    model: str

    def attempt(self, workdir: Path, prompt: str, *,
                test_cmd: list[str] | None, timeout: float) -> AgentRun: ...


def _require_local(model: str) -> None:
    """Every model is addressed as `ollama_chat/<model>`, so requests can only
    reach the Ollama server. The leak left is Ollama's own cloud-hosted tags,
    which Ollama forwards off the machine."""
    tag = model.rsplit(":", 1)[-1] if ":" in model else ""
    if tag == "cloud" or tag.endswith("-cloud"):
        raise ValueError(
            f"{model!r} is an Ollama cloud model: agentbench drives local models "
            "only, since this would send code off the machine outside the "
            "audited egress client")


class AiderAgent:
    """Aider with the search/replace (`diff`) edit format.

    `diff` rather than aider's per-model default: small local models default to
    `whole`, which rewrites entire files and is unusable on large ones. Each
    call's generation is capped (`num_predict`) so a thinking-mode runaway
    becomes a failed attempt instead of a hung one.
    """

    name = "aider"

    def __init__(self, model: str, *, num_ctx: int = 32768, num_predict: int = 8192,
                 map_tokens: int = 2048) -> None:
        _require_local(model)
        self.model = model
        self.num_ctx, self.num_predict, self.map_tokens = num_ctx, num_predict, map_tokens

    def _settings(self) -> str:
        return (f"- name: ollama_chat/{self.model}\n"
                f"  extra_params: {{num_ctx: {self.num_ctx}, num_predict: {self.num_predict}}}\n"
                f"  reasoning_tag: think\n")

    def attempt(self, workdir: Path, prompt: str, *,
                test_cmd: list[str] | None, timeout: float) -> AgentRun:
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write(self._settings())
            settings = f.name
        cmd = ["aider", "--model", f"ollama_chat/{self.model}", "--edit-format", "diff",
               "--model-settings-file", settings, "--map-tokens", str(self.map_tokens),
               "--yes-always", "--no-auto-commits", "--no-gitignore", "--no-pretty",
               "--no-stream", "--no-show-model-warnings", "--no-check-update",
               "--no-analytics", "--no-show-release-notes", "--message", prompt]
        if test_cmd:
            cmd += ["--auto-test", "--test-cmd", shlex.join(test_cmd)]
        env = {**os.environ, "OLLAMA_API_BASE":
               os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")}
        t0 = time.monotonic()
        try:
            r = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                               timeout=timeout, env=env)
            log, timed_out = r.stdout + r.stderr, False
        except subprocess.TimeoutExpired as e:
            out = e.stdout or ""
            log = out.decode(errors="replace") if isinstance(out, bytes) else out
            timed_out = True
        finally:
            os.unlink(settings)
        tokens = re.findall(r"Tokens: ([\d.]+k?) sent, ([\d.]+k?) received", log)
        return AgentRun(
            log=log, secs=round(time.monotonic() - t0, 1), timed_out=timed_out,
            llm_calls=len(tokens),
            tokens_sent=sum(_num(s) for s, _ in tokens),
            tokens_recv=sum(_num(r) for _, r in tokens),
            extra={
                # Search blocks quoting code that isn't in the file — granite's
                # failure mode in the first probe (27 of 36 attempts).
                "search_mismatches": log.count("must exactly match"),
                "format_errors": log.count("did not conform to the edit format"),
            },
        )


def _num(s: str) -> float:
    return float(s[:-1]) * 1000 if s.endswith("k") else float(s)


AGENTS = {"aider": AiderAgent}


def parse_arm(spec: str) -> Agent:
    name, sep, model = spec.partition(":")
    if not sep or not model:
        raise ValueError(f"arm {spec!r} must be <agent>:<model>, e.g. aider:qwen3.5:latest")
    if name not in AGENTS:
        raise ValueError(f"unknown agent {name!r}; available: {', '.join(sorted(AGENTS))}")
    return AGENTS[name](model)
