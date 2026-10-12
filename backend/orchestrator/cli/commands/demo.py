"""mahoraga demo: see the product on a recorded run before you have one of your own.

Copies the packaged demo bench (backend/orchestrator/demo) to ~/.mahoraga-v2/demo,
fresh each time, and serves the app against that copy. The engine's bench root
is pointed at the copy and never at ~/.mahoraga-v2/agentbench, so a demo cannot
read, change or hide a bench of your own.
"""
from __future__ import annotations

import os
from pathlib import Path

import typer

from backend.orchestrator import demo as demo_data


def demo(
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    headless: bool = typer.Option(False, "--headless", help="API only: mount no UI"),
) -> None:
    """Explore Mahoraga on a recorded run (18 real commits, two local models), not on your data."""
    try:
        root = demo_data.install(Path.home() / ".mahoraga-v2" / "demo")
    except FileExistsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    # Set before uvicorn imports the app, which reads them.
    os.environ["MAHORAGA_AGENTBENCH_ROOT"] = str(root)
    os.environ["MAHORAGA_DEMO"] = "1"
    if headless:
        os.environ["MAHORAGA_HEADLESS"] = "1"

    info = demo_data.meta(root)
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"Mahoraga demo: a recorded run ({info['dates']}, {info['machine']}), not your data.")
    typer.echo(f"Bench root: {root}")
    typer.echo(f"API at {url}/api/engine" if headless else f"Open {url}")

    import uvicorn
    uvicorn.run("backend.orchestrator.service.app:app", host="127.0.0.1", port=port)
