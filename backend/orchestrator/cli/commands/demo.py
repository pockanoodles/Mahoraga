"""mahoraga demo: see the product on a recorded run before you have one of your own.

Copies the packaged demo bench (backend/orchestrator/demo) to ~/.mahoraga-v2/demo,
fresh each time, and serves the app against that copy. The engine's bench root
is pointed at the copy and never at ~/.mahoraga-v2/agentbench, so a demo cannot
read, change or hide a bench of your own.

The rest of the engine's state (the router's decision log, the bandit, the cost
ledger) lives under ~/.mahoraga-v2 by way of the home directory, so the demo
server also runs with HOME set to a fresh scratch directory. Without that, the
router page of a demo would show the user's own log under a banner saying none
of it was theirs. The scratch home holds no Claude login and the cascade is off,
so nothing a demo does can reach the cloud.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from backend.orchestrator import demo as demo_data


def demo(
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    headless: bool = typer.Option(False, "--headless", help="API only: mount no UI"),
) -> None:
    """Explore Mahoraga on a recorded run (18 real commits, two local models), not on your data."""
    state = Path.home() / ".mahoraga-v2"
    try:
        root = demo_data.install(state / "demo")
        home = demo_data.scratch_home(state / "demo-home")
    except FileExistsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    env = {**os.environ, "MAHORAGA_AGENTBENCH_ROOT": str(root), "MAHORAGA_DEMO": "1",
           "HOME": str(home), "MAHORAGA_CASCADE": "off"}
    if headless:
        env["MAHORAGA_HEADLESS"] = "1"

    info = demo_data.meta(root)
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"Mahoraga demo: a recorded run ({info['dates']}, {info['machine']}), not your data.")
    typer.echo(f"Bench root: {root}")
    typer.echo(f"API at {url}/api/engine" if headless else f"Open {url}")

    # A fresh interpreter, not uvicorn.run() in this one: this process imported
    # most of the engine to parse the command line, and modules that resolve
    # ~/.mahoraga-v2 at import time would keep the real home. Exec'ing the server
    # makes every path resolve under the scratch home.
    sys.stdout.flush()
    os.execve(sys.executable, [sys.executable, "-m", "uvicorn", "backend.orchestrator.service.app:app",
                               "--host", "127.0.0.1", "--port", str(port)], env)
