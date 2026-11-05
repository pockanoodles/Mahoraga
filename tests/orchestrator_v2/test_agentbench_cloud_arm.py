"""The cloud arm: Claude Code under the sandbox, reaching its vendor only
through the audited proxy, and the Linux sandbox it needs."""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from backend.orchestrator.agentbench import agents, egress, manifest
from backend.orchestrator.agentbench.agents import (
    BRIDGE, ClaudeCodeAgent, base_python, bwrap_args, has_sandbox, parse_arm, parse_claude_events,
    sandbox_profile, sandboxed,
)
from backend.orchestrator.agentbench.runner import arm_name


# ── helpers ───────────────────────────────────────────────────────────────────


class Echo:
    """A TCP server on 127.0.0.1 that sends back what it gets."""

    def __enter__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._echo, args=(conn,), daemon=True).start()

    @staticmethod
    def _echo(conn):
        with conn:
            while data := conn.recv(4096):
                conn.sendall(data)

    def __exit__(self, *exc):
        self.sock.close()


def _short_dir() -> Path:
    # AF_UNIX paths are capped near 104 bytes; pytest's tmp_path can be longer.
    return Path(tempfile.mkdtemp(prefix="mh-t-"))


def _connect(proxy: egress.Proxy) -> socket.socket:
    if proxy.unix is not None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(proxy.unix))
    else:
        host, port = proxy.address.split(":")
        s = socket.create_connection((host, int(port)))
    s.settimeout(5)
    return s


def _ask(proxy: egress.Proxy, request: bytes) -> tuple[socket.socket, bytes]:
    s = _connect(proxy)
    s.sendall(request)
    reply = b""
    while b"\r\n\r\n" not in reply:
        reply += s.recv(4096)
    return s, reply.split(b"\r\n", 1)[0]


# ── the proxy ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("over_unix", [False, True])
def test_the_proxy_tunnels_to_an_allowed_host_and_counts_the_bytes(over_unix):
    with Echo() as echo:
        unix = _short_dir() / "p.sock" if over_unix else None
        with egress.Proxy([f"127.0.0.1:{echo.port}"], unix=unix) as proxy:
            s, status = _ask(proxy, f"CONNECT 127.0.0.1:{echo.port} HTTP/1.1\r\n\r\n".encode())
            assert status == b"HTTP/1.1 200 Connection Established"
            s.sendall(b"ping")
            assert s.recv(4) == b"ping"
            s.close()
    [t] = proxy.tunnels
    assert t.allowed and (t.host, t.port) == ("127.0.0.1", echo.port)
    assert (t.sent, t.received) == (4, 4)
    assert proxy.summary() == {"egress_tunnels": 1, "egress_refused": 0,
                               "egress_bytes_sent": 4, "egress_bytes_received": 4}
    assert json.loads(proxy.log_lines())["type"] == "egress"


def test_the_proxy_refuses_and_records_everything_else():
    with egress.Proxy(["api.anthropic.com:443"]) as proxy:
        s, status = _ask(proxy, b"CONNECT github.com:443 HTTP/1.1\r\nHost: github.com\r\n\r\n")
        assert status.startswith(b"HTTP/1.1 403")
        s.close()
        s, status = _ask(proxy, b"GET http://pypi.org/simple/ HTTP/1.1\r\n\r\n")
        assert status.startswith(b"HTTP/1.1 405")
        s.close()
    assert [(t.host, t.port, t.allowed) for t in proxy.tunnels] == [
        ("github.com", 443, False), ("pypi.org", 80, False)]
    assert proxy.summary()["egress_refused"] == 2


def test_the_relay_carries_a_unix_socket_to_its_one_target():
    with Echo() as echo, egress.Relay("127.0.0.1", echo.port, _short_dir() / "r.sock") as relay:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(str(relay.unix))
        s.sendall(b"hello")
        assert s.recv(5) == b"hello"
        s.close()


def test_the_bridge_turns_a_loopback_port_into_the_socket_outside():
    with socket.socket() as probe:  # a free port; the bridge binds it next
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    dial = ("import socket; s = socket.create_connection(('127.0.0.1', %d), 5); "
            "s.sendall(b'via-bridge'); print(s.recv(10).decode())" % port)
    with Echo() as echo, egress.Relay("127.0.0.1", echo.port, _short_dir() / "b.sock") as relay:
        out = subprocess.run([sys.executable, "-I", str(BRIDGE), f"{port}={relay.unix}", "--",
                              sys.executable, "-c", dial],
                             capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "via-bridge"


def test_the_bridge_exits_with_its_commands_status():
    out = subprocess.run([sys.executable, "-I", str(BRIDGE), "--", sys.executable, "-c",
                          "raise SystemExit(7)"], timeout=30)
    assert out.returncode == 7


# ── the Linux sandbox ─────────────────────────────────────────────────────────


def test_bwrap_mounts_stack_hidden_under_what_is_put_back(tmp_path):
    bench, repo, home = tmp_path / "bench", tmp_path / "repo", tmp_path / "bench" / "home"
    clone, venv, socks = bench / "clone", repo / ".venv", tmp_path / "net"
    for d in (clone, venv, home, socks):
        d.mkdir(parents=True)
    netrc = tmp_path / ".netrc"
    netrc.write_text("machine github.com password x")
    args = bwrap_args(clone, [clone, home], hidden=[repo, bench, tmp_path / "gone", netrc],
                      readable=[venv], sockets=[socks / "egress.sock"])
    assert args[:3] == ["bwrap", "--die-with-parent", "--unshare-all"]  # the network too
    flat = " ".join(args)
    r = lambda p: str(p.resolve())  # noqa: E731
    # Private temp and runtime dirs, so no daemon's socket is reachable.
    assert "--tmpfs /tmp --tmpfs /var/tmp --tmpfs /run" in flat
    # Hidden first, then put back: readable over hidden, writable over both.
    order = [flat.index(x) for x in (f"--tmpfs {r(repo)}", f"--tmpfs {r(bench)}",
                                     f"--ro-bind {r(venv)}", f"--bind {r(clone)}",
                                     f"--bind {r(socks)}")]
    assert order == sorted(order)
    assert str(tmp_path / "gone") not in flat  # a missing dir can't be mounted over
    assert f"--ro-bind /dev/null {r(netrc)}" in flat  # a hidden file reads as empty
    assert args[-2:] == ["--chdir", r(clone)]


def test_linux_runs_the_agent_under_bwrap_through_the_bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/bwrap" if n == "bwrap" else None)
    cmd = sandboxed(["claude", "-p", "x"], [tmp_path], ports={3128: tmp_path / "e.sock"})
    i = cmd.index("--", cmd.index("--chdir"))
    assert cmd[0] == "bwrap"
    # The bridge runs on the base interpreter with the stdlib alone, its file
    # put back by name: the harness's own checkout may be the hidden repo.
    assert cmd[i + 1:] == [str(base_python()), "-I", "-S", str(BRIDGE),
                           f"3128={tmp_path / 'e.sock'}", "--", "claude", "-p", "x"]
    assert f"--ro-bind {BRIDGE.resolve()} {BRIDGE.resolve()}" in " ".join(cmd)
    # No ports: no bridge, and no way out at all.
    plain = sandboxed(["sh"], [tmp_path])
    assert plain[plain.index("--", plain.index("--chdir")) + 1:] == ["sh"]


def test_linux_without_bwrap_has_no_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda n: None)
    assert sandboxed(["sh"], [tmp_path]) == ["sh"]
    assert not has_sandbox()


def test_the_users_credentials_are_hidden_from_every_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for d in (".ssh", ".config", "notes"):
        (tmp_path / d).mkdir()
    (tmp_path / ".git-credentials").write_text("https://u:token@github.com\n")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/" + n)
    profile = sandboxed(["sh"], [tmp_path / "work"])[2]
    for secret in (".ssh", ".config", ".git-credentials"):
        assert f'(subpath "{(tmp_path / secret).resolve()}")' in profile
    assert "notes" not in profile


def test_on_macos_a_cloud_arm_reaches_one_loopback_port():
    local = sandbox_profile([Path("/w")])
    assert '(remote ip "localhost:*")' in local  # local arms: Ollama, and themselves
    cloud = sandbox_profile([Path("/w")], loopback=[51234])
    assert '(remote ip "localhost:51234")' in cloud and "localhost:*" not in cloud


# ── Claude Code's events ──────────────────────────────────────────────────────


def _ev(**e) -> str:
    return json.dumps(e)


_CLAUDE_EVENTS = "\n".join([
    _ev(type="system", subtype="init", model="claude-sonnet-5-5", tools=["Bash", "Edit"]),
    # One model call, two content blocks: one call, counted once.
    _ev(type="assistant", message={"id": "m1", "usage": {"input_tokens": 10, "output_tokens": 5},
                                   "content": [{"type": "text", "text": "Looking."}]}),
    _ev(type="assistant", message={"id": "m1", "usage": {"input_tokens": 10, "output_tokens": 9},
                                   "content": [{"type": "tool_use", "name": "Bash", "id": "t1"}]}),
    _ev(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t1",
                                           "is_error": True, "content": "exit 1"}]}),
    _ev(type="assistant", message={"id": "m2", "usage": {"input_tokens": 3, "output_tokens": 4},
                                   "content": [{"type": "tool_use", "name": "Edit", "id": "t2"}]}),
    "not json",
    _ev(type="result", subtype="success", is_error=False, num_turns=3, total_cost_usd=0.4321,
        usage={"input_tokens": 13, "cache_read_input_tokens": 1000,
               "cache_creation_input_tokens": 200, "output_tokens": 13}),
])


def test_claude_events_are_counted():
    run = parse_claude_events(_CLAUDE_EVENTS)
    assert run.llm_calls == 2
    assert run.tokens_sent == 13 + 1000 + 200  # cache reads and writes were sent too
    assert run.tokens_recv == 13
    assert run.cost_usd == 0.4321
    assert run.extra == {"tool_calls": 2, "tool_errors": 1, "bash_calls": 1, "turns": 3}
    assert run.crashed == ""


def test_a_claude_error_result_is_the_harness_failing_not_the_model():
    limit = _ev(type="result", subtype="success", is_error=True, num_turns=1,
                result="Claude AI usage limit reached|1762380000", total_cost_usd=0)
    assert parse_claude_events(limit).crashed == "claude: Claude AI usage limit reached|1762380000"
    cut = parse_claude_events(_CLAUDE_EVENTS.rsplit("\n", 1)[0])  # no result line
    assert cut.crashed == "claude ended without a result"
    assert cut.cost_usd is None
    assert (cut.tokens_sent, cut.tokens_recv) == (13, 13)  # from the calls it did report


# ── the agent ─────────────────────────────────────────────────────────────────


def test_a_cloud_arm_names_a_pinned_model():
    agent = parse_arm("claude:claude-sonnet-5-5")
    assert isinstance(agent, ClaudeCodeAgent) and not agent.local
    assert arm_name(agent) == "claude:claude-sonnet-5-5"  # no context in its name
    for bad in ("claude:sonnet", "claude:opus", "claude:claude-sonnet-latest", "claude:gpt-5"):
        with pytest.raises(ValueError, match="pinned"):
            parse_arm(bad)
    with pytest.raises(ValueError, match="cloud arm"):
        parse_arm("claude:claude-sonnet-5-5@ctx=131072")


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """A ClaudeCodeAgent whose launches are recorded, not run."""
    token = tmp_path / "token"
    token.write_text("sk-ant-oat01-test\n")
    agent = ClaudeCodeAgent("claude-sonnet-5-5", home=tmp_path / "home", token_file=token)
    seen: list[dict] = []

    def launch(cmd, cwd, env, timeout, writable=(), hidden=(), readable=(), ports=None,
               loopback=None):
        home = Path(writable[0])
        seen.append({"cmd": cmd, "env": env, "home_files": sorted(p.name for p in home.iterdir()),
                     "ports": ports, "hidden": hidden, "readable": readable,
                     "loopback": loopback})
        (home / "left-by-an-attempt").write_text("x")
        return seen[-1].get("out", _CLAUDE_EVENTS), False, 4.0

    monkeypatch.setattr(agents, "_launch", launch)
    monkeypatch.setattr(agents, "has_sandbox", lambda: True)
    monkeypatch.setattr(agents, "linux_sandbox", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda n: "/bin/" + n)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-would-bill-the-api")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("https_proxy", "http://somewhere-else:8080")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_not-for-anthropic")
    return agent, seen


def test_claude_runs_clean_through_the_proxy(fake_claude, tmp_path):
    agent, seen = fake_claude
    run = agent.attempt(tmp_path, "Fix it.", test_cmd=["pytest", "t.py"], timeout=60)
    [call] = seen
    cmd, env = call["cmd"], call["env"]
    assert cmd[1:3] == ["-p", "Fix it.\n\nYou can check your work by running: pytest t.py"]
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5"
    assert cmd[cmd.index("--disallowedTools") + 1] == "WebFetch,WebSearch"
    assert "--strict-mcp-config" in cmd
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-test"
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env  # bills the token's login
    assert "GITHUB_TOKEN" not in env  # only what's on the list comes along
    assert "https_proxy" not in env and env["HTTPS_PROXY"].startswith("http://127.0.0.1:")
    assert env["HOME"] == str(tmp_path / "home" / "attempt")
    # The whole home hidden; the proxy's port the only one open.
    assert Path.home().resolve() in call["hidden"]
    assert call["loopback"] == [int(env["HTTPS_PROXY"].rsplit(":", 1)[1])]
    assert run.cost_usd == 0.4321 and run.extra["egress_tunnels"] == 0


def test_each_claude_attempt_starts_from_an_empty_home(fake_claude, tmp_path):
    agent, seen = fake_claude
    for _ in range(2):
        agent.attempt(tmp_path, "p", test_cmd=None, timeout=60)
    assert [c["home_files"] for c in seen] == [[], []]


def test_a_cloud_attempt_keeps_what_it_runs_when_the_home_is_hidden(tmp_path, monkeypatch):
    home = tmp_path / "home"
    node = home / ".nvm" / "versions" / "node" / "v22" / "bin" / "node"
    cli = home / ".nvm" / "versions" / "node" / "v22" / "lib" / "claude-code" / "cli.js"
    venv = home / ".virtualenvs" / "proj"
    for f in (node, cli, venv / "bin" / "python"):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("")
    cli.write_text("#!/usr/bin/env node\nconsole.log(1)\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(shutil, "which", lambda n: str(node) if n == "node" else None)
    hidden, readable = ClaudeCodeAgent.confine(cli, [], [venv])
    assert hidden == [home.resolve()]
    assert cli.parent in readable                       # the npm package
    assert node.parent.parent.resolve() in readable     # the node it runs on
    assert venv in readable                             # the test venv, outside any repo


def test_claude_cut_off_by_the_limit_timed_out_and_did_not_crash(fake_claude, tmp_path,
                                                                 monkeypatch):
    agent, _ = fake_claude
    monkeypatch.setattr(agents, "_launch", lambda *a, **k: ('{"type":"system"}\n', True, 60.0))
    run = agent.attempt(tmp_path, "p", test_cmd=None, timeout=60)
    assert run.timed_out and run.crashed == ""


def test_claude_without_a_token_says_how_to_make_one(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "has_sandbox", lambda: True)
    monkeypatch.setattr(shutil, "which", lambda n: "/bin/" + n)
    agent = ClaudeCodeAgent("claude-sonnet-5-5", home=tmp_path, token_file=tmp_path / "none")
    with pytest.raises(FileNotFoundError, match="claude setup-token"):
        agent.attempt(tmp_path, "p", test_cmd=None, timeout=1)


def test_claude_never_runs_without_the_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "has_sandbox", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda n: "/bin/" + n)
    with pytest.raises(RuntimeError, match="bubblewrap"):
        ClaudeCodeAgent("claude-sonnet-5-5", home=tmp_path).attempt(
            tmp_path, "p", test_cmd=None, timeout=1)


def test_a_cloud_only_manifest_never_asks_ollama(monkeypatch):
    def down():
        raise AssertionError("asked Ollama")
    monkeypatch.setattr(manifest.ollama, "pulled_models", down)
    assert manifest.models([ClaudeCodeAgent("claude-sonnet-5-5")]) == {
        "cloud": {"claude-sonnet-5-5": "Anthropic"}}


# ── end to end, in the real sandbox ───────────────────────────────────────────


@pytest.mark.skipif(not has_sandbox(), reason="needs sandbox-exec or bubblewrap")
def test_inside_the_sandbox_only_the_proxy_leads_out(tmp_path, monkeypatch):
    """A stand-in `claude` that tries the proxy to an allowed host, the proxy to
    a refused one, and a direct connection. Only the first gets anywhere."""
    bindir, work = tmp_path / "bin", tmp_path / "work"
    bindir.mkdir()
    work.mkdir()
    script = bindir / "claude"
    script.write_text(f"""#!{sys.executable} -I
import json, os, socket
probe = json.load(open("probe.json"))  # the environment carries nothing of the test's
proxy = os.environ["HTTPS_PROXY"].removeprefix("http://")
host, port = proxy.rsplit(":", 1)
def via_proxy(target):
    s = socket.create_connection((host, int(port)), 5)
    s.sendall(f"CONNECT {{target}} HTTP/1.1\\r\\n\\r\\n".encode())
    status = s.recv(4096).split(b"\\r\\n")[0].decode()
    if status.endswith("200 Connection Established"):
        s.sendall(b"ping"); status += " " + s.recv(4).decode()
    s.close(); return status
def direct():
    try:
        socket.create_connection(("1.1.1.1", 443), 5).close(); return "open"
    except OSError as e:
        return "blocked"
def secret():
    try:
        return open(probe["secret"]).read() or "empty"
    except OSError:
        return "blocked"
print(json.dumps({{"type": "probe", "allowed": via_proxy(probe["allowed"]),
                   "refused": via_proxy("github.com:443"), "direct": direct(),
                   "secret": secret(), "token": os.environ.get("GITHUB_TOKEN", "absent")}}))
print(json.dumps({{"type": "result", "is_error": False, "num_turns": 1, "total_cost_usd": 0.01,
                   "usage": {{"input_tokens": 1, "output_tokens": 1}}}}))
""")
    script.chmod(0o755)
    token = tmp_path / "token"
    token.write_text("t")
    home = tmp_path / "realhome"  # stands in for the user's home
    home.mkdir()
    (home / "notes.txt").write_text("PRIVATE")
    with Echo() as echo:
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
        (work / "probe.json").write_text(json.dumps({"allowed": f"127.0.0.1:{echo.port}",
                                                     "secret": str(home / "notes.txt")}))
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
        agent = ClaudeCodeAgent("claude-sonnet-5-5", home=tmp_path / "home", token_file=token)
        agent.hosts = (f"127.0.0.1:{echo.port}",)
        # The stand-in runs on this interpreter: readable, like a bench's test venv.
        run = agent.attempt(work, "p", test_cmd=None, timeout=60,
                            readable=[Path(sys.executable).parent.parent])
    assert run.crashed == "", run.log[-1500:]
    probe = next(json.loads(line) for line in run.log.splitlines() if line.startswith('{"type": "probe"'))
    assert probe == {"type": "probe", "allowed": "HTTP/1.1 200 Connection Established ping",
                     "refused": "HTTP/1.1 403 Forbidden", "direct": "blocked",
                     "secret": probe["secret"], "token": "absent"}
    assert probe["secret"] in ("blocked", "empty")  # macOS denies it; bubblewrap empties it
    assert run.extra["egress_tunnels"] == 1 and run.extra["egress_refused"] == 1
    assert run.cost_usd == 0.01 and run.crashed == ""
