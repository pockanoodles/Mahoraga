from __future__ import annotations
import typer
import httpx

app = typer.Typer(name="task", help="Inspect and manage tasks")
_BASE = "http://localhost:8001"


@app.command("list")
def task_list(
    run_id: str = typer.Option(None, "--run", "-r"),
    status: str = typer.Option(None, "--status", "-s"),
):
    url = f"{_BASE}/tasks"
    params: dict = {}
    if run_id:
        params["run_id"] = run_id
    if status:
        params["status"] = status
    resp = httpx.get(url, params=params, timeout=10)
    resp.raise_for_status()
    for t in resp.json():
        typer.echo(f"{t['id'][:8]}  {t['status']:<12}  {t['title']}")


@app.command("show")
def task_show(task_id: str):
    resp = httpx.get(f"{_BASE}/tasks/{task_id}", timeout=10)
    resp.raise_for_status()
    typer.echo(resp.text)


@app.command("retry")
def task_retry(task_id: str, run_id: str = typer.Option(..., "--run", "-r")):
    resp = httpx.post(f"{_BASE}/tasks/{task_id}/run", timeout=10)
    resp.raise_for_status()
    typer.echo(f"Task {task_id} queued for retry.")


@app.command("cancel")
def task_cancel(task_id: str, run_id: str = typer.Option(..., "--run", "-r")):
    resp = httpx.delete(f"{_BASE}/tasks/{task_id}", timeout=10)
    resp.raise_for_status()
    typer.echo(f"Task {task_id} cancelled.")
