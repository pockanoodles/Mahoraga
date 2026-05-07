"""
`orch budget` — F1 Budget Pacer inspector and resetter.

Subcommands
-----------

  orch budget status   — print pacer state (ceiling, λ, avg cost, headroom)
  orch budget reset    — clear persisted pacer state
  orch budget tune     — print current env-resolved config and explain it

Read-only by default. Reset requires no confirmation flag because the
pacer state self-rebuilds within `window` observations of normal
traffic — losing it is a small correction, not a data loss.
"""
from __future__ import annotations

import json
from pathlib import Path

import typer

from backend.orchestrator.routing.budget_pacer import (
    BUDGET_PACER_STATE_PATH,
    BudgetPacer,
    resolve_ceiling,
    resolve_eta,
    resolve_hard_limit,
    resolve_window,
)


app = typer.Typer(
    name="budget",
    help="Inspect and manage the F1 budget pacer.",
    no_args_is_help=True,
)


@app.command()
def status(
    state_path: Path = typer.Option(
        BUDGET_PACER_STATE_PATH, help="Path to pacer state JSON",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Print current pacer state. Reads from the persisted state file —
    safe to run while the FastAPI server is up."""
    if not state_path.exists():
        info = {
            "state_path": str(state_path),
            "status": "no state file yet",
            "ceiling": resolve_ceiling(),
            "hard_limit": resolve_hard_limit(),
            "window": resolve_window(),
            "eta": resolve_eta(),
        }
    else:
        pacer = BudgetPacer.load(state_path)
        info = {
            "state_path": str(state_path),
            **pacer.to_status_dict(),
        }
    if json_out:
        typer.echo(json.dumps(info, indent=2))
        return
    typer.echo("=== F1 Budget Pacer ===")
    for k, v in info.items():
        typer.echo(f"  {k:>14s}  {v}")


@app.command()
def reset(
    state_path: Path = typer.Option(BUDGET_PACER_STATE_PATH),
) -> None:
    """Delete the pacer state file. Next observation rebuilds it.

    Use this when you've changed BUDGET_CEILING significantly and want
    λ to start fresh rather than decay from the old value.
    """
    if state_path.exists():
        state_path.unlink()
        typer.echo(f"removed {state_path}")
    else:
        typer.echo(f"nothing to remove at {state_path}")


@app.command()
def tune() -> None:
    """Print env-resolved config and explain what each knob does.

    Useful for double-checking your environment before flipping
    MAHORAGA_ALLOW_PAID_ESCALATION=1.
    """
    typer.echo("=== F1 Budget Pacer config (env-resolved) ===")
    typer.echo(f"  MAHORAGA_BUDGET_CEILING={resolve_ceiling()}")
    typer.echo("    Rolling-average target. As avg cost approaches this,")
    typer.echo("    λ grows and the bandit shifts toward cheaper agents.")
    typer.echo(f"  MAHORAGA_BUDGET_HARD_LIMIT={resolve_hard_limit()}")
    typer.echo("    Absolute per-task cap. Agents whose estimated cost")
    typer.echo("    exceeds this are filtered before the bandit sees them.")
    typer.echo(f"  MAHORAGA_BUDGET_WINDOW={resolve_window()}")
    typer.echo("    Rolling-window size for avg cost calculation.")
    typer.echo(f"  MAHORAGA_BUDGET_ETA={resolve_eta()}")
    typer.echo("    Dual-ascent learning rate. Higher = faster λ response,")
    typer.echo("    higher chance of oscillation.")
