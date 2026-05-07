"""
`orch metrics live` — terminal view of the routing health snapshot.

Reads `routing_decisions.db` directly via `routing.observability.compute_health_snapshot`.
Designed for unattended operation — `--watch` refreshes every N seconds
so you can leave it open in a tmux pane.

Subcommands
-----------

  orch metrics live [--watch SECONDS] [--db PATH] [--json]

When --json is set, emits the snapshot as JSON each tick (suitable for
piping to jq or feeding a dashboard scraper). Default is a human-readable
text view.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import typer

from backend.orchestrator.routing.observability import (
    DEFAULT_DB_PATH,
    HealthSnapshot,
    compute_health_snapshot,
)


app = typer.Typer(
    name="metrics",
    help="Routing health metrics from the decisions DB.",
    no_args_is_help=True,
)


def _fmt(v: Optional[float], digits: int = 4) -> str:
    """Format a Maybe-float for table cells. None → '—'."""
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def _render_text(snap: HealthSnapshot) -> str:
    """Plain-text dashboard. Three columns of grouped sections, fixed widths."""
    lines: list[str] = []
    lines.append(f"=== Routing Health  @ {snap.timestamp}")
    lines.append(f"db: {snap.db_path}")
    lines.append("")
    lines.append(
        f"decisions: {snap.total_decisions:>6}   "
        f"with outcome: {snap.total_with_outcome:>6}   "
        f"strategies: {snap.by_strategy}"
    )
    lines.append("")

    # ── Reward windows ───────────────────────────────────────────────────────
    lines.append(f"{'window':<10} {'n':>6} {'outcome':>8} {'reward':>8} {'success':>8} {'lat_s':>8} {'cost':>8}")
    lines.append("-" * 64)
    for label, w in (
        ("rolling_100", snap.rolling_100),
        ("rolling_500", snap.rolling_500),
        ("all_time",    snap.all_time),
    ):
        lines.append(
            f"{label:<10} {w.n:>6} {w.n_with_outcome:>8} "
            f"{_fmt(w.mean_reward):>8} {_fmt(w.success_rate):>8} "
            f"{_fmt(w.mean_latency_s, 3):>8} {_fmt(w.mean_cost_usd, 5):>8}"
        )
    lines.append("")

    # ── Per-agent rollup ─────────────────────────────────────────────────────
    if snap.by_agent:
        lines.append(f"{'agent':<14} {'n':>6} {'outcome':>8} {'win':>6} {'reward':>8} {'lat_s':>8}")
        lines.append("-" * 56)
        for agent, st in sorted(snap.by_agent.items()):
            lines.append(
                f"{agent:<14} {st.n:>6} {st.n_with_outcome:>8} "
                f"{_fmt(st.win_rate, 3):>6} {_fmt(st.mean_reward):>8} "
                f"{_fmt(st.mean_latency_s, 3):>8}"
            )
        lines.append("")

    # ── Composer shadow telemetry ────────────────────────────────────────────
    cs = snap.composer_shadow
    if cs.n_with_data > 0:
        lines.append(
            f"composer shadow: n={cs.n_with_data}, "
            f"disagreements={cs.n_disagreements}, "
            f"agreed_reward={_fmt(cs.mean_reward_when_agreed)}, "
            f"disagreed_reward={_fmt(cs.mean_reward_when_disagreed)}, "
            f"counterfactual_delta={_fmt(cs.counterfactual_delta)}"
        )
    else:
        lines.append("composer shadow: no data yet (composer not enabled or no decisions logged)")

    # ── Escalation ───────────────────────────────────────────────────────────
    if snap.escalation.n_total_escalations > 0:
        lines.append(
            f"escalations: {snap.escalation.n_total_escalations} total "
            f"({_fmt(snap.escalation.rate_per_100, 2)} per 100), "
            f"by strategy: {snap.escalation.by_strategy}"
        )
    else:
        lines.append("escalations: 0 (escalation disabled or no triggers)")

    # ── Brain + A3 ───────────────────────────────────────────────────────────
    if snap.brain.n_total_with_data > 0:
        lines.append(
            f"brain: {snap.brain.n_with_hits}/{snap.brain.n_total_with_data} hits, "
            f"mean_top_sim={_fmt(snap.brain.mean_top_sim, 3)}"
        )
    else:
        lines.append("brain: no data (MAHORAGA_BRAIN_INTEGRATION_ENABLED off?)")

    if snap.a3.n_with_predictions > 0:
        lines.append(
            f"a3 calibration: n={snap.a3.n_with_predictions}, "
            f"MAE={_fmt(snap.a3.calibration_mae, 3)}"
        )
    else:
        lines.append("a3: no predictions logged yet")

    # ── Importance weights ───────────────────────────────────────────────────
    iw = snap.importance_weight
    lines.append(
        f"importance_weight: n={iw.n}, overrides={iw.n_overrides}, "
        f"mean={_fmt(iw.mean, 3)}, range=[{_fmt(iw.min, 3)}, {_fmt(iw.max, 3)}]"
    )

    # ── F2 Execution Pool ────────────────────────────────────────────────────
    ep = snap.execution_pool
    sat = " (saturated)" if ep.depth_norm >= 1.0 else ""
    lines.append(
        f"execution_pool: depth={ep.depth}/{ep.max_concurrent}  "
        f"queue_depth_norm={_fmt(ep.depth_norm, 3)}{sat}"
    )

    # ── F1 Budget Pacer ──────────────────────────────────────────────────────
    bp = snap.budget_pacer
    if bp.n_observed is not None:
        warn = "  ⚠ over ceiling" if bp.over_ceiling else ""
        lines.append(
            f"budget: avg=${_fmt(bp.avg_cost, 4)}/task  ceiling=${_fmt(bp.ceiling, 4)}  "
            f"hard_limit=${_fmt(bp.hard_limit, 4)}  λ={_fmt(bp.lambda_, 4)}  "
            f"n={bp.n_observed}{warn}"
        )
    else:
        lines.append("budget: pacer not yet observed any tasks")
    return "\n".join(lines)


@app.command()
def live(
    watch: Optional[float] = typer.Option(
        None, "--watch", "-w",
        help="Refresh every N seconds. Default: one-shot.",
    ),
    db: Path = typer.Option(DEFAULT_DB_PATH, help="Path to routing_decisions.db"),
    json_out: bool = typer.Option(
        False, "--json", help="Emit raw JSON instead of formatted text.",
    ),
) -> None:
    """Render the routing health snapshot. --watch loops every N seconds."""
    while True:
        snap = compute_health_snapshot(db_path=db)
        if json_out:
            typer.echo(json.dumps(snap.to_dict(), indent=2, default=str))
        else:
            # ANSI clear-screen on watch refresh; harmless on first paint.
            if watch is not None:
                typer.echo("\x1b[2J\x1b[H", nl=False)
            typer.echo(_render_text(snap))
        if watch is None:
            return
        try:
            time.sleep(watch)
        except KeyboardInterrupt:
            return


@app.command()
def snapshot(
    db: Path = typer.Option(DEFAULT_DB_PATH, help="Path to routing_decisions.db"),
) -> None:
    """One-shot JSON dump of the health snapshot. Pipe to jq."""
    snap = compute_health_snapshot(db_path=db)
    typer.echo(json.dumps(snap.to_dict(), indent=2, default=str))
