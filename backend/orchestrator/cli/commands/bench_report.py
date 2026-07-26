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
from ...routing.verify_replay import (
    load_bank as load_verify_bank,
    load_results as load_verify_results,
    evaluate as evaluate_verify,
    summarize as summarize_verify,
)
from ...routing.route_sim import (
    grade_matrix,
    load_cloud_costs,
    simulate as simulate_policies,
    infer_arms,
)
from ...tracking.pricing import PRICING, PRICING_AS_OF, calculate_cost

DEFAULT_METRICS_DB = Path.home() / ".mahoraga-v2" / "mahoraga.db"
DEFAULT_DECISIONS_DB = Path.home() / ".mahoraga-v2" / "routing_decisions.db"
DEFAULT_VERIFY_BANK = Path(__file__).resolve().parents[4] / "experiments" / "prompts_verifiable.jsonl"

VALID_METRICS = ("quality", "reward", "pass_rate", "latency_s", "tokens", "tps")

report_app = typer.Typer(
    name="report",
    help="Reporting tools for bench run analysis",
    no_args_is_help=True,
)


def _iso_to_epoch(iso: str, *, end_of_day: bool = False) -> float:
    """Parse an ISO-8601 date or datetime string into a UNIX epoch float.

    Accepts date-only (2026-07-26), naive datetimes (assumed UTC), and
    offset-aware forms (trailing Z or ±HH:MM). A date-only value with
    end_of_day=True maps to the last instant of that day, so an --until
    bound includes the whole day rather than silently excluding it.
    """
    raw = iso.strip()
    # Date-only: choose start- or end-of-day depending on which bound this is.
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        pass
    else:
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        return dt.replace(tzinfo=timezone.utc).timestamp()
    # Full datetime: fromisoformat handles ±HH:MM; map trailing Z to UTC.
    try:
        dt = datetime.fromisoformat(raw[:-1] + "+00:00" if raw[-1:] in ("Z", "z") else raw)
    except ValueError:
        raise ValueError(f"Cannot parse date: {iso!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


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
    try:
        conn.row_factory = sqlite3.Row

        # Build WHERE clauses and params
        wheres: list[str] = []
        params: list = []

        if since:
            epoch = _iso_to_epoch(since)
            wheres.append("CAST(m.timestamp AS REAL) >= ?")
            params.append(epoch)

        if until:
            # Date-only --until means "through the end of that day".
            epoch = _iso_to_epoch(until, end_of_day=True)
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
                m.prompt_tokens,
                m.reward_score,
                m.success,
                m.quality_score,
                m.cost_usd
            FROM task_metrics m
            {where_clause}
        """

        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


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


def _is_local_agent(agent_name: Optional[str]) -> bool:
    """Local arms are ollama-served; everything else is a cloud arm."""
    return (agent_name or "").startswith("ollama:")


def _aggregate_cost(rows: list[dict], reference_model: str) -> dict:
    """Compute actual vs counterfactual-cloud spend, overall and per bucket.

    Counterfactual (all-cloud) is built per row:
    - Cloud rows with a recorded cost_usd > 0 contribute their ACTUAL cost —
      they already ran on the cloud, so the recorded bill (which includes
      cache-creation tokens the token counters miss) is the ground truth.
      Token re-pricing is the fallback only when actual is 0/NULL.
    - Local rows are priced at reference_model rates (prompt tokens x input
      rate + generated tokens x output rate; no cache modeling).
    - Rows with no token data AND no cost (0 or NULL across the board) cannot
      be priced; they are counted as unpriced so coverage stays visible.

    Avoided = counterfactual minus actual, local rows only. Local rows priced
    with tokens_generated > 0 but no prompt-token data are counted as
    n_missing_prompt — their input side is missing, so avoided spend is
    understated (conservative) for them.
    """
    def _empty_bucket() -> dict:
        return {
            "n": 0, "n_local": 0, "n_unpriced": 0, "n_missing_prompt": 0,
            "actual_usd": 0.0, "counterfactual_usd": 0.0,
            "avoided_usd": 0.0, "avoided_success_usd": 0.0,
        }

    totals = _empty_bucket()
    per_bucket: dict[str, dict] = {}

    for r in rows:
        bucket = per_bucket.setdefault(r["capability_bucket"] or "", _empty_bucket())
        local = _is_local_agent(r["agent_name"])
        actual = r["cost_usd"] or 0.0
        gen = r["tokens_generated"] or 0
        prompt = r["prompt_tokens"] or 0
        # Rows are written with 0 (not NULL) when data is absent — a row is
        # unpriced when it has no tokens and no recorded cost at all.
        unpriced = gen == 0 and prompt == 0 and actual == 0.0
        missing_prompt = False
        if unpriced:
            counterfactual = 0.0
        elif not local and actual > 0:
            # Cloud row: its ground-truth all-cloud cost is what it actually cost.
            counterfactual = actual
        else:
            counterfactual = calculate_cost(reference_model, prompt, gen)
            missing_prompt = local and prompt == 0 and gen > 0
        avoided = counterfactual - actual if local else 0.0
        for agg in (totals, bucket):
            agg["n"] += 1
            agg["n_local"] += int(local)
            agg["n_unpriced"] += int(unpriced)
            agg["n_missing_prompt"] += int(missing_prompt)
            agg["actual_usd"] += actual
            agg["counterfactual_usd"] += counterfactual
            agg["avoided_usd"] += avoided
            agg["avoided_success_usd"] += avoided if r["success"] == 1 else 0.0

    for agg in (totals, *per_bucket.values()):
        agg["local_share"] = agg["n_local"] / agg["n"] if agg["n"] else None
        for k in ("actual_usd", "counterfactual_usd", "avoided_usd", "avoided_success_usd"):
            agg[k] = round(agg[k], 6)

    totals["n_cloud"] = totals["n"] - totals["n_local"]
    totals["savings_pct"] = (
        round(totals["avoided_usd"] / totals["counterfactual_usd"] * 100, 2)
        if totals["counterfactual_usd"] > 0 else None
    )
    totals["avoided_per_1k_tasks_usd"] = (
        round(totals["avoided_usd"] / totals["n"] * 1000, 6) if totals["n"] else None
    )

    methodology = (
        f"methodology (frozen): cloud rows use recorded actual cost; local rows priced "
        f"at {reference_model} rates as of {PRICING_AS_OF} (prompt in + generated out, "
        f"no cache modeling); rows with no token/cost data excluded"
    )
    if totals["n_missing_prompt"]:
        methodology += (
            f"; {totals['n_missing_prompt']} local rows lack prompt-token data "
            f"(input side understated)"
        )
    return {
        "reference_model": reference_model,
        "pricing_as_of": PRICING_AS_OF,
        "totals": totals,
        "per_bucket": per_bucket,
        "methodology": methodology,
    }


def _render_cost_table(agg: dict) -> None:
    t = agg["totals"]
    typer.echo(f"cost — actual spend vs counterfactual all-cloud at {agg['reference_model']} rates")
    typer.echo("(avoided = what the locally-served rows would have cost at cloud rates; "
                "cloud rows count at their recorded actual cost; "
                "unpriced rows have no token or cost data and are excluded from the counterfactual)")
    typer.echo("")
    local_pct = f"{t['local_share']*100:.1f}%" if t["local_share"] is not None else "n/a"
    typer.echo(f"  tasks:              {t['n']}  (local={t['n_local']} [{local_pct}], "
                f"cloud={t['n_cloud']}, unpriced={t['n_unpriced']})")
    typer.echo(f"  actual spend:       ${t['actual_usd']:.4f}")
    typer.echo(f"  all-cloud spend:    ${t['counterfactual_usd']:.4f}  (counterfactual)")
    typer.echo(f"  avoided (gross):    ${t['avoided_usd']:.4f}  — all local rows")
    typer.echo(f"  avoided (success):  ${t['avoided_success_usd']:.4f}  — successful local rows only")
    savings_str = f"{t['savings_pct']:.1f}%" if t["savings_pct"] is not None else "n/a"
    typer.echo(f"  savings:            {savings_str} of the all-cloud bill (gross)")
    per_1k = t["avoided_per_1k_tasks_usd"]
    per_1k_str = f"${per_1k:.2f}" if per_1k is not None else "n/a"
    typer.echo(f"  per 1,000 in-scope tasks:  {per_1k_str} avoided (gross)")
    if t["n_missing_prompt"]:
        typer.echo(
            f"  input-side under-count: {t['n_missing_prompt']} local rows have no "
            f"prompt-token data — avoided spend is understated for them"
        )

    typer.echo("")
    typer.echo("Per-bucket:")
    for bucket, pb in sorted(agg["per_bucket"].items()):
        share = f"{pb['local_share']*100:.0f}%" if pb["local_share"] is not None else "n/a"
        typer.echo(
            f"  {bucket:<12}  N={pb['n']:<4}  local={share:<5}  avoided=${pb['avoided_usd']:.4f}"
        )

    typer.echo("")
    typer.echo(agg["methodology"])


@report_app.command("cost")
def cost_report(
    since: Optional[str] = typer.Option(None, "--since", help="Filter rows with timestamp >= ISO date (e.g. 2026-04-24)"),
    until: Optional[str] = typer.Option(None, "--until", help="Upper bound ISO date"),
    bench_run_id: Optional[int] = typer.Option(None, "--bench-run-id", help="Filter to tasks in a specific bench run"),
    reference_model: str = typer.Option("claude-sonnet-4-6", "--reference-model", help="Cloud model whose rates price the counterfactual"),
    output_json: bool = typer.Option(False, "--json", help="Output aggregates as JSON"),
    output_csv: bool = typer.Option(False, "--csv", help="Output per-bucket breakdown as CSV"),
    db: Path = typer.Option(DEFAULT_METRICS_DB, "--db", help="Override metrics DB path"),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db", help="Override decisions DB path"),
) -> None:
    """Counterfactual cost report: what locally-served tasks would have cost
    at cloud API rates vs what was actually spent.

    Cloud rows contribute their recorded actual cost_usd to the all-cloud
    counterfactual (ground truth — token re-pricing would miss cache-creation
    tokens); local rows are priced at the reference model's rates. Rows with
    no token or cost data are counted as unpriced and excluded. Reports gross
    avoided spend (all local rows) and a success-only variant (success=1 local
    rows) so failed local attempts can't inflate the number, and discloses
    how many local rows lack prompt-token data (avoided spend understated).
    Zero new inference; reads the metrics DB offline.
    """
    if reference_model not in PRICING:
        typer.echo(
            f"Unknown reference model: {reference_model!r}. Choose from: {', '.join(sorted(PRICING))}",
            err=True,
        )
        raise typer.Exit(1)

    try:
        rows = _fetch_rows(
            db_path=db,
            decisions_db_path=decisions_db,
            since=since,
            until=until,
            bench_run_id=bench_run_id,
            agents_filter=None,
            buckets_filter=None,
        )
    except Exception as exc:
        typer.echo(f"Error reading DB: {exc}", err=True)
        raise typer.Exit(1)

    if not rows:
        typer.echo("No data")
        raise typer.Exit(0)

    agg = _aggregate_cost(rows, reference_model)

    if output_json:
        typer.echo(json.dumps(agg, indent=2))
        return

    if output_csv:
        import csv
        import sys
        writer = csv.writer(sys.stdout)
        writer.writerow(["bucket", "n", "n_local", "n_unpriced", "local_share",
                         "actual_usd", "counterfactual_usd", "avoided_usd", "avoided_success_usd"])
        for bucket, pb in sorted(agg["per_bucket"].items()):
            writer.writerow([
                bucket,
                pb["n"],
                pb["n_local"],
                pb["n_unpriced"],
                f"{pb['local_share']:.4f}" if pb["local_share"] is not None else "",
                f"{pb['actual_usd']:.6f}",
                f"{pb['counterfactual_usd']:.6f}",
                f"{pb['avoided_usd']:.6f}",
                f"{pb['avoided_success_usd']:.6f}",
            ])
        return

    _render_cost_table(agg)


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


@report_app.command("verify")
def verify_cmd(
    input_path: Path = typer.Option(
        ..., "--input", "-i", help="Bench --output JSONL with prompt_full/output_full fields"
    ),
    bank: Path = typer.Option(
        DEFAULT_VERIFY_BANK, "--bank", help="Gold benchmark bank (prompt + hidden tests) JSONL"
    ),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db"),
    output_json: bool = typer.Option(False, "--json"),
    notes: Optional[str] = typer.Option(
        None, "--notes", help="Why this verify run — logged to bench_runs."
    ),
) -> None:
    """Execution-based ("verifiable") scoring for code/debug outputs.

    Joins bench outputs to the gold bank's hidden tests, runs each extracted
    solution under python3, and reports pass@1 per (bucket, agent) alongside
    the heuristic quality score for the SAME outputs. The headline is whether
    pass@1 ranks the arms differently than the heuristic does — include a
    deliberately-weak canary arm to see whether execution ranks it last where
    the heuristic can't. Zero new inference; needs a bench JSONL from
    `orch bench run --output ...` over experiments/prompts_verifiable.jsonl.
    """
    if not bank.exists():
        typer.echo(f"Gold bank not found: {bank}", err=True)
        raise typer.Exit(1)
    if not input_path.exists():
        typer.echo(f"Results file not found: {input_path}", err=True)
        raise typer.Exit(1)

    bank_map = load_verify_bank(bank)
    results = load_verify_results(input_path)
    if not results:
        typer.echo(
            "No usable rows (need prompt_full + output_full + actual_agent — "
            "re-run the bench batch with --output if this file is older).",
            err=True,
        )
        raise typer.Exit(0)

    cases, unmatched = evaluate_verify(bank_map, results)
    if not cases:
        typer.echo(
            f"No results matched the gold bank ({unmatched} unmatched). "
            f"Was --input generated from {bank.name}?",
            err=True,
        )
        raise typer.Exit(0)

    result = summarize_verify(cases, results)

    # Auto-summary for the ledger: per-agent pass@1 and the rank correlation
    # between execution and the heuristic quality score (the payoff signal).
    overall = result["overall"]
    rc = result["rank_comparison"]
    pass_by_agent = {a: v["pass_rate"] for a, v in overall.items()}
    auto_summary = (
        f"input={input_path.name} bank={bank.name} n_cases={len(cases)} "
        f"unmatched={unmatched} pass@1_by_agent={pass_by_agent} "
        f"spearman_rho={rc.get('rho')} best_by_exec={rc.get('best_by_exec')} "
        f"its_heuristic_rank={rc.get('best_by_exec_heuristic_rank')}/{rc.get('n_arms')} "
        f"heuristic_top={rc.get('best_by_heuristic')} inverted={rc.get('inverted')}"
    )
    log_offline_run(
        decisions_db,
        mode="verify",
        task_count=len(cases),
        notes=f"{auto_summary} | {notes}" if notes else auto_summary,
    )

    if output_json:
        typer.echo(json.dumps(result, indent=2))
        return

    typer.echo(f"verify — {len(cases)} scored cases from {input_path.name} "
                f"({unmatched} unmatched vs {bank.name})")
    typer.echo("(pass@1 = fraction whose extracted code passed the hidden tests; "
                "q = mean heuristic quality on the same outputs)")
    typer.echo("")
    header = f"  {'bucket':<10}" + "".join(f"{a.split(':')[-1]:>22}" for a in result["agents"])
    typer.echo(header)
    for b in result["buckets"]:
        cells = []
        for a in result["agents"]:
            c = result["by_bucket"][b].get(a)
            if not c:
                cells.append(f"{'—':>22}")
            else:
                q = c["heuristic_quality"]
                cells.append(f"{c['pass_rate']:.2f}({c['passed']}/{c['n']}) q={q if q is not None else '—'!s:>5}".rjust(22))
        typer.echo(f"  {b:<10}" + "".join(cells))
    typer.echo("")
    typer.echo("  overall:")
    for a in result["agents"]:
        o = overall[a]
        typer.echo(f"    {a:<24} pass@1={o['pass_rate']:.3f} ({o['passed']}/{o['n']})  "
                    f"heuristic_q={o['heuristic_quality']}")
    typer.echo("")
    if rc.get("rho") is not None:
        typer.echo(f"  execution-vs-heuristic rank correlation (Spearman rho) = {rc['rho']} "
                    f"across {rc['n_arms']} arms")
    if rc.get("inverted"):
        typer.echo(
            f"  ** inversion: the most-correct arm ({rc['best_by_exec']}, "
            f"pass@1={rc['best_by_exec_pass_rate']:.3f}) is only rank "
            f"{rc['best_by_exec_heuristic_rank']}/{rc['n_arms']} by heuristic quality "
            f"(heuristic's top pick is {rc['best_by_heuristic']}) — the heuristic does not "
            f"track correctness."
        )
    elif rc.get("rho") is not None:
        typer.echo(f"  the most-correct arm ({rc['best_by_exec']}) is also the heuristic's top pick.")


@report_app.command("route-sim")
def route_sim_cmd(
    input_path: Path = typer.Option(
        ..., "--input", "-i", help="Force-explore bench JSONL (prompt_full/output_full/actual_agent)"
    ),
    bench_run_id: int = typer.Option(
        ..., "--bench-run-id", help="Bench run whose cloud rows supply per-prompt cost"
    ),
    bank: Path = typer.Option(
        DEFAULT_VERIFY_BANK, "--bank", help="Gold bank (prompt + hidden tests) for re-grading"
    ),
    metrics_db: Path = typer.Option(DEFAULT_METRICS_DB, "--metrics-db"),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db"),
    cloud_arm: str = typer.Option("claude-cli", "--cloud-arm", help="Agent id treated as the cloud escalation target"),
    local_first: Optional[str] = typer.Option(
        None, "--local-first",
        help="Comma-separated local arm cascade tried before escalating (default: best local arm)",
    ),
    output_json: bool = typer.Option(False, "--json"),
    notes: Optional[str] = typer.Option(None, "--notes", help="Why this sim — logged to bench_runs."),
) -> None:
    """Counterfactual routing-vs-baseline Pareto from a force-explore matrix.

    Re-grades every stored output against the hidden tests and joins the cloud
    arm's per-prompt cost, then computes — with zero new inference — what each
    policy WOULD have scored: always-cloud, always-local per arm, best-of-local,
    and a routed cascade (local first, escalate failures to cloud). The routed
    row uses an ORACLE escalation gate, so its cost is the achievable FLOOR /
    quality is the CEILING; on verifiable tasks that oracle is real (run the
    tests), on open-ended tasks it's the upper bound a fallible gate chases.
    """
    if not bank.exists():
        typer.echo(f"Gold bank not found: {bank}", err=True)
        raise typer.Exit(1)
    if not input_path.exists():
        typer.echo(f"Results file not found: {input_path}", err=True)
        raise typer.Exit(1)

    matrix, bank_prompts = grade_matrix(bank, input_path)
    if not matrix:
        typer.echo(
            f"No results matched the gold bank. Was --input generated from {bank.name}?",
            err=True,
        )
        raise typer.Exit(0)

    local_arms, best_local = infer_arms(matrix, cloud_arm)
    if not local_arms:
        typer.echo("No local (ollama:) arms found in the matrix — nothing to route.", err=True)
        raise typer.Exit(0)
    cascade = [a.strip() for a in local_first.split(",")] if local_first else [best_local]

    cloud_costs = load_cloud_costs(decisions_db, metrics_db, bench_run_id, cloud_arm)
    matched_cost = sum(1 for p in bank_prompts if p in cloud_costs)

    policies = simulate_policies(
        matrix, bank_prompts, cloud_costs,
        local_arms=local_arms, cloud_arm=cloud_arm, cascade=cascade,
    )
    by_name = {p.name: p for p in policies}
    routed = next(p for p in policies if p.name.startswith("routed:"))
    cloud = by_name.get("always-cloud")
    cost_cut = (
        100 * (1 - routed.cost_per_task / cloud.cost_per_task)
        if cloud and cloud.cost_per_task else None
    )

    cloud_per_1k = cloud.cost_per_task * 1000 if cloud else 0.0
    auto_summary = (
        f"input={input_path.name} bench_run_id={bench_run_id} n_prompts={len(bank_prompts)} "
        f"cloud_cost_matched={matched_cost}/{len(bank_prompts)} cascade={'->'.join(cascade)} "
        f"routed_pass@1={routed.pass_rate:.4f} routed_$1k={routed.cost_per_task*1000:.2f} "
        f"escalations={routed.escalations} "
        f"cloud_$1k={cloud_per_1k:.2f} cost_cut_pct={cost_cut}"
    )
    log_offline_run(
        decisions_db, mode="route-sim", task_count=len(bank_prompts),
        notes=f"{auto_summary} | {notes}" if notes else auto_summary,
    )

    if output_json:
        typer.echo(json.dumps({
            "bench_run_id": bench_run_id,
            "n_prompts": len(bank_prompts),
            "cloud_cost_matched": matched_cost,
            "local_arms": local_arms,
            "cascade": cascade,
            "cloud_arm": cloud_arm,
            "cost_cut_pct": round(cost_cut, 2) if cost_cut is not None else None,
            "policies": [p.as_dict() for p in policies],
        }, indent=2))
        return

    typer.echo(f"route-sim — bench_run_id={bench_run_id}, {len(bank_prompts)} prompts, "
               f"cloud cost matched {matched_cost}/{len(bank_prompts)}")
    typer.echo(f"local arms: {', '.join(a.split(':')[-1] for a in local_arms)}   "
               f"cascade: {' -> '.join(a.split(':')[-1] for a in cascade)} -> cloud")
    typer.echo("")
    typer.echo(f"  {'policy':<32}{'pass@1':>16}{'$/task':>12}{'$/1k':>10}")
    typer.echo("  " + "-" * 68)
    for p in policies:
        tag = f"  (esc {p.escalations})" if p.escalations is not None else ""
        typer.echo(f"  {p.name:<32}{f'{p.pass_rate:.3f} ({p.passed}/{p.n})':>16}"
                   f"{f'${p.cost_per_task:.4f}':>12}{f'${p.cost_per_task*1000:.2f}':>10}{tag}")
    typer.echo("")
    if cost_cut is not None:
        typer.echo(f"  routed vs always-cloud: {cost_cut:.1f}% cost cut at pass@1={routed.pass_rate:.3f} "
                   f"(${routed.cost_per_task*1000:.2f} vs ${cloud.cost_per_task*1000:.2f} per 1k)")
    typer.echo("  NOTE: routed uses an ORACLE escalation gate — achievable on verifiable tasks "
               "(run the tests), an upper bound elsewhere until a real gate is measured.")


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
