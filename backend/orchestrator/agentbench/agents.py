"""Coding agents the bench can drive.

An arm is `<agent>:<model>`, e.g. `aider:qwen3.5:latest`. An agent gets a
clean checkout, the task prompt, and — in the feedback condition — the
command that runs the task's tests. It edits files in place; scoring reads
the result from git, so an agent never reports its own success.

Agents: `aider` (search/replace edit blocks over a repo map) and `opencode`
(a tool loop: read, grep, edit, bash). Same prompt, same models, same
generation caps, so a difference between them is the agent's.

Models are local Ollama models only. Driving a cloud model through a
third-party agent would send code to a provider without passing through the
repo's single audited egress client, so it is refused, not configured.

Containment is enforced by the OS, not asked of the agent. On macOS every
agent runs under `sandbox-exec`: it can write only inside its checkout, its
own home, and the temp dir, and can open network connections only to
localhost. An agent with a shell can `cd` anywhere; one did, into the real
repo under benchmark, when a bug handed it the wrong working directory.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from . import ollama
from .bank import DEFAULT_ROOT


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
    # The agent itself died, e.g. "ImportError in _svdp.py line 23"; empty = it
    # ran. A crash is the harness failing, not the model, so it isn't scored.
    crashed: str = ""
    # Model requests that failed (timeouts, Ollama 500s). The way a slow
    # machine reaches an outcome: without these or a timeout, it didn't.
    request_errors: int = 0


class Agent(Protocol):
    name: str
    model: str

    def attempt(self, workdir: Path, prompt: str, *,
                test_cmd: list[str] | None, timeout: float) -> AgentRun: ...


# The agent process group running right now, so a stopped run can take it down.
_live: set[int] = set()


def kill_live_agents() -> None:
    for pgid in list(_live):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _live.discard(pgid)


def agent_home(name: str) -> Path:
    """An agent's own HOME: none of the user's config or credentials."""
    return Path(os.environ.get("MAHORAGA_AGENTBENCH_ROOT", DEFAULT_ROOT)) / "agent-home" / name


def sandbox_profile(writable: list[Path]) -> str:
    """Writes only under `writable` and the per-user temp area; network only
    to localhost (Ollama). Everything else, including the user's repos, is
    read-only to the agent and its children."""
    temp = Path(tempfile.gettempdir()).resolve().parent  # .../T, .../C
    paths = [Path(w).resolve() for w in writable] + [temp]
    allow = " ".join('(subpath "{}")'.format(str(p).replace('"', '\\"')) for p in paths)
    return ("(version 1)(allow default)"
            '(deny file-write* (subpath "/"))'
            f'(allow file-write* {allow} (subpath "/dev"))'
            "(deny network-outbound)"
            '(allow network-outbound (remote ip "localhost:*"))')


def sandboxed(cmd: list[str], writable: list[Path]) -> list[str]:
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return ["sandbox-exec", "-p", sandbox_profile(writable), *cmd]
    return cmd  # elsewhere: HOME isolation and permission config only


def _launch(cmd: list[str], cwd: Path, env: dict[str, str], timeout: float,
            writable: list[Path] = ()) -> tuple[str, bool, float]:
    """Run an agent sandboxed, in its own process group, with `cwd` as its
    working directory in every sense: some agents resolve it from $PWD rather
    than the real cwd, which is how one ran in the wrong repo. Returns
    (output, timed_out, secs). A timeout or a stopped run kills the whole
    group, so the agent's own children can't outlive the attempt.

    stdin is closed: an agent that asks a question gets EOF at once. Inherited
    from a terminal, aider's crash handler sat on "Open a GitHub Issue? (Y/n)"
    until the 20-minute timeout, and each crash was recorded as a no-edit."""
    t0 = time.monotonic()
    env = {**env, "PWD": str(cwd)}
    proc = subprocess.Popen(sandboxed(cmd, [cwd, *writable]), cwd=cwd, env=env, text=True,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    _live.add(proc.pid)
    try:
        out, _ = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        timed_out = True
    finally:
        _live.discard(proc.pid)
    return out or "", timed_out, round(time.monotonic() - t0, 1)


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
                 map_tokens: int = 2048, home: Path | None = None) -> None:
        _require_local(model)
        self.model = model
        self.num_ctx, self.num_predict, self.map_tokens = num_ctx, num_predict, map_tokens
        self.home = home or agent_home("aider")

    def _settings(self) -> str:
        return (f"- name: ollama_chat/{self.model}\n"
                f"  extra_params: {{num_ctx: {self.num_ctx}, num_predict: {self.num_predict}}}\n"
                f"  reasoning_tag: think\n")

    def attempt(self, workdir: Path, prompt: str, *,
                test_cmd: list[str] | None, timeout: float) -> AgentRun:
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write(self._settings())
            settings = f.name
        binary = shutil.which("aider")
        if not binary:
            raise FileNotFoundError("aider not on PATH")
        cmd = [binary, "--model", f"ollama_chat/{self.model}", "--edit-format", "diff",
               "--model-settings-file", settings, "--map-tokens", str(self.map_tokens),
               "--yes-always", "--no-auto-commits", "--no-gitignore", "--no-pretty",
               "--no-stream", "--no-show-model-warnings", "--no-check-update",
               "--no-analytics", "--no-show-release-notes", "--message", prompt]
        if test_cmd:
            cmd += ["--auto-test", "--test-cmd", shlex.join(test_cmd)]
        self.home.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "HOME": str(self.home), "OLLAMA_API_BASE": ollama.BASE_URL}
        try:
            log, timed_out, secs = _launch(cmd, workdir, env, timeout, [self.home])
        finally:
            os.unlink(settings)
        tokens = re.findall(r"Tokens: ([\d.]+k?) sent, ([\d.]+k?) received", log)
        return AgentRun(
            log=log, secs=secs, timed_out=timed_out, crashed=aider_crash(log),
            request_errors=aider_request_errors(log),
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


def aider_crash(log: str) -> str:
    """What killed aider, from its crash report ("# Uncaught ImportError in
    _svdp.py line 23"), or "" if it ran. The first one seen on the Studio was
    scipy 1.15.3 failing to load under macOS 27, before any model call."""
    m = re.search(r"^# Uncaught (\w+) in (.+)$", log, re.MULTILINE)
    if m:
        return f"{m.group(1)} in {m.group(2).strip()}"
    return "uncaught exception" if "An uncaught exception occurred" in log else ""


# One line per failed request, as litellm reports it through aider, e.g. from
# the throttled 2026-09-29 run: "litellm.APIConnectionError: Ollama_chatException
# - litellm.Timeout: Connection timed out after 600.0 seconds."
_REQUEST_ERROR = re.compile(r"litellm\.(?:APIConnectionError|InternalServerError|"
                            r"ServiceUnavailableError|APIError|Timeout)\b")


def aider_request_errors(log: str) -> int:
    return sum(1 for line in log.splitlines() if _REQUEST_ERROR.search(line))


def _num(s: str) -> float:
    return float(s[:-1]) * 1000 if s.endswith("k") else float(s)


class OpenCodeAgent:
    """opencode (`opencode run`), which reaches Ollama through its
    OpenAI-compatible endpoint.

    That endpoint ignores per-request options, so the model is served as a
    derived tag with aider's num_ctx and num_predict baked in; otherwise
    Ollama's default context would silently truncate opencode's prompt.

    Isolation: opencode runs with its own HOME and XDG dirs under the bench
    root, so it never sees the user's provider credentials. Only the ollama
    provider is enabled, web fetch is denied, sharing and autoupdate are off.
    The sandbox (see `sandbox_profile`) is what actually confines it.

    The feedback condition has no auto-test hook here: the prompt names the
    test command, and the agent can run it with its bash tool.
    """

    name = "opencode"

    def __init__(self, model: str, *, num_ctx: int = 32768, num_predict: int = 8192,
                 home: Path | None = None) -> None:
        _require_local(model)
        self.model, self.num_ctx, self.num_predict = model, num_ctx, num_predict
        self.home = home or agent_home("opencode")

    def _config(self, served: str) -> dict:
        return {
            "enabled_providers": ["ollama"], "autoupdate": False, "share": "disabled",
            "provider": {"ollama": {
                "npm": "@ai-sdk/openai-compatible", "name": "Ollama",
                "options": {"baseURL": f"{ollama.BASE_URL}/v1"},
                "models": {served: {"name": served, "tool_call": True}}}},
            "model": f"ollama/{served}", "small_model": f"ollama/{served}",
            "permission": {"edit": "allow", "bash": "allow", "webfetch": "deny",
                           "external_directory": "deny"},
        }

    def _env(self, served: str) -> dict[str, str]:
        h = self.home
        # Inherited XDG_* (terminals set some) would point back at the user's dirs.
        inherited = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
        return {**inherited, "HOME": str(h),
                "XDG_CONFIG_HOME": str(h / ".config"), "XDG_DATA_HOME": str(h / ".local/share"),
                "XDG_CACHE_HOME": str(h / ".cache"), "XDG_STATE_HOME": str(h / ".local/state"),
                "OPENCODE_CONFIG_CONTENT": json.dumps(self._config(served)),
                "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
                "OPENCODE_DISABLE_LSP_DOWNLOAD": "1"}

    def attempt(self, workdir: Path, prompt: str, *,
                test_cmd: list[str] | None, timeout: float) -> AgentRun:
        binary = shutil.which("opencode")
        if not binary:
            raise FileNotFoundError("opencode not on PATH")
        served = ollama.derive(self.model, {"num_ctx": self.num_ctx,
                                            "num_predict": self.num_predict})
        if test_cmd:
            prompt += f"\n\nYou can check your work by running: {shlex.join(test_cmd)}"
        self.home.mkdir(parents=True, exist_ok=True)
        cmd = [binary, "run", "--pure", "--format", "json", "--title", "agentbench",
               "--dir", str(workdir), "--model", f"ollama/{served}", prompt]
        out, timed_out, secs = _launch(cmd, workdir, self._env(served), timeout, [self.home])
        run = parse_opencode_events(out)
        run.log, run.secs, run.timed_out = out, secs, timed_out
        return run


def parse_opencode_events(out: str) -> AgentRun:
    """`opencode run --format json` emits one event per line: a step_finish
    per model call (with its token counts) and a tool_use per tool call."""
    run = AgentRun(log="", secs=0, timed_out=False,
                   extra={"tool_calls": 0, "tool_errors": 0, "bash_calls": 0})
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        part = event.get("part") or {}
        if event.get("type") == "step_finish":
            tok = part.get("tokens") or {}
            run.llm_calls += 1
            run.tokens_sent += tok.get("input", 0) + (tok.get("cache") or {}).get("read", 0)
            run.tokens_recv += tok.get("output", 0) + tok.get("reasoning", 0)
        elif event.get("type") == "tool_use":
            run.extra["tool_calls"] += 1
            run.extra["bash_calls"] += part.get("tool") == "bash"
            run.extra["tool_errors"] += (part.get("state") or {}).get("status") == "error"
    return run


AGENTS = {"aider": AiderAgent, "opencode": OpenCodeAgent}


def parse_arm(spec: str) -> Agent:
    name, sep, model = spec.partition(":")
    if not sep or not model:
        raise ValueError(f"arm {spec!r} must be <agent>:<model>, e.g. aider:qwen3.5:latest")
    if name not in AGENTS:
        raise ValueError(f"unknown agent {name!r}; available: {', '.join(sorted(AGENTS))}")
    return AGENTS[name](model)
