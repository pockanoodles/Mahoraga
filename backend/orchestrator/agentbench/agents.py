"""Coding agents the bench can drive.

An arm is `<agent>:<model>`, e.g. `aider:qwen3.5:latest`. An agent gets a
clean checkout, the task prompt, and — in the feedback condition — the
command that runs the task's tests. It edits files in place; scoring reads
the result from git, so an agent never reports its own success.

Agents: `aider` (search/replace edit blocks over a repo map) and `opencode`
(a tool loop: read, grep, edit, bash) on local Ollama models, and `claude`
(Claude Code, headless) as the cloud arm the local ones are measured against.
Same prompt, same tasks, same grader, so a difference is the arm's.

A local arm's model must be local: an Ollama cloud tag is refused, since
Ollama would forward the code off the machine. The cloud arm reaches its
vendor only through `egress.Proxy`, which tunnels to the vendor's API host
and nowhere else and records every connection: that proxy is the audited
egress for agent traffic, and the cloud arm won't start without it.

Containment is enforced by the OS, not asked of the agent. On macOS every
agent runs under `sandbox-exec`, on Linux under bubblewrap: it can write only
inside its checkout, its own home, and a temp dir, and can open connections
only to the loopback ports the harness gives it (Ollama, or the proxy). An
agent with a shell can `cd` anywhere; one did, into the real repo under
benchmark, when a bug handed it the wrong working directory. It can't read
the user's credentials either (`home_secrets`): whatever a cloud arm reads is
sent to its vendor.
"""
from __future__ import annotations

import contextlib
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
from typing import Iterator, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from . import egress, ollama
from .bank import bench_root

# Context every arm gets unless its spec says otherwise (`@ctx=N`). Sized for
# a plain transformer on a 16 GB machine; a 1,677-line file pulled into aider's
# chat overflows it, and Ollama truncates the prompt to fit rather than refusing
# it. A hybrid model holds far more, which is why tonight.DEFAULT_ARM asks 131k.
DEFAULT_NUM_CTX = 32768


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
    # The model answered in a form the agent can't act on, e.g. native tool-call
    # markup sent to aider; empty = the agent could read it. Like a crash, it
    # says nothing about the model's skill when nothing got edited.
    unreadable: str = ""
    # Model requests that failed (timeouts, Ollama 500s). The way a slow
    # machine reaches an outcome: without these or a timeout, it didn't.
    # None for an agent whose output doesn't say (only aider's log does).
    request_errors: int | None = None
    # What the vendor bills the attempt at its API prices, as the agent reports
    # it. None for a local arm, and for a cloud attempt cut off before its report.
    cost_usd: float | None = None


class Agent(Protocol):
    name: str
    model: str
    # False for a cloud arm: no Ollama model to probe, pull or unload.
    local: bool

    def attempt(self, workdir: Path, prompt: str, *,
                test_cmd: list[str] | None, timeout: float,
                hidden: Sequence[Path] = (), readable: Sequence[Path] = ()) -> AgentRun: ...


# The agent process group running right now, so a stopped run can take it down.
_live: set[int] = set()


def kill_live_agents() -> None:
    for pgid in list(_live):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _live.discard(pgid)


def is_local(agent) -> bool:
    return getattr(agent, "local", True)


def agent_home(name: str) -> Path:
    """An agent's own HOME: none of the user's config or credentials."""
    return bench_root() / "agent-home" / name


# Where credentials live under the user's real home. An agent gets its own
# HOME, so it only meets these by absolute path, and then it reads nothing.
_SECRETS = (".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".config",
            ".claude", ".codex", ".cursor",
            # Files: logins, and shell rc and history files, which hold exported tokens.
            ".netrc", ".git-credentials", ".npmrc", ".pypirc", ".yarnrc", ".pgpass",
            ".zshrc", ".zshenv", ".zprofile", ".bashrc", ".bash_profile", ".profile",
            ".zsh_history", ".bash_history", ".python_history", ".psql_history")


def home_secrets() -> list[Path]:
    """The user's credential dirs and files that exist. A cloud arm hides the
    whole home instead (`ClaudeCodeAgent`); this is every agent's floor."""
    home = Path.home()
    return [p.resolve() for name in _SECRETS if (p := home / name).exists()]


def linux_sandbox() -> bool:
    return sys.platform.startswith("linux") and shutil.which("bwrap") is not None


def has_sandbox() -> bool:
    return (sys.platform == "darwin" and shutil.which("sandbox-exec") is not None) \
        or linux_sandbox()


def _subpaths(paths: Sequence[Path]) -> str:
    return " ".join('(subpath "{}")'.format(str(Path(p).resolve()).replace('"', '\\"'))
                    for p in paths)


def sandbox_profile(writable: Sequence[Path], hidden: Sequence[Path] = (),
                    readable: Sequence[Path] = (), loopback: Sequence[int] | None = None) -> str:
    """Writes only under `writable` and the per-user temp area; network only
    to localhost: to the `loopback` ports if given (the cloud arm's proxy),
    else to any (Ollama, and agents whose own parts talk over localhost).
    Everything else, including the user's repos, is read-only to the agent
    and its children.

    `hidden` can't be read at all, except what `readable` carves back out:
    the bench hides the source repo, whose working tree holds every later
    commit's answer, and leaves readable the venv inside it that runs the
    tests. Only file contents and listings are denied, not metadata, so a
    path through a hidden directory still resolves."""
    temp = Path(tempfile.gettempdir()).resolve().parent  # .../T, .../C
    profile = ("(version 1)(allow default)"
               '(deny file-write* (subpath "/"))'
               f'(allow file-write* {_subpaths([*writable, temp])} (subpath "/dev"))'
               "(deny network-outbound)"
               + "".join(f'(allow network-outbound (remote ip "localhost:{p}"))'
                         for p in (loopback if loopback is not None else ["*"])))
    if hidden:
        profile += f"(deny file-read-data {_subpaths(hidden)})"
    if hidden:
        profile += f"(allow file-read-data {_subpaths([*readable, *writable])})"
    return profile


BRIDGE = Path(__file__).with_name("bridge.py")


def bwrap_args(cwd: Path, writable: Sequence[Path], hidden: Sequence[Path] = (),
               readable: Sequence[Path] = (), sockets: Sequence[Path] = ()) -> list[str]:
    """bubblewrap's mounts, in the order they stack: the whole system
    read-only; private temp and runtime dirs, so no socket the user's tmux,
    D-Bus or any daemon listens on is reachable; hidden dirs emptied; the
    readable and writable ones put back on top. Every namespace is new, the
    network one included, so the only way out is a socket in `sockets`.

    Unlike `sandbox-exec`, an emptied dir has no metadata to give away either:
    a path through it resolves only where something was put back."""
    def real(paths: Sequence[Path]) -> list[str]:
        return [str(Path(p).resolve()) for p in paths if Path(p).exists()]

    args = ["bwrap", "--die-with-parent", "--unshare-all",
            "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp", "--tmpfs", "/var/tmp", "--tmpfs", "/run"]
    for p in real(hidden):
        args += ["--tmpfs", p] if Path(p).is_dir() else ["--ro-bind", "/dev/null", p]
    for p in real(readable):
        args += ["--ro-bind", p, p]
    for p in real([*writable, *{Path(s).parent for s in sockets}]):
        args += ["--bind", p, p]
    return [*args, "--chdir", str(Path(cwd).resolve())]


def base_python() -> Path:
    """The interpreter under the harness's venv: what runs the bridge, with
    the standard library alone, so nothing of a hidden checkout is needed."""
    return Path(sys.executable).resolve()


def sandboxed(cmd: list[str], writable: Sequence[Path], hidden: Sequence[Path] = (),
              readable: Sequence[Path] = (), ports: Mapping[int, Path] | None = None,
              loopback: Sequence[int] | None = None) -> list[str]:
    """`cmd` under the OS sandbox. The first writable path is the working
    directory. Where the agent may connect:
      Linux: `ports`, loopback port -> the Unix socket outside it leads to;
        nothing else, since the agent has its own network namespace.
      macOS: localhost, at the `loopback` ports if given, else at any."""
    hidden = [*hidden, *home_secrets()]
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return ["sandbox-exec", "-p", sandbox_profile(writable, hidden, readable, loopback), *cmd]
    if linux_sandbox():
        ports = dict(ports or {})
        inner = cmd
        if ports:
            py = base_python()
            # The bridge lives in the harness, which is hidden when the harness
            # audits its own repo: its file and interpreter are put back alone.
            readable = [*readable, BRIDGE, py.parent.parent]
            inner = [str(py), "-I", "-S", str(BRIDGE),
                     *[f"{p}={s}" for p, s in ports.items()], "--", *cmd]
        return [*bwrap_args(writable[0], writable, hidden, readable, list(ports.values())),
                "--", *inner]
    return cmd  # elsewhere: HOME isolation and permission config only


@contextlib.contextmanager
def ollama_net() -> Iterator[tuple[dict[int, Path], str]]:
    """(ports, Ollama's URL as the agent sees it). Under bubblewrap the agent
    can't reach the host's loopback, so a relay carries its port 11434 to
    wherever Ollama really is."""
    if not linux_sandbox():
        yield {}, ollama.BASE_URL
        return
    u = urlsplit(ollama.BASE_URL)
    with tempfile.TemporaryDirectory(prefix="mh-net-") as d:  # short: AF_UNIX paths are
        sock = Path(d) / "ollama.sock"                         # capped near 104 bytes
        with egress.Relay(u.hostname or "127.0.0.1", u.port or 11434, sock):
            yield {OLLAMA_PORT: sock}, f"http://127.0.0.1:{OLLAMA_PORT}"


OLLAMA_PORT = 11434


def _launch(cmd: list[str], cwd: Path, env: dict[str, str], timeout: float,
            writable: Sequence[Path] = (), hidden: Sequence[Path] = (),
            readable: Sequence[Path] = (), ports: Mapping[int, Path] | None = None,
            loopback: Sequence[int] | None = None) -> tuple[str, bool, float]:
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
    proc = subprocess.Popen(sandboxed(cmd, [cwd, *writable], hidden, readable, ports, loopback),
                            cwd=cwd,
                            env=env, text=True,
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

    Each model request may take the whole attempt's limit. Left alone, aider
    gives litellm's 600 seconds, and a 16 GB laptop reading a 100k-token
    prompt at 131k needs longer: from 10-14 to 10-28 its attempts spent their
    time on requests cut at ten minutes and retried, some never got a reply,
    and a Studio reading ten times faster never met that clock at all.
    """

    name = "aider"
    local = True

    def __init__(self, model: str, *, num_ctx: int = DEFAULT_NUM_CTX, num_predict: int = 8192,
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
                test_cmd: list[str] | None, timeout: float,
                hidden: Sequence[Path] = (), readable: Sequence[Path] = ()) -> AgentRun:
        binary = shutil.which("aider")
        if not binary:
            raise FileNotFoundError("aider not on PATH")
        # In its own home, not the temp dir, which bubblewrap makes private.
        self.home.mkdir(parents=True, exist_ok=True)
        settings = self.home / ".agentbench-model-settings.yml"
        settings.write_text(self._settings())
        cmd = [binary, "--model", f"ollama_chat/{self.model}", "--edit-format", "diff",
               "--model-settings-file", str(settings), "--map-tokens", str(self.map_tokens),
               "--yes-always", "--no-auto-commits", "--no-gitignore", "--no-pretty",
               "--no-stream", "--no-show-model-warnings", "--no-check-update",
               "--no-analytics", "--no-show-release-notes", "--timeout", f"{timeout:g}",
               "--message", prompt]
        if test_cmd:
            cmd += ["--auto-test", "--test-cmd", shlex.join(test_cmd)]
        try:
            with ollama_net() as (ports, base):
                env = {**os.environ, "HOME": str(self.home), "OLLAMA_API_BASE": base}
                log, timed_out, secs = _launch(cmd, workdir, env, timeout, [self.home],
                                               hidden, readable, ports)
        finally:
            settings.unlink(missing_ok=True)
        tokens = re.findall(r"Tokens: ([\d.]+k?) sent, ([\d.]+k?) received", log)
        calls = aider_tool_calls(log)
        return AgentRun(
            log=log, secs=secs, timed_out=timed_out, crashed=aider_crash(log),
            unreadable=f"{calls} tool calls, which aider can't run" if calls else "",
            request_errors=aider_request_errors(log),
            llm_calls=len(tokens),
            tokens_sent=sum(_num(s) for s, _ in tokens),
            tokens_recv=sum(_num(r) for _, r in tokens),
            extra={
                # Search blocks quoting code that isn't in the file — granite's
                # failure mode in the first probe (27 of 36 attempts).
                "search_mismatches": log.count("must exactly match"),
                "format_errors": log.count("did not conform to the edit format"),
                # Replies cut off at num_predict, almost always mid-thought. The
                # cap is part of the arm, so these still count; this is how a
                # report tells "ran out of budget" from "answered wrong".
                "capped_calls": sum(_num(r) >= 0.98 * self.num_predict for _, r in tokens),
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


def aider_tool_calls(log: str) -> int:
    """Replies written as native tool calls (`<tool_call>` blocks), which aider
    prints as text and never runs. Ornith-1.5:9b on the Studio, 2026-10-13: 47
    of 102 attempts, each recorded as a no-edit in about 12 seconds."""
    return log.count("<tool_call>")


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
    local = True

    def __init__(self, model: str, *, num_ctx: int = DEFAULT_NUM_CTX, num_predict: int = 8192,
                 home: Path | None = None) -> None:
        _require_local(model)
        self.model, self.num_ctx, self.num_predict = model, num_ctx, num_predict
        self.home = home or agent_home("opencode")

    def _config(self, served: str, base: str | None = None) -> dict:
        return {
            "enabled_providers": ["ollama"], "autoupdate": False, "share": "disabled",
            "provider": {"ollama": {
                "npm": "@ai-sdk/openai-compatible", "name": "Ollama",
                "options": {"baseURL": f"{base or ollama.BASE_URL}/v1"},
                "models": {served: {"name": served, "tool_call": True}}}},
            "model": f"ollama/{served}", "small_model": f"ollama/{served}",
            "permission": {"edit": "allow", "bash": "allow", "webfetch": "deny",
                           "external_directory": "deny"},
        }

    def _env(self, served: str, base: str | None = None) -> dict[str, str]:
        h = self.home
        # Inherited XDG_* (terminals set some) would point back at the user's dirs.
        inherited = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
        return {**inherited, "HOME": str(h),
                "XDG_CONFIG_HOME": str(h / ".config"), "XDG_DATA_HOME": str(h / ".local/share"),
                "XDG_CACHE_HOME": str(h / ".cache"), "XDG_STATE_HOME": str(h / ".local/state"),
                "OPENCODE_CONFIG_CONTENT": json.dumps(self._config(served, base)),
                "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
                "OPENCODE_DISABLE_LSP_DOWNLOAD": "1"}

    def attempt(self, workdir: Path, prompt: str, *,
                test_cmd: list[str] | None, timeout: float,
                hidden: Sequence[Path] = (), readable: Sequence[Path] = ()) -> AgentRun:
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
        with ollama_net() as (ports, base):
            out, timed_out, secs = _launch(cmd, workdir, self._env(served, base), timeout,
                                           [self.home], hidden, readable, ports)
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


# The hosts each cloud agent's proxy tunnels to: the vendor's API, nothing else.
# A request anywhere else is refused and recorded, so the first attempt on a new
# version of the agent shows anything new it reaches for.
CLAUDE_HOSTS = ("api.anthropic.com:443",)
PROXY_PORT = 3128  # inside a Linux sandbox's own network namespace


def claude_token_file() -> Path:
    return Path(os.environ.get("MAHORAGA_CLAUDE_TOKEN_FILE",
                               Path.home() / ".config" / "mahoraga" / "claude-token"))


# All the cloud arm inherits from the harness's environment. Anything else
# could be a credential (a GITHUB_TOKEN, an ANTHROPIC_API_KEY that would bill
# the API instead of the token's login) that Bash would print into a reply.
_CLOUD_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "USER", "LOGNAME",
                   "SHELL", "TMPDIR")


class ClaudeCodeAgent:
    """Claude Code (`claude -p`), the cloud arm: a vendor's own agent on its
    own model, run the way a user runs it, so the local arms are measured
    against what they'd replace.

    The model is a pinned id (claude-sonnet-5-5), never an alias like
    `sonnet`, which moves when the vendor ships. Auth is a long-lived token
    from `claude setup-token`, read from `claude_token_file()`, so the CLI
    runs with a fresh, empty HOME for each attempt: none of the user's
    CLAUDE.md, skills, hooks, MCP servers or memory, and nothing one attempt
    writes reaches the next. Edits in the checkout and Bash are allowed; web
    fetch and search are not, and the sandbox gives no route out but the
    proxy, which reaches only `CLAUDE_HOSTS`.

    The feedback condition names the test command in the prompt, as for
    opencode. Cost is the CLI's own `total_cost_usd`: what the attempt costs
    at API prices, whether the login is a subscription or not.

    What it reads goes to the vendor, so it sees less than a local arm: none
    of the user's home but its checkout, its own HOME, and the programs it
    runs (the CLI, and the interpreter behind the test venv), and only the
    environment variables in `_CLOUD_ENV_KEEP`.
    """

    name = "claude"
    local = False
    vendor = "Anthropic"
    hosts = CLAUDE_HOSTS

    def __init__(self, model: str, *, home: Path | None = None,
                 token_file: Path | None = None) -> None:
        if not re.fullmatch(r"claude-[a-z0-9]+(-[a-z0-9.]+)+", model) or model.endswith("latest"):
            raise ValueError(f"{model!r} isn't a pinned Claude model id, e.g. claude-sonnet-5-5: "
                             "an alias moves when the vendor ships, and the arm with it")
        self.model = model
        self.home = home or agent_home("claude")
        self.token_file = token_file

    def _token(self) -> str:
        path = self.token_file or claude_token_file()
        try:
            token = path.read_text().strip()
        except FileNotFoundError:
            raise FileNotFoundError(
                f"no Claude token at {path}: run `claude setup-token` and save what it prints "
                f"there (mkdir -p {path.parent} && (umask 077; cat > {path})), or point "
                "MAHORAGA_CLAUDE_TOKEN_FILE at it") from None
        if not token:
            raise ValueError(f"{path} is empty")
        return token

    def _env(self, home: Path, token: str, proxy_url: str) -> dict[str, str]:
        inherited = {k: os.environ[k] for k in _CLOUD_ENV_KEEP if k in os.environ}
        return {**inherited, "HOME": str(home), "CLAUDE_CODE_OAUTH_TOKEN": token,
                "HTTPS_PROXY": proxy_url, "HTTP_PROXY": proxy_url,
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
                "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
                "DISABLE_BUG_COMMAND": "1"}

    def _cmd(self, binary: str, prompt: str) -> list[str]:
        # The prompt goes right after -p: a list-valued flag before it would take it in.
        return [binary, "-p", prompt, "--output-format", "stream-json", "--verbose",
                "--model", self.model, "--permission-mode", "acceptEdits",
                "--allowedTools", "Bash", "--disallowedTools", "WebFetch,WebSearch",
                "--strict-mcp-config"]

    @staticmethod
    def confine(binary: Path, hidden: Sequence[Path], readable: Sequence[Path]
                ) -> tuple[list[Path], list[Path]]:
        """The whole home hidden, and back on top only what the attempt runs:
        the CLI, each readable venv and the interpreter it was made from."""
        runtimes = [(Path(v) / "bin" / "python").resolve().parent.parent for v in readable
                    if (Path(v) / "bin" / "python").exists()]
        return ([*hidden, Path.home().resolve()],
                [*readable, binary.parent, *runtimes])

    def attempt(self, workdir: Path, prompt: str, *,
                test_cmd: list[str] | None, timeout: float,
                hidden: Sequence[Path] = (), readable: Sequence[Path] = ()) -> AgentRun:
        found = shutil.which("claude")
        if not found:
            raise FileNotFoundError("claude not on PATH")
        binary = Path(found).resolve()  # run by its real path: the link sits in the hidden home
        if not has_sandbox():
            raise RuntimeError("the cloud arm runs only inside the OS sandbox, which this machine "
                               "doesn't have: on Linux, install bubblewrap (apt install bubblewrap)")
        token = self._token()
        if test_cmd:
            prompt += f"\n\nYou can check your work by running: {shlex.join(test_cmd)}"
        home = self.home / "attempt"  # fresh every time
        shutil.rmtree(home, ignore_errors=True)
        home.mkdir(parents=True)
        hidden, readable = self.confine(binary, hidden, readable)
        with contextlib.ExitStack() as stack:
            if linux_sandbox():
                d = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="mh-net-")))
                proxy = stack.enter_context(egress.Proxy(self.hosts, unix=d / "egress.sock"))
                ports, url = {PROXY_PORT: d / "egress.sock"}, f"http://127.0.0.1:{PROXY_PORT}"
                loopback = None
            else:
                proxy = stack.enter_context(egress.Proxy(self.hosts))
                ports, url = {}, f"http://{proxy.address}"
                loopback = [int(proxy.address.rsplit(":", 1)[1])]  # the proxy and nothing else
            out, timed_out, secs = _launch(self._cmd(str(binary), prompt), workdir,
                                           self._env(home, token, url), timeout, [home],
                                           hidden, readable, ports, loopback)
        run = parse_claude_events(out)
        run.log, run.secs, run.timed_out = out + proxy.log_lines(), secs, timed_out
        run.extra.update(proxy.summary())
        if timed_out:
            run.crashed = ""  # cut off by the limit, which is an outcome, not a crash
        return run


def parse_claude_events(out: str) -> AgentRun:
    """`claude -p --output-format stream-json` emits one event per line: an
    `assistant` event per content block (blocks of one model call share its
    message id and usage), `user` events carrying tool results, and one
    `result` at the end with the session's usage and cost.

    An error result (a refused login, a usage limit) is the harness failing,
    not the model, so it's a crash: the attempt is retried, not scored. So is
    a run that ends without a result, unless the limit cut it off."""
    run = AgentRun(log="", secs=0, timed_out=False,
                   extra={"tool_calls": 0, "tool_errors": 0, "bash_calls": 0, "turns": 0})
    usage: dict[str, dict] = {}
    result: dict | None = None
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind, msg = event.get("type"), event.get("message") or {}
        if kind == "assistant":
            if msg.get("id"):
                usage[msg["id"]] = msg.get("usage") or usage.get(msg["id"]) or {}
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    run.extra["tool_calls"] += 1
                    run.extra["bash_calls"] += block.get("name") == "Bash"
        elif kind == "user":
            content = msg.get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result" \
                        and block.get("is_error"):
                    run.extra["tool_errors"] += 1
        elif kind == "result":
            result = event
    run.llm_calls = len(usage)
    totals = (result or {}).get("usage") or {}
    per_call = list(usage.values())

    def tokens(*keys: str) -> float:
        if totals:
            return sum(totals.get(k) or 0 for k in keys)
        return sum(u.get(k) or 0 for u in per_call for k in keys)

    run.tokens_sent = tokens("input_tokens", "cache_read_input_tokens",
                             "cache_creation_input_tokens")
    run.tokens_recv = tokens("output_tokens")
    if result is None:
        run.crashed = "claude ended without a result"
        return run
    run.extra["turns"] = int(result.get("num_turns") or 0)
    cost = result.get("total_cost_usd")
    run.cost_usd = float(cost) if isinstance(cost, (int, float)) else None
    if result.get("is_error"):
        why = str(result.get("result") or result.get("subtype") or "error")
        run.crashed = f"claude: {why.splitlines()[0][:200] if why else 'error'}"
    return run


AGENTS = {"aider": AiderAgent, "opencode": OpenCodeAgent, "claude": ClaudeCodeAgent}


def parse_arm(spec: str) -> Agent:
    """`<agent>:<model>[@ctx=N]`, e.g. aider:gpt-oss:20b@ctx=131072. The context
    is part of the arm: the same model at two sizes is two arms, recorded and
    reported apart. Ollama tags can't contain `@`, so the split is unambiguous.
    A cloud arm (claude:claude-sonnet-5-5) has the vendor's context, not ours."""
    body, at, opt = spec.partition("@")
    name, sep, model = body.partition(":")
    if not sep or not model:
        raise ValueError(f"arm {spec!r} must be <agent>:<model>, e.g. aider:qwen3.5:latest")
    if name not in AGENTS:
        raise ValueError(f"unknown agent {name!r}; available: {', '.join(sorted(AGENTS))}")
    if not AGENTS[name].local:
        if at:
            raise ValueError(f"{name} is a cloud arm: its context is the vendor's, so "
                             f"@{opt} can't be set")
        return AGENTS[name](model)
    num_ctx = DEFAULT_NUM_CTX
    if at:
        key, eq, val = opt.partition("=")
        if key != "ctx" or not eq or not val.isdigit() or int(val) < 2048:
            raise ValueError(f"arm option {opt!r} must be ctx=<tokens>, at least 2048, "
                             "e.g. aider:gpt-oss:20b@ctx=131072")
        num_ctx = int(val)
    return AGENTS[name](model, num_ctx=num_ctx)
