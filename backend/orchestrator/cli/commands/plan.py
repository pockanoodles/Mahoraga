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


@app.command("generate")
def plan_generate(
    mission_id: str = typer.Option(..., "--mission", "-m", help="Mission ID to decompose"),
):
    """Auto-generate a task plan for a mission using the local Ollama planner."""
    typer.echo(f"Generating plan for mission {mission_id[:8]}...")
    try:
        resp = httpx.post(f"{_BASE}/missions/{mission_id}/generate", timeout=180.0)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        typer.echo(f"Error: {exc.response.status_code} — {exc.response.text}", err=True)
        raise typer.Exit(code=1)
    except httpx.ConnectError:
        typer.echo(f"Error: could not connect to {_BASE}. Is the service running?", err=True)
        raise typer.Exit(code=1)

    data = resp.json()
    typer.echo(f"Plan:  {data['plan_id']}")
    typer.echo(f"Run:   {data['run_id']}")
    typer.echo(f"\nTasks ({len(data['tasks'])}):")
    for i, task in enumerate(data["tasks"], 1):
        typer.echo(f"  {i}. [{task['id'][:8]}] {task['title']}")
        typer.echo(f"     Goal: {task['goal']}")


@app.command("approve")
def plan_approve(plan_id: str):
    resp = httpx.post(f"{_BASE}/plans/{plan_id}/approve", timeout=10)
    resp.raise_for_status()
    typer.echo(f"Plan {plan_id} approved.")
