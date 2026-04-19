from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(name="eval", help="Routing evaluation commands", no_args_is_help=True)


@app.command("ab")
def ab_eval(
    tasks: Path = typer.Option(
        Path("eval/tasks/default_ab.yaml"),
        "--tasks", "-t",
        help="Path to task suite YAML",
    ),
    baseline: str = typer.Option(
        "ollama:general",
        "--baseline",
        help="Baseline agent for routing-OFF run. Format: fixed:<agent_id>",
    ),
    repeat: int = typer.Option(1, "--repeat", "-n", help="Number of repeat runs"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
) -> None:
    """Run A/B evaluation: compare routing OFF vs ON on a fixed task suite."""
    from ...eval.runner import run_ab_eval, print_ab_report

    if not tasks.exists():
        typer.echo(f"Task suite not found: {tasks}", err=True)
        raise typer.Exit(1)

    agent = baseline.removeprefix("fixed:")

    if not json_output:
        typer.echo(f"Running A/B eval on {tasks.name} ({repeat} repeat(s))...")

    summary = asyncio.run(run_ab_eval(tasks, baseline_agent=agent, repeat=repeat))
    print_ab_report(summary, json_output=json_output)
