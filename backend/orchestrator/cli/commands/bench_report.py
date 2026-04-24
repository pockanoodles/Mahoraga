"""
bench_report.py — compatibility-matrix report for bench run analysis.

Reads task_metrics from the metrics DB and renders a (bucket x agent) matrix
of aggregate quality, reward, pass-rate, latency, tokens, or tps.

Usage:
    orch bench report compat-matrix [OPTIONS]
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

DEFAULT_METRICS_DB = Path.home() / ".mahoraga" / "mahoraga.db"
DEFAULT_DECISIONS_DB = Path.home() / ".mahoraga" / "routing_decisions.db"

VALID_METRICS = ("quality", "reward", "pass_rate", "latency_s", "tokens", "tps")

report_app = typer.Typer(
    name="report",
    help="Reporting tools for bench run analysis",
    no_args_is_help=True,
)


def _iso_to_epoch(iso: str) -> float:
    """Parse an ISO-8601 date or datetime string into a UNIX epoch float."""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(iso, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {iso!r}")


def _fetch_rows(
    db_path: Path,
    decisions_db_path: Path,
    since: Optional[str],
    until: Optional[str],
    bench_run_id: Optional[int],
    agents_filter: Optional[list[str]],
    buckets_filter: Optional[list[str]],
) -> list[dict]:
    """Query task_metrics (with optional join on decisions for bench_run_id filter)."""
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Build WHERE clauses and params
    wheres: list[str] = []
    params: list = []

    if since:
        epoch = _iso_to_epoch(since)
        wheres.append("CAST(m.timestamp AS REAL) >= ?")
        params.append(epoch)

    if until:
        epoch = _iso_to_epoch(until)
        wheres.append("CAST(m.timestamp AS REAL) <= ?")
        params.append(epoch)

    if agents_filter:
        placeholders = ",".join("?" * len(agents_filter))
        wheres.append(f"m.agent_name IN ({placeholders})")
        params.extend(agents_filter)

    if buckets_filter:
        placeholders = ",".join("?" * len(buckets_filter))
        wheres.append(f"m.capability_bucket IN ({placeholders})")
        params.extend(buckets_filter)

    # bench_run_id requires joining the decisions DB
    if bench_run_id is not None:
        resolved = decisions_db_path.resolve()
        if not resolved.is_file():
            raise typer.BadParameter(
                f"--bench-run-id given but decisions DB not found at {resolved}. "
                "Pass --decisions-db to override."
            )
        # SQLite ATTACH doesn't accept parameter binding; canonicalize the path
        # and sanity-check it's a real file to keep injection surface minimal.
        conn.execute("ATTACH DATABASE ? AS ddb", (str(resolved),))
        wheres.append(
            "m.task_id IN (SELECT task_id FROM ddb.decisions WHERE bench_run_id = ?)"
        )
        params.append(bench_run_id)

    where_clause = ("WHERE " + " AND ".join(wheres)) if wheres else ""

    sql = f"""
        SELECT
            m.agent_name,
            m.capability_bucket,
            m.wall_time_ms,
            m.tokens_generated,
            m.tokens_per_second,
            m.reward_score,
            m.success,
            m.quality_score
        FROM task_metrics m
        {where_clause}
    """

    cur = conn.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


_METRIC_TO_FIELD = {
    "quality": "avg_quality",
    "reward": "avg_reward",
    "pass_rate": "pass_rate",
    "latency_s": "avg_latency_s",
    "tokens": "avg_tokens",
    "tps": "avg_tps",
}


def _aggregate(rows: list[dict], best_by: str = "quality") -> dict:
    """Return matrix[bucket][agent] = cell_agg, per_agent, per_bucket dicts.

    `best_by` is the metric key used to pick the winning agent per bucket.
    """
    # Group by (bucket, agent)
    cells: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        key = (r["capability_bucket"] or "", r["agent_name"] or "")
        cells.setdefault(key, []).append(r)

    def _cell_agg(cell_rows: list[dict]) -> dict:
        n = len(cell_rows)
        successes = [r["success"] for r in cell_rows if r["success"] is not None]
        qualities = [r["quality_score"] for r in cell_rows if r["quality_score"] is not None]
        rewards = [r["reward_score"] for r in cell_rows if r["reward_score"] is not None]
        latencies = [r["wall_time_ms"] for r in cell_rows if r["wall_time_ms"] is not None]
        tokens = [r["tokens_generated"] for r in cell_rows if r["tokens_generated"] is not None]
        tps = [r["tokens_per_second"] for r in cell_rows if r["tokens_per_second"] is not None]
        return {
            "n": n,
            "pass_rate": sum(successes) / len(successes) if successes else None,
            "avg_quality": sum(qualities) / len(qualities) if qualities else None,
            "avg_reward": sum(rewards) / len(rewards) if rewards else None,
            "avg_latency_s": sum(latencies) / len(latencies) / 1000 if latencies else None,
            "avg_tokens": sum(tokens) / len(tokens) if tokens else None,
            "avg_tps": sum(tps) / len(tps) if tps else None,
        }

    matrix: dict[str, dict[str, dict]] = {}
    for (bucket, agent), cell_rows in cells.items():
        matrix.setdefault(bucket, {})[agent] = _cell_agg(cell_rows)

    # Per-agent: aggregate across all buckets
    agent_rows: dict[str, list[dict]] = {}
    for r in rows:
        agent_rows.setdefault(r["agent_name"] or "", []).append(r)

    per_agent: dict[str, dict] = {
        agent: _cell_agg(ar) for agent, ar in agent_rows.items()
    }

    # Per-bucket: aggregate + find best agent by avg_quality
    per_bucket: dict[str, dict] = {}
    bucket_rows: dict[str, list[dict]] = {}
    for r in rows:
        bucket_rows.setdefault(r["capability_bucket"] or "", []).append(r)

    best_field = _METRIC_TO_FIELD.get(best_by, "avg_quality")
    # For latency, lower is better; everything else, higher.
    reverse = best_by == "latency_s"
    for bucket, br in bucket_rows.items():
        agg = _cell_agg(br)
        best_agent = None
        best_score: Optional[float] = None
        if bucket in matrix:
            for agent, cell in matrix[bucket].items():
                v = cell.get(best_field)
                if v is None:
                    continue
                if best_score is None:
                    better = True
                else:
                    better = v < best_score if reverse else v > best_score
                if better:
                    best_score = v
                    best_agent = agent
        agg["best_agent"] = best_agent
        agg["best_score"] = best_score
        agg["best_by"] = best_by
        per_bucket[bucket] = agg

    return {"matrix": matrix, "per_agent": per_agent, "per_bucket": per_bucket}


def _metric_value(cell: dict, metric: str) -> Optional[float]:
    return cell.get(_METRIC_TO_FIELD[metric])


def _render_table(agg: dict, metric: str, min_samples: int) -> None:
    matrix = agg["matrix"]
    per_agent = agg["per_agent"]
    per_bucket = agg["per_bucket"]

    if not matrix:
        typer.echo("No data")
        return

    all_buckets = sorted(matrix.keys())
    all_agents: list[str] = []
    seen: set[str] = set()
    for bucket in all_buckets:
        for agent in sorted(matrix[bucket].keys()):
            if agent not in seen:
                all_agents.append(agent)
                seen.add(agent)

    metric_label = {
        "quality": "avg quality score",
        "reward": "avg reward score",
        "pass_rate": "pass rate",
        "latency_s": "avg latency (s)",
        "tokens": "avg tokens generated",
        "tps": "avg tokens/second",
    }[metric]

    typer.echo(f"compat-matrix — {metric_label}")
    typer.echo(
        f"(N tasks per cell in parentheses; cells with N<{min_samples} omitted)"
    )
    typer.echo("")

    col_width = 20
    bucket_col = 12

    # Header row
    header = f"{'':>{bucket_col}}"
    for agent in all_agents:
        header += f"  {agent:>{col_width}}"
    typer.echo(header)
    typer.echo("-" * len(header))

    for bucket in all_buckets:
        line = f"{bucket:>{bucket_col}}"
        for agent in all_agents:
            cell = matrix.get(bucket, {}).get(agent)
            if cell is None or cell["n"] < min_samples:
                line += f"  {'—':>{col_width}}"
            else:
                val = _metric_value(cell, metric)
                if val is None:
                    line += f"  {'n/a':>{col_width}}"
                else:
                    cell_str = f"{val:.2f} ({cell['n']})"
                    line += f"  {cell_str:>{col_width}}"
        typer.echo(line)

    typer.echo("")
    typer.echo("Per-agent totals:")
    for agent in all_agents:
        ag = per_agent.get(agent, {})
        n = ag.get("n", 0)
        q = ag.get("avg_quality")
        pr = ag.get("pass_rate")
        lat = ag.get("avg_latency_s")
        q_str = f"{q:.2f}" if q is not None else "n/a"
        pr_str = f"{pr*100:.0f}%" if pr is not None else "n/a"
        lat_str = f"{lat:.1f}s" if lat is not None else "n/a"
        typer.echo(
            f"  {agent:<24}  N={n:<4}  avg_quality={q_str:<6}  pass={pr_str:<5}  avg_latency={lat_str}"
        )

    typer.echo("")
    typer.echo("Per-bucket totals:")
    for bucket in all_buckets:
        pb = per_bucket.get(bucket, {})
        n = pb.get("n", 0)
        best = pb.get("best_agent") or "n/a"
        best_score = pb.get("best_score")
        score_str = f" ({best_score:.2f})" if best_score is not None else ""
        typer.echo(f"  {bucket:<12}  N={n:<4}  best={best}{score_str}")


@report_app.command("compat-matrix")
def compat_matrix(
    since: Optional[str] = typer.Option(None, "--since", help="Filter rows with timestamp >= ISO date (e.g. 2026-04-24)"),
    until: Optional[str] = typer.Option(None, "--until", help="Upper bound ISO date"),
    bench_run_id: Optional[int] = typer.Option(None, "--bench-run-id", help="Filter to tasks in a specific bench run"),
    agent: Optional[str] = typer.Option(None, "--agent", help="Filter to agent name(s), comma-separated"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="Filter to bucket name(s), comma-separated"),
    min_samples: int = typer.Option(3, "--min-samples", help="Suppress cells with fewer than N tasks"),
    metric: str = typer.Option("quality", "--metric", help="quality | reward | pass_rate | latency_s | tokens | tps"),
    output_json: bool = typer.Option(False, "--json", help="Output raw aggregates as JSON"),
    output_csv: bool = typer.Option(False, "--csv", help="Output as CSV"),
    db: Path = typer.Option(DEFAULT_METRICS_DB, "--db", help="Override metrics DB path"),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db", help="Override decisions DB path"),
) -> None:
    """Render a bucket x agent compatibility matrix from task_metrics."""
    if metric not in VALID_METRICS:
        typer.echo(f"Invalid metric: {metric!r}. Choose from: {', '.join(VALID_METRICS)}", err=True)
        raise typer.Exit(1)

    agents_filter = [a.strip() for a in agent.split(",") if a.strip()] if agent else None
    buckets_filter = [b.strip() for b in bucket.split(",") if b.strip()] if bucket else None

    try:
        rows = _fetch_rows(
            db_path=db,
            decisions_db_path=decisions_db,
            since=since,
            until=until,
            bench_run_id=bench_run_id,
            agents_filter=agents_filter,
            buckets_filter=buckets_filter,
        )
    except Exception as exc:
        typer.echo(f"Error reading DB: {exc}", err=True)
        raise typer.Exit(1)

    if not rows:
        typer.echo("No data")
        raise typer.Exit(0)

    agg = _aggregate(rows, best_by=metric)

    if output_json:
        typer.echo(json.dumps(agg, indent=2))
        return

    if output_csv:
        import csv
        import sys
        writer = csv.writer(sys.stdout)
        writer.writerow(["bucket", "agent", "n", "pass_rate", "avg_quality", "avg_reward", "avg_latency_s", "avg_tokens", "avg_tps"])
        for bkt, agents_map in sorted(agg["matrix"].items()):
            for ag, cell in sorted(agents_map.items()):
                writer.writerow([
                    bkt,
                    ag,
                    cell["n"],
                    f"{cell['pass_rate']:.4f}" if cell["pass_rate"] is not None else "",
                    f"{cell['avg_quality']:.4f}" if cell["avg_quality"] is not None else "",
                    f"{cell['avg_reward']:.4f}" if cell["avg_reward"] is not None else "",
                    f"{cell['avg_latency_s']:.4f}" if cell["avg_latency_s"] is not None else "",
                    f"{cell['avg_tokens']:.1f}" if cell["avg_tokens"] is not None else "",
                    f"{cell['avg_tps']:.2f}" if cell["avg_tps"] is not None else "",
                ])
        return

    _render_table(agg, metric, min_samples)
