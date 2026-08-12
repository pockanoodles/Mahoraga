"""
`orch metrics live` — terminal view of the routing health snapshot.

Reads `routing_decisions.db` directly via `routing.observability.compute_health_snapshot`.
Designed for unattended operation — `--watch` refreshes every N seconds
so you can leave it open in a tmux pane.

Subcommands
-----------

  orch metrics live [--watch SECONDS] [--db PATH] [--json]
  orch metrics snapshot [--db PATH]           — one-shot JSON dump

When --json is set, emits the snapshot as JSON each tick (suitable for
piping to jq or feeding a dashboard scraper). Default is a human-readable
text view with ANSI colors, an alert banner, and a recent-decisions tail.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from backend.orchestrator.routing.observability import (
    DEFAULT_DB_PATH,
    HealthSnapshot,
    compute_health_snapshot,
)
from backend.orchestrator.routing.usage_report import compute_usage, render_usage


app = typer.Typer(
    name="metrics",
    help="Routing health metrics from the decisions DB.",
    no_args_is_help=True,
)


# ── ANSI helpers ──────────────────────────────────────────────────────────────

_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def _green(t: str) -> str:  return _c("32", t)
def _yellow(t: str) -> str: return _c("33", t)
def _red(t: str) -> str:    return _c("31;1", t)
def _bold(t: str) -> str:   return _c("1", t)
def _dim(t: str) -> str:    return _c("2", t)
def _cyan(t: str) -> str:   return _c("36", t)


def _fmt(v: Optional[float], digits: int = 4) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def _color_reward(v: Optional[float]) -> str:
    if v is None:
        return "—"
    s = f"{v:.4f}"
    if v >= 0.7:
        return _green(s)
    if v >= 0.4:
        return _yellow(s)
    return _red(s)


def _color_success(v: Optional[float]) -> str:
    if v is None:
        return "—"
    s = f"{v:.4f}"
    if v >= 0.8:
        return _green(s)
    if v >= 0.5:
        return _yellow(s)
    return _red(s)


# ── Recent decisions query (reads DB directly) ────────────────────────────────


def _recent_decisions(db_path: Path, n: int = 12) -> list[dict]:
    """Fetch the N most recent decisions with their outcomes."""
    if not Path(db_path).exists():
        return []
    try:
        from backend.orchestrator.store.metrics import _classify_bucket as classify_bucket
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT id, timestamp, selected_agent, strategy, "
            "       task_goal, "
            "       reward, success, latency_s, "
            "       escalation_strategy, override_reason "
            "FROM decisions ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
        conn.close()
    except Exception:
        return []
    out = []
    for row in rows:
        (rid, ts, agent, strat, goal, reward, success, latency, esc_strat, override) = row
        bucket = classify_bucket(goal or "") if goal else "?"
        out.append({
            "id": rid,
            "ts": (ts or "")[:19],
            "agent": agent or "?",
            "bucket": bucket,
            "goal": (goal or "")[:40],
            "strategy": strat or "?",
            "reward": reward,
            "success": success,
            "latency_s": latency,
            "escalation": esc_strat,
            "override": override,
        })
    return list(reversed(out))  # oldest first for chronological display


# ── Alert detection ───────────────────────────────────────────────────────────


def _alerts(snap: HealthSnapshot) -> list[str]:
    """Return a list of alert strings (empty = all clear)."""
    alerts: list[str] = []

    if snap.quarantine.n_active > 0:
        agents = ", ".join(
            f"{e['bucket']}/{e['agent']}" for e in snap.quarantine.entries
        )
        alerts.append(f"quarantine active: {agents}")

    if snap.budget_pacer.over_ceiling:
        alerts.append(
            f"budget over ceiling "
            f"(avg=${_fmt(snap.budget_pacer.avg_cost, 4)} > "
            f"${_fmt(snap.budget_pacer.ceiling, 4)})"
        )

    r100 = snap.rolling_100
    if r100.n_with_outcome >= 10 and r100.success_rate is not None and r100.success_rate < 0.4:
        alerts.append(
            f"low success rate in last 100: {_fmt(r100.success_rate, 3)}"
        )

    if snap.total_decisions > 0 and snap.by_agent:
        no_outcome = [
            a for a, st in snap.by_agent.items()
            if st.n > 5 and st.n_with_outcome == 0
        ]
        if no_outcome:
            alerts.append(f"agents with no logged outcomes: {', '.join(no_outcome)}")

    return alerts


# ── Main render ───────────────────────────────────────────────────────────────


def _render_text(snap: HealthSnapshot, recent: list[dict]) -> str:
    lines: list[str] = []

    # Header
    lines.append(
        _bold(f"=== Mahoraga Routing Health  @ {snap.timestamp[:19]} UTC")
    )
    lines.append(_dim(f"db: {snap.db_path}"))
    lines.append("")

    # ── Alert banner ──────────────────────────────────────────────────────────
    alert_list = _alerts(snap)
    if alert_list:
        for a in alert_list:
            lines.append(_red(f"  ⚠  {a}"))
        lines.append("")
    else:
        lines.append(_green("  ✓  All systems nominal"))
        lines.append("")

    # ── Overview ──────────────────────────────────────────────────────────────
    lines.append(
        f"decisions: {_bold(str(snap.total_decisions)):>6}   "
        f"with outcome: {snap.total_with_outcome:>6}   "
        f"strategies: {snap.by_strategy}"
    )
    lines.append("")

    # ── Reward windows ────────────────────────────────────────────────────────
    lines.append(
        _cyan(f"{'window':<12} {'n':>6} {'w/outcome':>9} {'reward':>9} {'success':>9} {'lat_s':>8} {'cost':>8}")
    )
    lines.append(_dim("-" * 68))
    for label, w in (
        ("rolling_100", snap.rolling_100),
        ("rolling_500", snap.rolling_500),
        ("all_time",    snap.all_time),
    ):
        lines.append(
            f"{label:<12} {w.n:>6} {w.n_with_outcome:>9} "
            f"{_color_reward(w.mean_reward):>9} {_color_success(w.success_rate):>9} "
            f"{_fmt(w.mean_latency_s, 2):>8} {_fmt(w.mean_cost_usd, 5):>8}"
        )
    lines.append("")

    # ── Per-agent rollup ──────────────────────────────────────────────────────
    if snap.by_agent:
        lines.append(
            _cyan(f"{'agent':<16} {'n':>6} {'w/outcome':>9} {'win%':>7} {'reward':>9} {'lat_s':>8}")
        )
        lines.append(_dim("-" * 60))
        for agent, st in sorted(snap.by_agent.items(), key=lambda x: -x[1].n):
            win = f"{st.win_rate:.1%}" if st.win_rate is not None else "—"
            win_colored = (
                _green(win) if (st.win_rate or 0) >= 0.7
                else _yellow(win) if (st.win_rate or 0) >= 0.4
                else _red(win)
            ) if st.win_rate is not None else "—"
            lines.append(
                f"{agent:<16} {st.n:>6} {st.n_with_outcome:>9} "
                f"{win_colored:>7} {_color_reward(st.mean_reward):>9} "
                f"{_fmt(st.mean_latency_s, 2):>8}"
            )
        lines.append("")

    # ── Composer shadow ───────────────────────────────────────────────────────
    cs = snap.composer_shadow
    if cs.n_with_data > 0:
        delta = cs.counterfactual_delta
        delta_str = f"{delta:+.4f}" if delta is not None else "—"
        delta_colored = (
            _green(delta_str) if (delta or 0) > 0.05
            else _yellow(delta_str) if (delta or 0) > -0.05
            else _red(delta_str)
        )
        lines.append(
            f"composer shadow  n={cs.n_with_data}  "
            f"disagreements={cs.n_disagreements}  "
            f"agreed_reward={_fmt(cs.mean_reward_when_agreed)}  "
            f"disagreed_reward={_fmt(cs.mean_reward_when_disagreed)}  "
            f"Δ={delta_colored}"
        )
    else:
        lines.append(_dim("composer shadow: no data yet"))

    # ── Escalation ────────────────────────────────────────────────────────────
    if snap.escalation.n_total_escalations > 0:
        lines.append(
            f"escalations: {snap.escalation.n_total_escalations} total  "
            f"({_fmt(snap.escalation.rate_per_100, 2)}/100)  "
            f"by_strategy={snap.escalation.by_strategy}"
        )
    else:
        lines.append(_dim("escalations: 0"))

    # ── F5 Quarantine ─────────────────────────────────────────────────────────
    qs = snap.quarantine
    if qs.n_active > 0:
        cells = "  ".join(
            _red(f"{e['bucket']}/{e['agent']}(σ={e['deviation_sigmas']:.2f})")
            for e in qs.entries
        )
        lines.append(f"quarantine: {qs.n_active} active — {cells}")
    else:
        lines.append(_dim("quarantine: none"))
    if qs.n_drift_events_total > 0:
        lines.append(
            f"drift: {qs.n_drift_events_total} total  "
            f"{qs.n_drift_events_unresolved} unresolved"
        )

    # ── F2 pool + F1 budget ───────────────────────────────────────────────────
    ep = snap.execution_pool
    pool_str = f"pool: {ep.depth}/{ep.max_concurrent}  norm={_fmt(ep.depth_norm, 3)}"
    pool_str += _red("  SATURATED") if ep.depth_norm >= 1.0 else ""
    lines.append(pool_str)

    bp = snap.budget_pacer
    if bp.n_observed is not None:
        budget_str = (
            f"budget: avg=${_fmt(bp.avg_cost, 4)}/task  "
            f"ceiling=${_fmt(bp.ceiling, 4)}  "
            f"λ={_fmt(bp.lambda_, 4)}  n={bp.n_observed}"
        )
        lines.append(_red(budget_str) if bp.over_ceiling else budget_str)
    else:
        lines.append(_dim("budget: no tasks observed yet"))

    # ── Brain + A3 ────────────────────────────────────────────────────────────
    if snap.brain.n_total_with_data > 0:
        lines.append(
            f"brain: {snap.brain.n_with_hits}/{snap.brain.n_total_with_data} hits  "
            f"mean_top_sim={_fmt(snap.brain.mean_top_sim, 3)}"
        )
    if snap.a3.n_with_predictions > 0:
        lines.append(
            f"a3: n={snap.a3.n_with_predictions}  MAE={_fmt(snap.a3.calibration_mae, 3)}"
        )

    # ── Recent decisions tail ─────────────────────────────────────────────────
    if recent:
        lines.append("")
        lines.append(_cyan(
            f"{'#':>6}  {'time':<19}  {'agent':<14}  {'bucket':<10}  "
            f"{'ok':>3}  {'reward':>8}  {'lat_s':>6}  {'note'}"
        ))
        lines.append(_dim("-" * 82))
        for d in recent:
            ok = _green("✓") if d["success"] == 1 else (
                _red("✗") if d["success"] == 0 else _dim("·")
            )
            reward_s = _color_reward(d["reward"]) if d["reward"] is not None else _dim("—")
            lat_s = f"{d['latency_s']:.2f}" if d["latency_s"] is not None else "—"
            note = ""
            if d["override"]:
                note = _yellow("override")
            elif d["escalation"] and d["escalation"] != "none":
                note = _cyan(d["escalation"])
            lines.append(
                f"{d['id']:>6}  {d['ts']:<19}  {d['agent']:<14}  "
                f"{d['bucket']:<10}  {ok:>3}  {reward_s:>8}  {lat_s:>6}  {note}"
            )

    return "\n".join(lines)


# ── Commands ──────────────────────────────────────────────────────────────────


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
    tail: int = typer.Option(
        12, "--tail", "-n",
        help="Number of recent decisions to show in the tail (0 to hide).",
    ),
) -> None:
    """Render the routing health snapshot with colors, alerts, and recent decisions."""
    while True:
        snap = compute_health_snapshot(db_path=db)
        if json_out:
            typer.echo(json.dumps(snap.to_dict(), indent=2, default=str))
        else:
            if watch is not None:
                typer.echo("\x1b[2J\x1b[H", nl=False)
            recent = _recent_decisions(db, n=tail) if tail > 0 else []
            typer.echo(_render_text(snap, recent))
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


@app.command()
def usage(
    since: Optional[str] = typer.Option(
        None, "--since", help="Start date, inclusive (YYYY-MM-DD)."
    ),
    until: Optional[str] = typer.Option(
        None, "--until", help="End date, inclusive (YYYY-MM-DD)."
    ),
    db: Path = typer.Option(DEFAULT_DB_PATH, help="Path to routing_decisions.db"),
    json_out: bool = typer.Option(
        False, "--json", help="Emit raw JSON instead of formatted text.",
    ),
) -> None:
    """What the cascade did for real work: local share, escalations, spend avoided.

    Organic traffic only — rows carrying a bench_run_id are experiments, and a
    single forced-explore run would otherwise swamp a month of actual use.
    """
    report = compute_usage(db, since=since, until=until)
    if json_out:
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        typer.echo(render_usage(report))
