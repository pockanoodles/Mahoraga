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

from ...routing.reweight_replay import load_decisions, log_offline_run, summarize
from ...routing.quality_replay import VARIANTS, load_rows as load_quality_rows, summarize as summarize_quality

DEFAULT_METRICS_DB = Path.home() / ".mahoraga-v2" / "mahoraga.db"
DEFAULT_DECISIONS_DB = Path.home() / ".mahoraga-v2" / "routing_decisions.db"

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


def _parse_weights(raw: str) -> tuple[float, float, float, float]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise typer.BadParameter(
            f"expected 4 comma-separated weights (success,quality,speed,cost), got {len(parts)}"
        )
    try:
        w = tuple(float(p) for p in parts)
    except ValueError as exc:
        raise typer.BadParameter(f"weights must be numeric: {exc}")
    if any(v < 0.05 for v in w):
        raise typer.BadParameter("each weight must be >= 0.05 (per BUCKET_WEIGHTS convention)")
    return w  # type: ignore[return-value]


@report_app.command("reweight")
def reweight_cmd(
    weights: str = typer.Option(
        ..., "--weights", help="Alt weights as w_success,w_quality,w_speed,w_cost (e.g. 0.20,0.55,0.20,0.05)"
    ),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Only replay the most-recent N decisions"),
    min_samples: int = typer.Option(3, "--min-samples", help="Suppress buckets with fewer than N tasks"),
    output_json: bool = typer.Option(False, "--json"),
    notes: Optional[str] = typer.Option(
        None, "--notes", help="Why this reweight run — logged to bench_runs alongside live batches."
    ),
) -> None:
    """Recompute logged decisions' reward under an alternate weight vector.

    Zero new inference — re-scores decisions already in the DB using their
    logged success/quality/latency/cost, under BUCKET_WEIGHTS (baseline) vs.
    the given alt weights. Answers whether re-weighting opens agent
    separation that the current weights suppress, without running anything.
    """
    alt = _parse_weights(weights)
    rows = load_decisions(decisions_db, limit=limit)
    if not rows:
        typer.echo("No data")
        raise typer.Exit(0)

    result = summarize(rows, alt)

    max_widening = max(
        (c["alt_gap"] / c["baseline_gap"] for c in result.values() if c["baseline_gap"] > 0),
        default=None,
    )
    auto_summary = (
        f"weights={alt} n_decisions={len(rows)} max_widening_ratio="
        f"{round(max_widening, 2) if max_widening is not None else 'n/a'}"
    )
    log_offline_run(
        decisions_db,
        mode="reweight",
        task_count=len(rows),
        notes=f"{auto_summary} | {notes}" if notes else auto_summary,
    )

    if output_json:
        typer.echo(json.dumps(result, indent=2))
        return

    typer.echo(f"reweight — baseline BUCKET_WEIGHTS vs alt={alt}")
    typer.echo("(gap = max-min avg reward across agents in that bucket; wider alt_gap = more separation)")
    typer.echo("")
    header = f"{'bucket':<12} {'n':>4} {'baseline_gap':>14} {'alt_gap':>10}  {'baseline_avg':<40} {'alt_avg'}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for bucket, cell in sorted(result.items()):
        if cell["n"] < min_samples:
            continue
        typer.echo(
            f"{bucket:<12} {cell['n']:>4} {cell['baseline_gap']:>14.4f} {cell['alt_gap']:>10.4f}  "
            f"{cell['baseline_avg']!s:<40} {cell['alt_avg']!s}"
        )


@report_app.command("quality-replay")
def quality_replay_cmd(
    input_path: Path = typer.Option(
        ..., "--input", "-i", help="Bench --output JSONL with prompt_full/output_full fields"
    ),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db"),
    output_json: bool = typer.Option(False, "--json"),
    notes: Optional[str] = typer.Option(
        None, "--notes", help="Why this quality-replay run — logged to bench_runs."
    ),
) -> None:
    """Re-score already-captured (prompt, output, bucket, agent) rows under
    generous quality-scorer variants (higher length plateaus, uncapped
    security keyword bonuses, continuous not-plan) to test whether the
    heuristic scorer's caps/plateaus — not the models — suppress agent
    separation. Zero new inference; needs a bench JSONL produced with
    `orch bench run --output ...` after the prompt_full/output_full fields
    were added (2026-07-09).
    """
    rows = load_quality_rows(input_path)
    if not rows:
        typer.echo(
            "No usable rows (need prompt_full + output_full fields — "
            "re-run the bench batch if this file predates 2026-07-09).",
            err=True,
        )
        raise typer.Exit(0)

    result = summarize_quality(rows, VARIANTS)

    baseline_gap = result["baseline"]["overall_gap_avg"]
    max_widening = max(
        (v["overall_gap_avg"] / baseline_gap for k, v in result.items() if k != "baseline" and baseline_gap > 0),
        default=None,
    )
    auto_summary = (
        f"input={input_path} n_rows={len(rows)} baseline_gap={baseline_gap} "
        f"max_variant_widening_ratio={round(max_widening, 2) if max_widening is not None else 'n/a'}"
    )
    log_offline_run(
        decisions_db,
        mode="quality-replay",
        task_count=len(rows),
        notes=f"{auto_summary} | {notes}" if notes else auto_summary,
    )

    if output_json:
        typer.echo(json.dumps(result, indent=2))
        return

    typer.echo(f"quality-replay — {len(rows)} real captured rows from {input_path}")
    typer.echo("(gap = max-min avg score across agents in that bucket, per config)")
    typer.echo("")
    for cfg_name, cfg_result in result.items():
        typer.echo(f"=== {cfg_name} === overall_gap_avg={cfg_result['overall_gap_avg']}  "
                    f"score_vs_tokens_corr={cfg_result['score_vs_tokens_corr']}")
        for bucket, cell in sorted(cfg_result["per_bucket"].items()):
            typer.echo(f"  {bucket:<10} n={cell['n']:<4} gap={cell['gap']:<8} avg_by_agent={cell['avg_by_agent']}")
        typer.echo("")


@report_app.command("runs")
def list_runs(
    mode: Optional[str] = typer.Option(None, "--mode", help="Filter to one mode (bandit | force-explore | reweight | ...)"),
    limit: int = typer.Option(30, "--limit", help="Most-recent N runs"),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db"),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """List every logged experiment — live batches and offline analyses alike.

    The single ledger for "what have we actually tested": every `orch bench
    run` batch and every `orch bench report reweight` call writes a
    bench_runs row with a `notes` field explaining why. This just reads them
    back, most-recent first.
    """
    if not decisions_db.exists():
        typer.echo("No data")
        raise typer.Exit(0)

    conn = sqlite3.connect(str(decisions_db))
    conn.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT id, started_at, mode, agents, task_count_planned, "
            "task_count_completed, notes FROM bench_runs"
        )
        params: list = []
        if mode:
            sql += " WHERE mode = ?"
            params.append(mode)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

    if not rows:
        typer.echo("No data")
        raise typer.Exit(0)

    if output_json:
        typer.echo(json.dumps(rows, indent=2))
        return

    for r in rows:
        completed = r["task_count_completed"]
        planned = r["task_count_planned"]
        typer.echo(
            f"#{r['id']:<4} {r['started_at'][:19]}  mode={r['mode']:<14} "
            f"tasks={completed}/{planned}"
        )
        if r["notes"]:
            typer.echo(f"      {r['notes']}")
