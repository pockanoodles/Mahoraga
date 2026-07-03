"""
`orch quarantine` — F5 quarantine inspector + manual operator hooks.

Subcommands
-----------

  orch quarantine list                          — current entries + probe progress
  orch quarantine clear --bucket B --agent A    — manual release
  orch quarantine add   --bucket B --agent A    — manual quarantine (operator override)
  orch quarantine events [--limit N]            — recent drift_events from DB

Reads from `~/.mahoraga-v2/quarantine.json` and the decisions DB. Manual
operations rewrite the state file in place; the running FastAPI server
will pick up the change on its next route() call (it reads the file on
each routing decision via `QuarantineManager.load`).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

import typer

from backend.orchestrator.routing.decision_log import _DEFAULT_DB_PATH
from backend.orchestrator.routing.quarantine import (
    QUARANTINE_STATE_PATH,
    QuarantineManager,
)


app = typer.Typer(
    name="quarantine",
    help="Inspect and manage F5 drift-driven agent quarantines.",
    no_args_is_help=True,
)


def _load_manager() -> QuarantineManager:
    return QuarantineManager.load(QUARANTINE_STATE_PATH)


@app.command("list")
def list_entries(
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List currently-quarantined cells with drift signal + probe state."""
    mgr = _load_manager()
    entries = [e.to_dict() for e in mgr.all_entries()]
    if json_out:
        typer.echo(json.dumps({"entries": entries}, indent=2))
        return
    if not entries:
        typer.echo("no active quarantines")
        return
    typer.echo("=== Active Quarantines ===")
    for e in entries:
        typer.echo(
            f"  {e['bucket']:>14s} / {e['agent']:<14s}  "
            f"σ={e['deviation_sigmas']:.2f}  "
            f"probes={e['probe_successes']}/{e['probe_attempts']}  "
            f"reason={e['reason_kind']}  since={e['quarantined_at']}"
        )


@app.command("clear")
def clear(
    bucket: str = typer.Option(..., "--bucket", help="Bucket name"),
    agent: str = typer.Option(..., "--agent", help="Agent name"),
) -> None:
    """Manually release a quarantined cell.

    Updates the persisted state file AND marks the corresponding
    drift_events rows as `manual_released` so the audit trail captures
    operator decisions distinctly from auto-recovery."""
    mgr = _load_manager()
    if not mgr.is_quarantined(bucket, agent):
        typer.echo(f"no quarantine for {bucket}/{agent}", err=True)
        raise typer.Exit(code=1)
    mgr.release(bucket, agent)
    mgr.save()

    # Also close out the drift_events row(s).
    conn = sqlite3.connect(str(_DEFAULT_DB_PATH))
    try:
        cur = conn.execute(
            "UPDATE drift_events SET resolution = 'manual_released' "
            "WHERE bucket = ? AND agent = ? AND resolution IS NULL",
            (bucket, agent),
        )
        conn.commit()
        rows = cur.rowcount or 0
    finally:
        conn.close()
    typer.echo(
        f"cleared {bucket}/{agent} (drift_events updated: {rows})"
    )


@app.command("add")
def add(
    bucket: str = typer.Option(..., "--bucket"),
    agent: str = typer.Option(..., "--agent"),
    reason: str = typer.Option(
        "manual", "--reason",
        help="Reason tag for the quarantine entry (free-form).",
    ),
) -> None:
    """Pre-emptively quarantine an agent without waiting for drift.

    Useful when you already know a service is down (rate limit, auth
    expiry, model swap) and want to stop routing to it immediately.
    """
    mgr = _load_manager()
    if mgr.is_quarantined(bucket, agent):
        typer.echo(f"{bucket}/{agent} already quarantined")
        return
    mgr.manual_quarantine(bucket, agent, reason=reason)
    mgr.save()
    typer.echo(f"quarantined {bucket}/{agent} ({reason})")


@app.command("events")
def events(
    limit: int = typer.Option(20, "--limit", "-n"),
    db: Path = typer.Option(_DEFAULT_DB_PATH),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show recent drift_events from the decisions DB.

    Includes resolved + unresolved events. The audit trail is what
    you'd grep when investigating "why did this agent get quarantined
    last week."
    """
    if not Path(db).exists():
        typer.echo("decisions DB not found", err=True)
        raise typer.Exit(code=1)
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT timestamp, bucket, agent, deviation_sigmas, "
            "       window_mean, historical_mean, resolution "
            "FROM drift_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        typer.echo(f"drift_events table not present: {exc}", err=True)
        raise typer.Exit(code=1)
    finally:
        conn.close()
    if json_out:
        items = [
            {
                "timestamp": r[0], "bucket": r[1], "agent": r[2],
                "deviation_sigmas": r[3], "window_mean": r[4],
                "historical_mean": r[5], "resolution": r[6],
            }
            for r in rows
        ]
        typer.echo(json.dumps(items, indent=2))
        return
    if not rows:
        typer.echo("no drift events on record")
        return
    typer.echo(f"=== Drift Events (last {len(rows)}) ===")
    for ts, bucket, agent, sigmas, wm, hm, resolution in rows:
        status = resolution or "ACTIVE"
        typer.echo(
            f"  {ts}  {bucket:>14s}/{agent:<14s}  σ={sigmas:.2f}  "
            f"window={wm:.3f}  hist={hm:.3f}  [{status}]"
        )
