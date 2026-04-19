from __future__ import annotations
from typing import Optional
import httpx
import typer

BASE_URL = "http://localhost:8001"

app = typer.Typer(name="rankings", help="Agent rankings", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def rankings(
    ctx: typer.Context,
    bucket: Optional[str] = typer.Option(None, "--bucket", "-b", help="Filter by task bucket"),
    difficulty: Optional[str] = typer.Option(None, "--difficulty", "-d", help="Filter by difficulty"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Show only one agent"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    refresh: bool = typer.Option(False, "--refresh", "-r", help="Rebuild rankings first"),
) -> None:
    """Show local agent rankings (from live routing history + benchmark harness)."""
    if ctx.invoked_subcommand is not None:
        return

    params: dict = {"limit": limit, "refresh": str(refresh).lower()}
    if bucket:
        params["bucket"] = bucket
    if difficulty:
        params["difficulty"] = difficulty
    if agent:
        params["agent"] = agent

    try:
        r = httpx.get(f"{BASE_URL}/api/rankings", params=params, timeout=30.0)
        r.raise_for_status()
    except httpx.ConnectError:
        typer.echo("Cannot connect to Mahoraga server. Is it running?", err=True)
        raise typer.Exit(1)

    data = r.json()
    rows = data["rankings"]

    if json_output:
        import json
        print(json.dumps(data, indent=2))
        return

    if not rows:
        scope_label = f"{data['scope_type']}={data['scope_value']}"
        typer.echo(f"No rankings data for scope: {scope_label}. Run 'orch benchmark refresh' to populate.")
        return

    scope_label = data["scope_value"] if data["scope_value"] != "all" else "overall"
    typer.echo(f"\nLocal Rankings ({scope_label})\n")

    header = f"{'Rank':<6} {'Agent':<22} {'Win Rate':<10} {'95% CI':<16} {'Avg Latency':<14} {'Avg Reward':<12} {'N':<6}"
    typer.echo(header)
    typer.echo("-" * len(header))

    for row in rows:
        rank = row["rank"]
        a = row["agent"][:21]
        wr = f"{row['win_rate']:.2f}" if row.get("win_rate") is not None else "n/a"
        ci = f"{row.get('ci_low', 0):.2f}–{row.get('ci_high', 0):.2f}" if row.get("ci_low") is not None else "n/a"
        lat_ms = row.get("avg_latency_ms")
        lat = f"{lat_ms/1000:.1f}s" if lat_ms else "n/a"
        rwd = f"{row['avg_reward']:.2f}" if row.get("avg_reward") is not None else "n/a"
        n = row.get("sample_count", 0)
        typer.echo(f"{rank:<6} {a:<22} {wr:<10} {ci:<16} {lat:<14} {rwd:<12} {n:<6}")

    if verbose:
        typer.echo("\n(verbose: source breakdown coming in --refresh mode)")
    typer.echo()
