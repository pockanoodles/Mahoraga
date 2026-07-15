"""orch service — manage Mahoraga as a macOS launchd background service."""
from __future__ import annotations

import subprocess
from pathlib import Path

import typer

app = typer.Typer(help="Manage Mahoraga as a background service (macOS launchd).")

_LABEL = "com.mahoraga.orch"
_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"
_PROJECT_ROOT = Path(__file__).parents[4]
_ORCH_BIN = _PROJECT_ROOT / ".venv" / "bin" / "orch"
_LOG_DIR = Path.home() / ".mahoraga-v2"
_LOG_FILE = _LOG_DIR / "server.log"

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

    <key>StandardOutPath</key>
    <string>{log}</string>

    <key>StandardErrorPath</key>
    <string>{log}</string>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
"""


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def _is_loaded() -> bool:
    result = _launchctl("list", _LABEL)
    return result.returncode == 0


@app.command("install")
def install() -> None:
    """Install and start Mahoraga as a login-persistent background service."""
    if not _ORCH_BIN.exists():
        typer.echo(f"orch binary not found at {_ORCH_BIN}. Run `pip install -e .` first.", err=True)
        raise typer.Exit(1)

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    plist_content = _PLIST_TEMPLATE.format(
        label=_LABEL,
        orch_bin=str(_ORCH_BIN),
        workdir=str(_PROJECT_ROOT),
        log=str(_LOG_FILE),
    )
    _PLIST_PATH.write_text(plist_content)
    typer.echo(f"Wrote plist → {_PLIST_PATH}")

    if _is_loaded():
        _launchctl("unload", str(_PLIST_PATH))

    result = _launchctl("load", str(_PLIST_PATH))
    if result.returncode != 0:
        typer.echo(f"launchctl load failed:\n{result.stderr}", err=True)
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
        result = _launchctl("load", str(_PLIST_PATH))
    else:
        result = _launchctl("start", _LABEL)
    if result.returncode != 0:
        typer.echo(f"Failed to start: {result.stderr.strip() or 'already running?'}", err=True)
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
