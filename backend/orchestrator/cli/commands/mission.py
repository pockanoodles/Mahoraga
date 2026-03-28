from __future__ import annotations
import typer
import httpx
from typing import Optional

app = typer.Typer(name="mission", help="Manage missions")
from . import BASE_URL as _BASE


@app.command("new")
def mission_new(
    title: str = typer.Option(..., prompt=True),
    goal: str = typer.Option(..., prompt=True),
    background: str = typer.Option("", prompt=False),
    success_condition: str = typer.Option("", prompt=False),
):
    """Create a new mission."""
    payload = {
        "title": title,
        "goal": goal,
        "background": background,
        "success_condition": success_condition,
    }
    resp = httpx.post(f"{_BASE}/missions", json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    typer.echo(f"Mission created: {data['id']}")


@app.command("show")
def mission_show(mission_id: str):
    """Show a mission."""
    resp = httpx.get(f"{_BASE}/missions/{mission_id}", timeout=10)
    resp.raise_for_status()
    typer.echo(resp.text)


@app.command("list")
def mission_list():
    """List all missions."""
    resp = httpx.get(f"{_BASE}/missions", timeout=10)
    resp.raise_for_status()
    for m in resp.json():
        typer.echo(f"{m['id'][:8]}  {m['status']:<10}  {m['title']}")
