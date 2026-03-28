from __future__ import annotations
import typer
import httpx

app = typer.Typer(name="run", help="Manage runs")
_BASE = "http://localhost:8001"


@app.command("start")
def run_start(plan_id: str):
    """Start execution of an approved plan."""
    resp = httpx.post(f"{_BASE}/runs/{plan_id}/start", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    typer.echo(f"Run {data['run_id']} started.")


@app.command("show")
def run_show(run_id: str):
    resp = httpx.get(f"{_BASE}/runs/{run_id}", timeout=10)
    resp.raise_for_status()
    typer.echo(resp.text)


@app.command("list")
def run_list():
    resp = httpx.get(f"{_BASE}/runs", timeout=10)
    resp.raise_for_status()
    for r in resp.json():
        typer.echo(f"{r['id'][:8]}  {r['status']:<12}  plan={r['plan_id'][:8]}")


@app.command("cancel")
def run_cancel(run_id: str):
    resp = httpx.delete(f"{_BASE}/runs/{run_id}", timeout=10)
    resp.raise_for_status()
    typer.echo(f"Run {run_id} cancelled.")
