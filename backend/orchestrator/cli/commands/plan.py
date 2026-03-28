from __future__ import annotations
import typer
import httpx

app = typer.Typer(name="plan", help="Manage plans")
from . import BASE_URL as _BASE


@app.command("create")
def plan_create(
    mission_id: str = typer.Option(..., "--mission", "-m", help="Mission ID"),
    mode: str = typer.Option("plan_first", "--mode", help="plan_first | direct | review_loop"),
):
    """Create a plan for a mission and start a run."""
    payload = {"mission_id": mission_id, "mode": mode}
    resp = httpx.post(f"{_BASE}/plans", json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    typer.echo(f"Plan created: {data['plan_id']}  Run: {data['run_id']}  (status: {data['run_status']})")


@app.command("show")
def plan_show(plan_id: str):
    resp = httpx.get(f"{_BASE}/plans/{plan_id}", timeout=10)
    resp.raise_for_status()
    typer.echo(resp.text)


@app.command("list")
def plan_list(mission_id: str = typer.Option(None, "--mission", "-m")):
    url = f"{_BASE}/plans" + (f"?mission_id={mission_id}" if mission_id else "")
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    for p in resp.json():
        typer.echo(f"{p['id'][:8]}  v{p['version']}  {p['status']:<10}  mission={p['mission_id'][:8]}")


@app.command("approve")
def plan_approve(plan_id: str):
    resp = httpx.post(f"{_BASE}/plans/{plan_id}/approve", timeout=10)
    resp.raise_for_status()
    typer.echo(f"Plan {plan_id} approved.")
