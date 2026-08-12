"""orch service — manage Mahoraga as a macOS launchd background service.

Configuration note: a launchd job does NOT inherit your shell's environment. It
starts with a minimal PATH and no user variables at all, which silently breaks
two things that work fine under a manual `orch serve`:

  - every MAHORAGA_* knob (exec gate, reward judge, escalation cascade, memory
    mode) is stuck at its code default, with no way to set it;
  - `claude`, `ollama`, and anything else in ~/.local/bin or Homebrew is
    unresolvable, so the escalation arm degrades to serving local answers
    without an error anyone would notice.

So `install` bakes an EnvironmentVariables block into the plist: a PATH that
covers the venv, ~/.local/bin, and Homebrew, plus any KEY=VALUE lines from
`~/.mahoraga-v2/service.env`. Edit that file and re-run `orch service install`
to change the daemon's configuration.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

import typer

app = typer.Typer(help="Manage Mahoraga as a background service (macOS launchd).")

_LABEL = "com.mahoraga.orch"
_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"
_PROJECT_ROOT = Path(__file__).parents[4]
_ORCH_BIN = _PROJECT_ROOT / ".venv" / "bin" / "orch"
_LOG_DIR = Path.home() / ".mahoraga-v2"
_LOG_FILE = _LOG_DIR / "server.log"
_SERVICE_ENV_FILE = _LOG_DIR / "service.env"

_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{orch_bin}</string>
        <string>serve</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{workdir}</string>

    <key>RunAtLoad</key>
    <true/>

    <!-- SuccessfulExit: false means "restart after a crash, not after a
         deliberate stop." Plain `<true/>` respawns unconditionally, which
         made `orch service stop` cosmetic — launchd relaunched the process
         within ~1s of the SIGTERM regardless of why it exited. -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <!-- launchd gives a job no user environment and a minimal PATH. Without
         this block the daemon cannot see any MAHORAGA_* setting and cannot
         resolve `claude`/`ollama`, so the escalation cascade silently serves
         local answers. Sourced from ~/.mahoraga-v2/service.env at install. -->
    <key>EnvironmentVariables</key>
    <dict>
{env_entries}
    </dict>

    <key>StandardOutPath</key>
    <string>{log}</string>

    <key>StandardErrorPath</key>
    <string>{log}</string>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
"""

# PATH the daemon runs with. Covers the venv (orch, python), ~/.local/bin (the
# `claude` CLI the escalation arm spawns), and Homebrew (`ollama`), then the
# system defaults. launchd's own default is /usr/bin:/bin:/usr/sbin:/sbin.
_DAEMON_PATH_DIRS = [
    str(_PROJECT_ROOT / ".venv" / "bin"),
    str(Path.home() / ".local" / "bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]


def _parse_env_file(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines from `path`; blank lines and `#` comments ignored.

    Deliberately not a full dotenv parser — no interpolation, no export
    keyword, no multi-line values. The daemon's config surface is a handful of
    MAHORAGA_* flags, and a surprising parser is worse than a dumb one.
    """
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            env[key] = value.strip().strip('"').strip("'")
    return env


def _build_environment() -> dict[str, str]:
    """The daemon's environment: a working PATH plus the user's service.env.

    HOME is passed through explicitly because state paths (`~/.mahoraga-v2`)
    resolve from it, and service.env wins over the defaults so PATH itself can
    be overridden if a roster needs something exotic.
    """
    env = {
        "PATH": ":".join(_DAEMON_PATH_DIRS),
        "HOME": str(Path.home()),
    }
    env.update(_parse_env_file(_SERVICE_ENV_FILE))
    return env


def _render_env_entries(env: dict[str, str]) -> str:
    """Render an env dict as indented plist <key>/<string> pairs, XML-escaped."""
    return "\n".join(
        f"        <key>{_xml_escape(k)}</key>\n        <string>{_xml_escape(v)}</string>"
        for k, v in sorted(env.items())
    )


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def _is_loaded() -> bool:
    result = _launchctl("list", _LABEL)
    return result.returncode == 0


def _load_job() -> tuple[bool, str]:
    """Load the job, clearing launchd's sticky disabled flag first.

    Two traps this exists to close, both of which produced a CLI that reported
    success while the daemon was not running:

      - `launchctl load` prints "Load failed: 5: Input/output error" and still
        exits 0, so the return code cannot be trusted. Verify with `list`.
      - a label can be marked disabled in launchd's persistent database, which
        survives unload/reinstall and even a reboot. Every subsequent load
        fails with that same opaque errno 5 until `launchctl enable` clears it.

    Returns (loaded, detail).
    """
    _launchctl("enable", f"gui/{os.getuid()}/{_LABEL}")
    result = _launchctl("load", str(_PLIST_PATH))
    if _is_loaded():
        return True, ""
    detail = (result.stderr or result.stdout or "").strip()
    return False, detail or "launchctl reported no error but the job is not listed"


@app.command("install")
def install() -> None:
    """Install and start Mahoraga as a login-persistent background service."""
    if not _ORCH_BIN.exists():
        typer.echo(f"orch binary not found at {_ORCH_BIN}. Run `pip install -e .` first.", err=True)
        raise typer.Exit(1)

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    environment = _build_environment()
    plist_content = _PLIST_TEMPLATE.format(
        label=_LABEL,
        orch_bin=str(_ORCH_BIN),
        workdir=str(_PROJECT_ROOT),
        log=str(_LOG_FILE),
        env_entries=_render_env_entries(environment),
    )
    _PLIST_PATH.write_text(plist_content)
    typer.echo(f"Wrote plist → {_PLIST_PATH}")

    settings = {k: v for k, v in environment.items() if k.startswith("MAHORAGA_")}
    if settings:
        typer.echo(f"Daemon settings from {_SERVICE_ENV_FILE}:")
        for key, value in sorted(settings.items()):
            typer.echo(f"  {key}={value}")
    else:
        typer.echo(
            f"No MAHORAGA_* settings found — create {_SERVICE_ENV_FILE} "
            "(KEY=VALUE per line) and re-run to configure the daemon."
        )

    if _is_loaded():
        _launchctl("unload", str(_PLIST_PATH))

    loaded, detail = _load_job()
    if not loaded:
        typer.echo(f"launchctl load failed: {detail}", err=True)
        raise typer.Exit(1)

    typer.echo("Service installed and started.")
    typer.echo(f"Logs → {_LOG_FILE}")
    typer.echo("Toggle:  orch service stop / orch service start")
    typer.echo("Remove:  orch service uninstall")


@app.command("uninstall")
def uninstall() -> None:
    """Stop and remove the background service."""
    if _is_loaded():
        _launchctl("unload", str(_PLIST_PATH))
        typer.echo("Service unloaded.")

    if _PLIST_PATH.exists():
        _PLIST_PATH.unlink()
        typer.echo(f"Removed {_PLIST_PATH}")
    else:
        typer.echo("No plist found — nothing to remove.")


@app.command("start")
def start() -> None:
    """Start the service (if installed and not running)."""
    if not _PLIST_PATH.exists():
        typer.echo("Service not installed. Run `orch service install` first.", err=True)
        raise typer.Exit(1)
    # `stop` below unloads the job entirely, so the common case here is
    # "not loaded" — load it fresh (RunAtLoad + KeepAlive bring it up).
    # Fall back to `launchctl start` only if it's somehow loaded-but-stopped.
    if not _is_loaded():
        loaded, detail = _load_job()
        if not loaded:
            typer.echo(f"Failed to start: {detail}", err=True)
            raise typer.Exit(1)
    else:
        result = _launchctl("start", _LABEL)
        if result.returncode != 0:
            typer.echo(
                f"Failed to start: {result.stderr.strip() or 'already running?'}", err=True
            )
            raise typer.Exit(1)
    typer.echo("Service started.")


@app.command("stop")
def stop() -> None:
    """Stop the service without uninstalling it."""
    # `launchctl stop` sends SIGTERM but leaves the job loaded — the process
    # is killed BY the signal rather than exiting cleanly, so LastExitStatus
    # is the signal number, which KeepAlive's SuccessfulExit:false treats as
    # a crash and respawns. Unloading removes the job from launchd entirely,
    # so KeepAlive never gets a chance to act. This is why `orch service
    # stop` used to be immediately undone by launchd — see brain/state/findings.md Era 8.
    if not _is_loaded():
        typer.echo("Service already stopped.")
        return
    result = _launchctl("unload", str(_PLIST_PATH))
    if result.returncode != 0:
        typer.echo(f"Failed to stop: {result.stderr.strip() or 'not running?'}", err=True)
        raise typer.Exit(1)
    typer.echo("Service stopped. Run `orch service start` to resume.")


@app.command("status")
def status() -> None:
    """Show whether the service is running and tail recent logs."""
    if not _PLIST_PATH.exists():
        typer.echo("Not installed.")
        return

    result = _launchctl("list", _LABEL)
    if result.returncode != 0:
        typer.echo("Installed but not loaded (stopped).")
    else:
        pid = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith('"PID"'):
                # format: "PID" = 12345;
                parts = stripped.split("=", 1)
                if len(parts) == 2:
                    pid = parts[1].strip().rstrip(";").strip()
                break
        if pid and pid != "-":
            typer.echo(f"Running  PID {pid}")
        else:
            typer.echo("Loaded but not running (crashed / throttled?).")

    if _LOG_FILE.exists():
        typer.echo(f"\n── Last 20 lines of {_LOG_FILE} ──")
        result = subprocess.run(
            ["tail", "-n", "20", str(_LOG_FILE)], capture_output=True, text=True, check=False
        )
        typer.echo(result.stdout.rstrip())
