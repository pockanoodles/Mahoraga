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

The same machinery runs the audit's queue worker (`mahoraga audit service`): a
`Job` names the label, the arguments after `orch`, and the log. On a machine
where the user has no GUI login (the Studio, reached over ssh, where someone
else is at the console), the GUI domain doesn't exist, so the job goes into the
user's background domain with `LimitLoadToSessionType = Background`. A job there
starts again after a reboot only once the user has a session; an ssh login is
enough.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

import typer

app = typer.Typer(help="Manage Mahoraga as a background service (macOS launchd).")

_LABEL = "com.mahoraga.orch"
_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"
_PROJECT_ROOT = Path(__file__).parents[4]
_LOG_DIR = Path.home() / ".mahoraga-v2"
_LOG_FILE = _LOG_DIR / "server.log"
_SERVICE_ENV_FILE = _LOG_DIR / "service.env"


def _orch_bin() -> Path:
    """`orch` beside the interpreter running this, so a pip or Homebrew
    install finds its own; the repo's venv otherwise."""
    beside = Path(sys.executable).parent / "orch"
    return beside if beside.exists() else _PROJECT_ROOT / ".venv" / "bin" / "orch"


@dataclass(frozen=True)
class Job:
    label: str
    args: tuple[str, ...]          # after `orch`
    log: Path
    workdir: Path = _PROJECT_ROOT
    env: dict[str, str] = field(default_factory=dict)   # on top of _build_environment()

    @property
    def plist(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{self.label}.plist"


DAEMON = Job(_LABEL, ("serve",), _LOG_FILE)

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
{args}
    </array>
{session}
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
    str(Path(sys.executable).parent),
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / ".opencode" / "bin"),  # the audit's opencode arm
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


def render_plist(job: Job) -> str:
    environment = {**_build_environment(), **job.env}
    return _PLIST_TEMPLATE.format(
        label=_xml_escape(job.label), orch_bin=_xml_escape(str(_orch_bin())),
        args="\n".join(f"        <string>{_xml_escape(a)}</string>" for a in job.args),
        session=("" if _domain().startswith("gui/") else
                 "\n    <key>LimitLoadToSessionType</key>\n    <string>Background</string>\n"),
        workdir=_xml_escape(str(job.workdir)), log=_xml_escape(str(job.log)),
        env_entries=_render_env_entries(environment),
    )


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def _domain() -> str:
    """gui/<uid> when the user is logged in at the console, else user/<uid>."""
    uid = os.getuid()
    try:
        r = subprocess.run(["launchctl", "print", f"gui/{uid}"], capture_output=True, text=True)
    except OSError:  # no launchctl: not macOS, nothing will load anyway
        return f"gui/{uid}"
    return f"gui/{uid}" if r.returncode == 0 else f"user/{uid}"


def _is_loaded(job: Job = DAEMON) -> bool:
    return _launchctl("print", f"{_domain()}/{job.label}").returncode == 0


def _load_job(job: Job = DAEMON) -> tuple[bool, str]:
    """Load the job, clearing launchd's sticky disabled flag first.

    Two traps this exists to close, both of which produced a CLI that reported
    success while the daemon was not running:

      - `launchctl load` prints "Load failed: 5: Input/output error" and still
        exits 0, so the return code cannot be trusted. Verify with `print`.
      - a label can be marked disabled in launchd's persistent database, which
        survives unload/reinstall and even a reboot. Every subsequent load
        fails with that same opaque errno 5 until `launchctl enable` clears it.

    Returns (loaded, detail).
    """
    domain = _domain()
    _launchctl("enable", f"{domain}/{job.label}")
    result = (_launchctl("load", str(job.plist)) if domain.startswith("gui/")
              else _launchctl("bootstrap", domain, str(job.plist)))
    if _is_loaded(job):
        return True, ""
    detail = (result.stderr or result.stdout or "").strip()
    return False, detail or "launchctl reported no error but the job is not listed"


def _unload(job: Job) -> subprocess.CompletedProcess[str]:
    domain = _domain()
    if domain.startswith("gui/"):
        return _launchctl("unload", str(job.plist))
    return _launchctl("bootout", f"{domain}/{job.label}")


def install_job(job: Job, cli: str = "orch service") -> None:
    orch = _orch_bin()
    if not orch.exists():
        typer.echo(f"orch binary not found at {orch}. Run `pip install -e .` first.", err=True)
        raise typer.Exit(1)
    job.log.parent.mkdir(parents=True, exist_ok=True)
    job.plist.parent.mkdir(parents=True, exist_ok=True)
    job.plist.write_text(render_plist(job))
    typer.echo(f"Wrote plist → {job.plist}")
    if _is_loaded(job):
        _unload(job)
    loaded, detail = _load_job(job)
    if not loaded:
        typer.echo(f"launchctl load failed: {detail}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Installed and started in {_domain()}.")
    typer.echo(f"Logs → {job.log}")
    typer.echo(f"Toggle:  {cli} stop / {cli} start")
    typer.echo(f"Remove:  {cli} uninstall")


def uninstall_job(job: Job) -> None:
    if _is_loaded(job):
        _unload(job)
        typer.echo("Service unloaded.")
    if job.plist.exists():
        job.plist.unlink()
        typer.echo(f"Removed {job.plist}")
    else:
        typer.echo("No plist found — nothing to remove.")


def start_job(job: Job, cli: str = "orch service") -> None:
    if not job.plist.exists():
        typer.echo(f"Service not installed. Run `{cli} install` first.", err=True)
        raise typer.Exit(1)
    # `stop` unloads the job entirely, so "not loaded" is the common case: load
    # it fresh and RunAtLoad + KeepAlive bring it up.
    if not _is_loaded(job):
        loaded, detail = _load_job(job)
        if not loaded:
            typer.echo(f"Failed to start: {detail}", err=True)
            raise typer.Exit(1)
    else:
        result = _launchctl("kickstart", f"{_domain()}/{job.label}")
        if result.returncode != 0:
            typer.echo(f"Failed to start: {result.stderr.strip() or 'already running?'}", err=True)
            raise typer.Exit(1)
    typer.echo("Service started.")


def stop_job(job: Job, cli: str = "orch service") -> None:
    # `launchctl stop` sends SIGTERM but leaves the job loaded: the process is
    # killed BY the signal rather than exiting cleanly, which KeepAlive's
    # SuccessfulExit:false treats as a crash and respawns. Unloading removes
    # the job from launchd entirely, so KeepAlive never acts. This is why
    # `orch service stop` used to be undone at once — brain/state/findings.md Era 8.
    if not _is_loaded(job):
        typer.echo("Service already stopped.")
        return
    result = _unload(job)
    if result.returncode != 0:
        typer.echo(f"Failed to stop: {result.stderr.strip() or 'not running?'}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Service stopped. Run `{cli} start` to resume.")


def status_job(job: Job) -> None:
    if not job.plist.exists():
        typer.echo("Not installed.")
        return
    result = _launchctl("print", f"{_domain()}/{job.label}")
    if result.returncode != 0:
        typer.echo("Installed but not loaded (stopped).")
    else:
        pid = next((line.split("=", 1)[1].strip() for line in result.stdout.splitlines()
                    if line.strip().startswith("pid =")), None)
        typer.echo(f"Running  PID {pid}" if pid else "Loaded but not running (crashed / throttled?).")
    if job.log.exists():
        typer.echo(f"\n── Last 20 lines of {job.log} ──")
        out = subprocess.run(["tail", "-n", "20", str(job.log)], capture_output=True, text=True,
                             check=False)
        typer.echo(out.stdout.rstrip())


@app.command("install")
def install() -> None:
    """Install and start Mahoraga as a login-persistent background service."""
    environment = _build_environment()
    settings = {k: v for k, v in environment.items() if k.startswith("MAHORAGA_")}
    if settings:
        typer.echo(f"Daemon settings from {_SERVICE_ENV_FILE}:")
        for key, value in sorted(settings.items()):
            typer.echo(f"  {key}={value}")
    else:
        typer.echo(f"No MAHORAGA_* settings found — create {_SERVICE_ENV_FILE} "
                   "(KEY=VALUE per line) and re-run to configure the daemon.")
    install_job(DAEMON)


@app.command("uninstall")
def uninstall() -> None:
    """Stop and remove the background service."""
    uninstall_job(DAEMON)


@app.command("start")
def start() -> None:
    """Start the service (if installed and not running)."""
    start_job(DAEMON)


@app.command("stop")
def stop() -> None:
    """Stop the service without uninstalling it."""
    stop_job(DAEMON)


@app.command("status")
def status() -> None:
    """Show whether the service is running and tail recent logs."""
    status_job(DAEMON)
