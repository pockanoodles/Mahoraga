from __future__ import annotations
import typer
import httpx

from . import BASE_URL as _BASE


def _require_server() -> None:
    """Print a helpful message and exit when the server is unreachable."""
    typer.echo(
        "Mahoraga server is not running. Start it with:\n\n"
        "  orch serve\n\n"
        "or:\n\n"
        "  cd ~/Projects/Mahoraga && uvicorn backend.orchestrator.service.app:app --port 8000",
        err=True,
    )
    raise typer.Exit(code=1)


def status(run_id: str = typer.Argument(None)):
    """Show current status of a run or all active runs."""
    try:
        if run_id:
            resp = httpx.get(f"{_BASE}/runs/{run_id}", timeout=10)
        else:
            resp = httpx.get(f"{_BASE}/runs?status=active", timeout=10)
        resp.raise_for_status()
        typer.echo(resp.text)
    except httpx.ConnectError:
        _require_server()


def events(run_id: str, task_id: str = typer.Option(None, "--task")):
    """Show event log for a run or specific task."""
    if task_id:
        resp = httpx.get(f"{_BASE}/tasks/{task_id}/events", timeout=10)
    else:
        resp = httpx.get(f"{_BASE}/runs/{run_id}/events", timeout=10)
    resp.raise_for_status()
    for ev in resp.json():
        typer.echo(f"{ev['ts']:.0f}  {ev['type']:<30}  {ev.get('task_id', '')[:8]}")


def approve(task_id: str, run_id: str = typer.Option(..., "--run", "-r")):
    """Approve a blocked task."""
    resp = httpx.post(f"{_BASE}/tasks/{task_id}/approve", json={"run_id": run_id}, timeout=10)
    resp.raise_for_status()
    typer.echo(f"Task {task_id} approved.")


def reject(task_id: str, run_id: str = typer.Option(..., "--run", "-r")):
    """Reject a blocked task."""
    resp = httpx.post(f"{_BASE}/tasks/{task_id}/reject", json={"run_id": run_id}, timeout=10)
    resp.raise_for_status()
    typer.echo(f"Task {task_id} rejected.")
