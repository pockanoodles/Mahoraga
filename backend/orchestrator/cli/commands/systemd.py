"""A `service.Job` as a systemd user unit: the Linux side of `service.py`.

The unit keeps the launchd job's contract, so `orch service` and `mahoraga
audit service` mean the same thing on either machine:

  - its environment is written into it (the PATH that finds orch, claude and
    opencode, HOME, MAHORAGA_HOME when set, and ~/.mahoraga-v2/service.env),
    because a user unit, like a launchd job, starts without the login shell's;
  - it restarts after a crash and not after a deliberate stop
    (Restart=on-failure, launchd's KeepAlive SuccessfulExit=false);
  - its output goes to the job's log file, so status and the docs name one
    place on both machines.

The worker exits 130 when stopped, after killing any agent it started. That is
listed as success, so a stop never leaves the unit failed, which is what a
health check watching user units (ops on the OptiPlex) would report.

A user unit starts at boot, before anyone logs in, only when the user has
linger on. Install says so when it's off.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from .service import Job


def unit_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"


def unit_name(label: str) -> str:
    """com.mahoraga.audit-worker -> mahoraga-audit-worker.service"""
    return label.removeprefix("com.").replace(".", "-") + ".service"


def unit_path(job: Job) -> Path:
    return unit_dir() / unit_name(job.label)


def _path(p: Path) -> str:
    """A path setting: taken to the end of the line, specifiers expanded."""
    return str(p).replace("%", "%%")


def _quoted(value: str, *, exec_line: bool = False) -> str:
    """A double-quoted word. ExecStart also expands $VAR, so `$` doubles there."""
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return '"' + (value.replace("$", "$$") if exec_line else value) + '"'


def render_unit(job: Job, orch_bin: Path, environment: dict[str, str], cli: str) -> str:
    if any("\n" in s for s in (*job.args, str(job.workdir), *environment.values())):
        raise ValueError("a unit setting can't hold a newline")
    return "\n".join([
        "[Unit]",
        f"Description=Mahoraga: orch {' '.join(job.args)}",
        f"# Written by `{cli} install`; change service.env and install again.",
        "",
        "[Service]",
        "ExecStart=" + " ".join(_quoted(a, exec_line=True) for a in (str(orch_bin), *job.args)),
        f"WorkingDirectory={_path(job.workdir)}",
        *(f"Environment={_quoted(f'{k}={v}')}" for k, v in sorted(environment.items())),
        "Restart=on-failure",
        "RestartSec=10",
        "SuccessExitStatus=130 143",
        f"StandardOutput=append:{_path(job.log)}",
        f"StandardError=append:{_path(job.log)}",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                          check=False)


def _state(job: Job) -> dict[str, str]:
    out = _systemctl("show", unit_name(job.label), "-p", "ActiveState", "-p", "SubState",
                     "-p", "MainPID").stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def is_running(job: Job) -> bool:
    return _state(job).get("ActiveState") in ("active", "activating", "reloading")


def _fail(what: str, result: subprocess.CompletedProcess[str]) -> None:
    detail = (result.stderr or result.stdout or "").strip()
    typer.echo(f"systemctl {what} failed: {detail or f'exit {result.returncode}'}", err=True)
    raise typer.Exit(1)


def _linger_note() -> str | None:
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    r = subprocess.run(["loginctl", "show-user", user, "-p", "Linger", "--value"],
                       capture_output=True, text=True, check=False)
    if r.returncode == 0 and r.stdout.strip() == "no":
        return ("Linger is off, so this runs only while you're logged in. To start it at "
                "boot: loginctl enable-linger")
    return None


def install(job: Job, orch_bin: Path, environment: dict[str, str], cli: str,
            settle: float = 2.0) -> None:
    unit = unit_name(job.label)
    path = unit_path(job)
    job.log.parent.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_unit(job, orch_bin, environment, cli))
    typer.echo(f"Wrote unit → {path}")
    for step in (("daemon-reload",), ("enable", unit), ("restart", unit)):
        if (r := _systemctl(*step)).returncode:
            _fail(step[0], r)
    # `restart` returns once the process is spawned; one that dies at once
    # shows up a moment later, so look again before saying it runs.
    time.sleep(settle)
    if not is_running(job):
        typer.echo(f"{unit} is not running after the install; its log: {job.log}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Installed and started as the user unit {unit}.")
    typer.echo(f"Logs → {job.log}")
    typer.echo(f"Toggle:  {cli} stop / {cli} start")
    typer.echo(f"Remove:  {cli} uninstall")
    if note := _linger_note():
        typer.echo(note)


def uninstall(job: Job) -> None:
    unit = unit_name(job.label)
    if is_running(job) or unit_path(job).exists():
        _systemctl("disable", "--now", unit)
        typer.echo("Service stopped and disabled.")
    if unit_path(job).exists():
        unit_path(job).unlink()
        _systemctl("daemon-reload")
        typer.echo(f"Removed {unit_path(job)}")
    else:
        typer.echo("No unit found — nothing to remove.")


def start(job: Job, cli: str) -> None:
    if not unit_path(job).exists():
        typer.echo(f"Service not installed. Run `{cli} install` first.", err=True)
        raise typer.Exit(1)
    if (r := _systemctl("start", unit_name(job.label))).returncode:
        _fail("start", r)
    typer.echo("Service started.")


def stop(job: Job, cli: str) -> None:
    if not is_running(job):
        typer.echo("Service already stopped.")
        return
    if (r := _systemctl("stop", unit_name(job.label))).returncode:
        _fail("stop", r)
    typer.echo(f"Service stopped. Run `{cli} start` to resume.")


def status(job: Job) -> None:
    if not unit_path(job).exists():
        typer.echo("Not installed.")
        return
    state = _state(job)
    if state.get("ActiveState") == "active":
        typer.echo(f"Running  PID {state.get('MainPID', '?')}")
    else:
        typer.echo(f"Installed, {state.get('ActiveState', 'unknown')} "
                   f"({state.get('SubState', '?')}).")
    if job.log.exists():
        typer.echo(f"\n── Last 20 lines of {job.log} ──")
        out = subprocess.run(["tail", "-n", "20", str(job.log)], capture_output=True, text=True,
                             check=False)
        typer.echo(out.stdout.rstrip())
