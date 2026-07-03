"""
`orch analyze` — L3.3 post-hoc decision analysis.

Spec: docs/specs/v2-debug-F1-F4.md §L3.3.

Read-only queries against `routing_decisions.db` that answer specific
operational questions. Distinct from the live observability dashboard
(`orch metrics live`) which is a snapshot — these are post-hoc
reductions over historical data, the kind of question you'd ask
weekly during a routing review.

Subcommands
-----------

  orch analyze composer-counterfactual  — was reward higher when composer
                                          would have overridden vs. agreed?
  orch analyze escalation-roi           — did escalation events improve
                                          quality, or just cost money?
  orch analyze a3-calibration           — A3 prediction error per agent
  orch analyze drift-history            — drift_events with resolution outcomes
  orch analyze override-roi             — when composer DID override (live
                                          mode), what was the reward delta?
  orch analyze weekly                   — runs all of the above

Output is human-readable text by default; --json for piping.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

import typer

from backend.orchestrator.routing.decision_log import _DEFAULT_DB_PATH


app = typer.Typer(
    name="analyze",
    help="Post-hoc analyses over routing_decisions.db.",
    no_args_is_help=True,
)


def _connect(db: Path) -> sqlite3.Connection:
    if not Path(db).exists():
        typer.echo(f"decisions DB not found: {db}", err=True)
        raise typer.Exit(code=1)
    return sqlite3.connect(str(db))


def _format_section(title: str, rows: list[dict]) -> str:
    """Render a list of dicts as a key=value block under a title."""
    lines = [f"=== {title} ==="]
    if not rows:
        lines.append("  (no data)")
        return "\n".join(lines)
    for row in rows:
        kvs = "  ".join(f"{k}={v}" for k, v in row.items())
        lines.append(f"  {kvs}")
    return "\n".join(lines)


# ── composer counterfactual ───────────────────────────────────────────────────


def _composer_counterfactual(conn: sqlite3.Connection) -> list[dict]:
    """When the composer would have overridden the bandit, was the
    reward better than when it would have agreed? Computed across all
    rows that carry the composer_would_pick shadow signal."""
    rows = conn.execute(
        "SELECT "
        "  CASE WHEN composer_would_pick = bandit_pick OR composer_would_pick IS NULL "
        "       THEN 'agreed' ELSE 'disagreed' END as alignment, "
        "  COUNT(*) as n, "
        "  AVG(reward) as mean_reward "
        "FROM decisions "
        "WHERE reward IS NOT NULL "
        "  AND composer_would_pick IS NOT NULL "
        "GROUP BY alignment"
    ).fetchall()
    out = []
    for alignment, n, mean_reward in rows:
        out.append({
            "alignment": alignment,
            "n": int(n),
            "mean_reward": (
                round(float(mean_reward), 4) if mean_reward is not None else None
            ),
        })
    return out


# ── escalation ROI ────────────────────────────────────────────────────────────


def _escalation_roi(conn: sqlite3.Connection) -> list[dict]:
    """Per-strategy mean reward + cost. Did escalation events actually
    produce higher quality, or did we pay Claude budget for nothing?"""
    # NOTE: alias `strategy` would collide with the existing
    # `strategy` column on the decisions table — SQLite resolves the
    # GROUP BY name as the column, not the alias, and lumps every row
    # under one bucket. Use a non-colliding alias.
    rows = conn.execute(
        "SELECT "
        "  COALESCE(escalation_strategy, 'none') as escalation_kind, "
        "  COUNT(*) as n, "
        "  AVG(reward) as mean_reward, "
        "  AVG(cost_usd) as mean_cost, "
        "  AVG(latency_s) as mean_latency "
        "FROM decisions "
        "WHERE reward IS NOT NULL "
        "GROUP BY escalation_kind "
        "ORDER BY n DESC"
    ).fetchall()
    out = []
    for escalation_kind, n, mean_reward, mean_cost, mean_latency in rows:
        out.append({
            "strategy": escalation_kind,
            "n": int(n),
            "mean_reward": (
                round(float(mean_reward), 4) if mean_reward is not None else None
            ),
            "mean_cost_usd": (
                round(float(mean_cost), 6) if mean_cost is not None else None
            ),
            "mean_latency_s": (
                round(float(mean_latency), 4) if mean_latency is not None else None
            ),
        })
    return out


# ── A3 calibration ────────────────────────────────────────────────────────────


def _a3_calibration(conn: sqlite3.Connection) -> list[dict]:
    """Per-agent mean absolute error: |a3_pred(agent) - reward|."""
    rows = conn.execute(
        "SELECT a3_predictions, selected_agent, reward "
        "FROM decisions "
        "WHERE a3_predictions IS NOT NULL AND reward IS NOT NULL"
    ).fetchall()
    per_agent: dict[str, list[float]] = {}
    for predictions_json, agent, reward in rows:
        try:
            predictions = json.loads(predictions_json)
        except (json.JSONDecodeError, TypeError):
            continue
        p = predictions.get(agent) if isinstance(predictions, dict) else None
        if p is None:
            continue
        per_agent.setdefault(agent, []).append(abs(float(p) - float(reward)))
    out = []
    for agent, errs in per_agent.items():
        out.append({
            "agent": agent,
            "n": len(errs),
            "mae": round(sum(errs) / len(errs), 4),
        })
    out.sort(key=lambda r: r["agent"])
    return out


# ── drift history ─────────────────────────────────────────────────────────────


def _drift_history(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT timestamp, bucket, agent, deviation_sigmas, "
            "       window_mean, historical_mean, "
            "       COALESCE(resolution, 'ACTIVE') as resolution "
            "FROM drift_events ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for ts, bucket, agent, sigmas, wm, hm, resolution in rows:
        out.append({
            "timestamp": ts,
            "bucket": bucket,
            "agent": agent,
            "deviation_sigmas": (
                round(float(sigmas), 4) if sigmas is not None else None
            ),
            "window_mean": (
                round(float(wm), 4) if wm is not None else None
            ),
            "historical_mean": (
                round(float(hm), 4) if hm is not None else None
            ),
            "resolution": resolution,
        })
    return out


# ── override ROI (live composer overrides) ────────────────────────────────────


def _override_roi(conn: sqlite3.Connection) -> list[dict]:
    """When the composer DID override the bandit live (selected_agent
    != bandit_pick AND override_reason is set), was the reward better
    than when it didn't? Distinct from composer_counterfactual which
    compares would-pick vs bandit-pick under shadow mode."""
    rows = conn.execute(
        "SELECT "
        "  CASE WHEN override_reason IS NOT NULL THEN 'overridden' ELSE 'not_overridden' END as kind, "
        "  COUNT(*) as n, "
        "  AVG(reward) as mean_reward, "
        "  AVG(importance_weight) as mean_iw "
        "FROM decisions "
        "WHERE reward IS NOT NULL "
        "GROUP BY kind"
    ).fetchall()
    out = []
    for kind, n, mean_reward, mean_iw in rows:
        out.append({
            "kind": kind,
            "n": int(n),
            "mean_reward": (
                round(float(mean_reward), 4) if mean_reward is not None else None
            ),
            "mean_importance_weight": (
                round(float(mean_iw), 4) if mean_iw is not None else None
            ),
        })
    return out


# ── CLI commands ─────────────────────────────────────────────────────────────


@app.command("composer-counterfactual")
def composer_counterfactual_cmd(
    db: Path = typer.Option(_DEFAULT_DB_PATH),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Mean reward when composer would have agreed vs. disagreed."""
    conn = _connect(db)
    try:
        result = _composer_counterfactual(conn)
    finally:
        conn.close()
    if json_out:
        typer.echo(json.dumps(result, indent=2))
        return
    typer.echo(_format_section("Composer Counterfactual (shadow mode)", result))


@app.command("escalation-roi")
def escalation_roi_cmd(
    db: Path = typer.Option(_DEFAULT_DB_PATH),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Per-strategy mean reward, cost, and latency."""
    conn = _connect(db)
    try:
        result = _escalation_roi(conn)
    finally:
        conn.close()
    if json_out:
        typer.echo(json.dumps(result, indent=2))
        return
    typer.echo(_format_section("Escalation Strategy ROI", result))


@app.command("a3-calibration")
def a3_calibration_cmd(
    db: Path = typer.Option(_DEFAULT_DB_PATH),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """A3 prediction MAE per agent."""
    conn = _connect(db)
    try:
        result = _a3_calibration(conn)
    finally:
        conn.close()
    if json_out:
        typer.echo(json.dumps(result, indent=2))
        return
    typer.echo(_format_section("A3 Calibration (per-agent MAE)", result))


@app.command("drift-history")
def drift_history_cmd(
    limit: int = typer.Option(50, "--limit", "-n"),
    db: Path = typer.Option(_DEFAULT_DB_PATH),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Recent drift_events with resolution outcomes."""
    conn = _connect(db)
    try:
        result = _drift_history(conn, limit=limit)
    finally:
        conn.close()
    if json_out:
        typer.echo(json.dumps(result, indent=2))
        return
    typer.echo(_format_section("Drift History", result))


@app.command("override-roi")
def override_roi_cmd(
    db: Path = typer.Option(_DEFAULT_DB_PATH),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Mean reward + importance weight when composer overrode vs. didn't."""
    conn = _connect(db)
    try:
        result = _override_roi(conn)
    finally:
        conn.close()
    if json_out:
        typer.echo(json.dumps(result, indent=2))
        return
    typer.echo(_format_section("Composer Override ROI (live mode)", result))


@app.command("weekly")
def weekly(
    db: Path = typer.Option(_DEFAULT_DB_PATH),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run all analyses in one shot — the weekly routing review."""
    conn = _connect(db)
    try:
        report = {
            "composer_counterfactual": _composer_counterfactual(conn),
            "escalation_roi": _escalation_roi(conn),
            "a3_calibration": _a3_calibration(conn),
            "drift_history": _drift_history(conn, limit=20),
            "override_roi": _override_roi(conn),
        }
    finally:
        conn.close()
    if json_out:
        typer.echo(json.dumps(report, indent=2))
        return
    for title, result in (
        ("Composer Counterfactual (shadow mode)", report["composer_counterfactual"]),
        ("Escalation Strategy ROI", report["escalation_roi"]),
        ("A3 Calibration (per-agent MAE)", report["a3_calibration"]),
        ("Drift History (last 20)", report["drift_history"]),
        ("Composer Override ROI (live mode)", report["override_roi"]),
    ):
        typer.echo(_format_section(title, result))
        typer.echo("")
