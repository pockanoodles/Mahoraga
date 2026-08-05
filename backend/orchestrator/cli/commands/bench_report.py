"""
bench_report.py — compatibility-matrix report for bench run analysis.

Reads task_metrics from the metrics DB and renders a (bucket x agent) matrix
of aggregate quality, reward, pass-rate, latency, tokens, or tps.

Usage:
    orch bench report compat-matrix [OPTIONS]
"""
from __future__ import annotations

import asyncio
import json
import re
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
from ...routing.reward_fidelity_replay import run_replay as run_reward_replay
from ...routing.route_ceiling import run_ceiling
from ...routing.judge_gate import judge_one, GENERAL_RUBRIC
from ...routing.tool_judge import tool_augmented_judge
from ...routing.code_judge import MIN_DISAGREEMENTS, differential_check
from ...routing.nonverifiable_bank import (
    load_bank as load_nv_bank,
    load_refs as load_nv_refs,
    score as score_nv,
)
from ...workers.ollama import OllamaWorker
from ...workers.claude_cli import ClaudeCliWorker
from ...tracking.pricing import PRICING, PRICING_AS_OF, calculate_cost

DEFAULT_METRICS_DB = Path.home() / ".mahoraga-v2" / "mahoraga.db"
DEFAULT_DECISIONS_DB = Path.home() / ".mahoraga-v2" / "routing_decisions.db"
DEFAULT_VERIFY_BANK = Path(__file__).resolve().parents[4] / "experiments" / "prompts_verifiable.jsonl"
DEFAULT_HUMANEVAL_BANK = Path(__file__).resolve().parents[4] / "experiments" / "prompts_humaneval_plus.jsonl"
# The recorded P1 force-explore cross (Era 20). The qwen topup file carries the
# 53 recorded qwen timings the recovered full file lacks (same outputs).
DEFAULT_P1_CROSS = (
    Path(__file__).resolve().parents[4] / "experiments" / "p1_cross_granite.jsonl",
    Path(__file__).resolve().parents[4] / "experiments" / "p1_cross_qwen_full.jsonl",
    Path(__file__).resolve().parents[4] / "experiments" / "p1_cross_qwen_topup.jsonl",
)
# The recorded P0 live cascade (Era 19) — local arm + judge + cloud outcome per row.
DEFAULT_LIVE_ROUTE = Path(__file__).resolve().parents[4] / "experiments" / "live_route_humaneval_164.jsonl"
DEFAULT_NV_BANK = Path(__file__).resolve().parents[4] / "experiments" / "prompts_nonverifiable.jsonl"
DEFAULT_NV_REFS = Path(__file__).resolve().parents[4] / "experiments" / "prompts_nonverifiable_refs.jsonl"
DEFAULT_JUDGE_CACHE = Path.home() / ".mahoraga-v2" / "judge_gate_cache.json"
DEFAULT_JUDGE_BANK_CACHE = Path.home() / ".mahoraga-v2" / "judge_bank_cache.json"

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


@report_app.command("judge-gate")
def judge_gate_cmd(
    input_path: Path = typer.Option(
        ..., "--input", "-i", help="Force-explore bench JSONL (prompt_full/output_full/actual_agent)"
    ),
    bench_run_id: int = typer.Option(..., "--bench-run-id", help="Bench run for per-prompt cloud cost"),
    bank: Path = typer.Option(DEFAULT_VERIFY_BANK, "--bank", help="Gold bank — hidden tests give the ground truth the judge is scored against"),
    metrics_db: Path = typer.Option(DEFAULT_METRICS_DB, "--metrics-db"),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db"),
    primary_local: Optional[str] = typer.Option(None, "--primary-local", help="Local arm whose output the judge gates (default: best local)"),
    cloud_arm: str = typer.Option("claude-cli", "--cloud-arm"),
    judge_model: str = typer.Option("qwen3.5:latest", "--judge-model", help="Model that renders the correctness verdict"),
    judge_egress: str = typer.Option("local", "--judge-egress", help="local = Ollama (free) | cli = claude-cli (spends cloud $ per call)"),
    cache_path: Path = typer.Option(DEFAULT_JUDGE_CACHE, "--cache", help="Verdict cache (keyed by judge model) so re-runs never re-pay"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Judge only the first N prompts (cheap smoke)"),
    output_json: bool = typer.Option(False, "--json"),
    notes: Optional[str] = typer.Option(None, "--notes"),
) -> None:
    """LLM-judge escalation gate: does a judge, seeing prompt+output ONLY (no
    hidden tests — the production posture), correctly decide when to escalate?

    Judges the primary local arm's stored outputs, scores the verdicts against
    the gold bank's hidden tests (the ground truth), and runs route-sim with the
    judge as the escalation gate — charging the judge's own per-call cost on
    every task. `--judge-egress local` is free (Ollama) and is the on-thesis
    path; `cli` uses claude-cli and spends per call. Verdicts are cached, so a
    second run (or `--json`) costs nothing.
    """
    if not bank.exists():
        typer.echo(f"Gold bank not found: {bank}", err=True)
        raise typer.Exit(1)
    if not input_path.exists():
        typer.echo(f"Results file not found: {input_path}", err=True)
        raise typer.Exit(1)
    if judge_egress not in ("local", "cli"):
        raise typer.BadParameter("--judge-egress must be 'local' or 'cli'")

    matrix, bank_prompts = grade_matrix(bank, input_path)
    if not matrix:
        typer.echo(f"No results matched the gold bank. Was --input generated from {bank.name}?", err=True)
        raise typer.Exit(0)
    local_arms, best_local = infer_arms(matrix, cloud_arm)
    primary = primary_local or best_local
    if not primary:
        typer.echo("No local arm found to gate.", err=True)
        raise typer.Exit(0)

    bank_map = load_verify_bank(bank)
    primary_out = {
        r["prompt"]: r["output"]
        for r in load_verify_results(input_path)
        if r["agent"] == primary and r["prompt"] in bank_map
    }
    if not primary_out:
        typer.echo(f"No stored outputs for primary arm {primary!r} in {input_path.name}.", err=True)
        raise typer.Exit(0)

    if judge_egress == "local":
        worker = OllamaWorker(model=judge_model, worker_id="ollama:judge", extra_payload={"think": False})
    else:
        worker = ClaudeCliWorker(model=judge_model, worker_id="claude-cli:judge")

    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    slot = cache.setdefault(judge_model, {})
    verdicts: dict[str, Optional[bool]] = {}
    costs: list[float] = []

    async def _judge_all() -> None:
        items = [p for p in bank_prompts if p in primary_out]
        if limit:
            items = items[:limit]
        for p in items:
            hit = slot.get(p)
            if hit and hit.get("verdict") is not None:
                verdict, cost = hit["verdict"], hit.get("cost", 0.0) or 0.0
            else:
                verdict, cost, _raw, _err = await judge_one(worker, p, primary_out[p])
                slot[p] = {"verdict": verdict, "cost": cost}
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(cache, indent=2))
            verdicts[p] = verdict
            if cost:
                costs.append(cost)

    asyncio.run(_judge_all())

    mean_cost = sum(costs) / len(costs) if costs else 0.0
    scored_prompts = [p for p in verdicts if p in primary_out]
    # Confusion vs ground truth. positive = "judge says local is correct (accept)".
    tp = fp = tn = fn = 0
    unparsed = 0
    for p in scored_prompts:
        v = verdicts[p]
        if v is None:
            unparsed += 1
            continue
        true_pass = matrix.get(p, {}).get(primary)
        if v and true_pass:
            tp += 1
        elif v and not true_pass:
            fp += 1  # accepted a wrong answer -> quality leak
        elif (not v) and (not true_pass):
            tn += 1  # caught a real failure -> good escalation
        else:
            fn += 1  # escalated a correct answer -> wasted cloud $
    n_fail = sum(1 for p in bank_prompts if primary in matrix.get(p, {}) and not matrix[p][primary])
    graded = tp + fp + tn + fn
    accuracy = (tp + tn) / graded if graded else 0.0
    fail_recall = tn / n_fail if n_fail else 0.0

    cloud_costs = load_cloud_costs(decisions_db, metrics_db, bench_run_id, cloud_arm)
    solved = lambda p: bool(verdicts.get(p, False))  # None/False -> escalate
    policies = simulate_policies(
        matrix, bank_prompts, cloud_costs,
        local_arms=local_arms, cloud_arm=cloud_arm, cascade=[primary],
        local_solved=solved, gate_cost_per_task=mean_cost,
    )
    routed = next(p for p in policies if p.name.startswith("routed:"))
    cloud = next((p for p in policies if p.name == "always-cloud"), None)
    cost_cut = (100 * (1 - routed.cost_per_task / cloud.cost_per_task)
                if cloud and cloud.cost_per_task else None)

    auto_summary = (
        f"input={input_path.name} judge={judge_model} egress={judge_egress} "
        f"primary={primary} judged={graded} unparsed={unparsed} "
        f"acc={accuracy:.3f} fail_recall={tn}/{n_fail} judge_$1k={mean_cost*1000:.2f} "
        f"routed_pass@1={routed.pass_rate:.4f} routed_$1k={routed.cost_per_task*1000:.2f} "
        f"escalations={routed.escalations} cost_cut_pct={cost_cut}"
    )
    log_offline_run(decisions_db, mode="judge-gate", task_count=graded,
                    notes=f"{auto_summary} | {notes}" if notes else auto_summary)

    if output_json:
        typer.echo(json.dumps({
            "judge_model": judge_model, "judge_egress": judge_egress, "primary": primary,
            "judged": graded, "unparsed": unparsed,
            "accuracy": round(accuracy, 4), "fail_recall": round(fail_recall, 4),
            "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "n_fail": n_fail},
            "judge_cost_per_task": round(mean_cost, 6),
            "cost_cut_pct": round(cost_cut, 2) if cost_cut is not None else None,
            "policies": [p.as_dict() for p in policies],
        }, indent=2))
        return

    typer.echo(f"judge-gate — judge={judge_model} ({judge_egress}), primary={primary}, "
               f"{graded} judged ({unparsed} unparsed)")
    typer.echo(f"  accuracy={accuracy:.3f}  fail-recall={tn}/{n_fail}={fail_recall:.3f}  "
               f"(caught {tn} / missed {fp} failures; over-escalated {fn})")
    typer.echo(f"  judge cost = ${mean_cost:.4f}/call (${mean_cost*1000:.2f}/1k, charged on every task)")
    typer.echo("")
    typer.echo(f"  {'policy':<32}{'pass@1':>16}{'$/task':>12}{'$/1k':>10}")
    typer.echo("  " + "-" * 68)
    for p in policies:
        tag = f"  (esc {p.escalations})" if p.escalations is not None else ""
        typer.echo(f"  {p.name:<32}{f'{p.pass_rate:.3f} ({p.passed}/{p.n})':>16}"
                   f"{f'${p.cost_per_task:.4f}':>12}{f'${p.cost_per_task*1000:.2f}':>10}{tag}")
    typer.echo("")
    if cost_cut is not None:
        typer.echo(f"  judge-gate vs always-cloud: {cost_cut:.1f}% cost cut at pass@1={routed.pass_rate:.3f}")


@report_app.command("judge-bank")
def judge_bank_cmd(
    bank: Path = typer.Option(DEFAULT_NV_BANK, "--bank", help="Non-verifiable bank (id/prompt/bucket/tier)"),
    refs: Path = typer.Option(DEFAULT_NV_REFS, "--refs", help="Reference+mutant labels keyed by id"),
    judge_model: str = typer.Option("qwen3.5:latest", "--judge-model", help="Model that renders the correctness verdict"),
    judge_egress: str = typer.Option("local", "--judge-egress", help="local = Ollama (free) | cli = claude-cli (spends $/call)"),
    tool: bool = typer.Option(False, "--tool", help="Compute-augmented judge (tool_judge): a self-consistent sandboxed solver catches wrong NUMBERS the plain judge misses. Recall-only. Local egress only."),
    cache_path: Path = typer.Option(DEFAULT_JUDGE_BANK_CACHE, "--cache", help="Verdict cache (keyed by judge model) so re-runs never re-pay"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Judge only the first N rows (cheap smoke)"),
    output_json: bool = typer.Option(False, "--json"),
    notes: Optional[str] = typer.Option(None, "--notes"),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db"),
) -> None:
    """Judge-discrimination on NON-VERIFIABLE tasks — the judge's real proving
    ground. 5c proved the cascade live on code, where a hidden-test oracle
    exists; here there is none. Each bank row ships a correct `reference` and a
    subtly-flawed `mutant` (labels by construction). The judge sees prompt +
    answer ONLY and must accept the reference / reject the mutant. Reports
    accuracy, reference-accept rate, and — the escalation-relevant number —
    **mutant catch rate** (can it catch a bad answer with no oracle?), plus
    per-bucket and per-defect breakdowns. `--judge-egress local` is free.
    """
    if not bank.exists():
        typer.echo(f"Non-verifiable bank not found: {bank}", err=True)
        raise typer.Exit(1)
    if not refs.exists():
        typer.echo(f"Refs not found: {refs}", err=True)
        raise typer.Exit(1)
    if judge_egress not in ("local", "cli"):
        raise typer.BadParameter("--judge-egress must be 'local' or 'cli'")

    bank_map = load_nv_bank(bank)
    refs_map = load_nv_refs(refs)
    ids = [i for i in bank_map if i in refs_map]
    if limit:
        ids = ids[:limit]
    if not ids:
        typer.echo("No rows to judge (bank/refs join is empty).", err=True)
        raise typer.Exit(1)

    if tool and judge_egress != "local":
        raise typer.BadParameter("--tool runs a sandboxed solver and is local-egress only")

    if judge_egress == "local":
        worker = OllamaWorker(model=judge_model, worker_id="ollama:judge", extra_payload={"think": False})
    else:
        worker = ClaudeCliWorker(model=judge_model, worker_id="claude-cli:judge")

    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    # Tool verdicts differ from the plain judge's, so they get their own cache
    # slot rather than clobbering the base-judge results for the same model.
    slot = cache.setdefault(f"{judge_model}::tool" if tool else judge_model, {})
    verdict_ref: dict[str, Optional[bool]] = {}
    verdict_mut: dict[str, Optional[bool]] = {}
    costs: list[float] = []

    async def _judge(cache_key: str, prompt: str, answer: str) -> Optional[bool]:
        hit = slot.get(cache_key)
        if hit and hit.get("verdict") is not None:
            if hit.get("cost"):
                costs.append(hit["cost"])
            return hit["verdict"]
        if tool:
            verdict, cost, _detail = await tool_augmented_judge(worker, prompt, answer)
        else:
            verdict, cost, _raw, _err = await judge_one(worker, prompt, answer, rubric=GENERAL_RUBRIC)
        slot[cache_key] = {"verdict": verdict, "cost": cost}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2))
        if cost:
            costs.append(cost)
        return verdict

    async def _judge_all() -> None:
        for idx, i in enumerate(ids, 1):
            prompt = bank_map[i]["prompt"]
            verdict_ref[i] = await _judge(f"{i}:ref", prompt, refs_map[i]["reference"])
            verdict_mut[i] = await _judge(f"{i}:mut", prompt, refs_map[i]["mutant"])
            if not output_json:
                ok_ref = "✓" if verdict_ref[i] is True else ("·" if verdict_ref[i] is False else "?")
                ok_mut = "✓" if verdict_mut[i] is False else ("·" if verdict_mut[i] is True else "?")
                typer.echo(f"  [{idx:>3}/{len(ids)}] {i:<28} ref={ok_ref} mutant={ok_mut}")

    asyncio.run(_judge_all())

    # Score only the rows actually judged (so --limit doesn't count the rest as
    # unparsed); on a full run this is the whole bank.
    judged_bank = {i: bank_map[i] for i in ids}
    judged_refs = {i: refs_map[i] for i in ids}
    sc = score_nv(judged_bank, judged_refs, verdict_ref, verdict_mut)
    mean_cost = sum(costs) / len(costs) if costs else 0.0

    auto_summary = (
        f"bank={bank.name} judge={judge_model} egress={judge_egress} n={sc.n_rows} "
        f"accuracy={sc.accuracy:.3f} ref_accept={sc.ref_accept_rate:.3f} "
        f"mutant_catch={sc.mutant_catch_rate:.3f} paired={sc.paired_correct}/{sc.n_rows} "
        f"unparsed={sc.unparsed} judge_$1k={mean_cost*1000:.2f}"
    )
    log_offline_run(decisions_db, mode="judge-bank", task_count=sc.n_rows,
                    notes=f"{auto_summary} | {notes}" if notes else auto_summary)

    if output_json:
        typer.echo(json.dumps({
            "judge_model": judge_model, "judge_egress": judge_egress,
            "judge_cost_per_task": round(mean_cost, 6),
            **sc.as_dict(),
        }, indent=2))
        return

    typer.echo("")
    typer.echo(f"judge-bank — judge={judge_model} ({judge_egress}){' +tool' if tool else ''}, {sc.n_rows} labeled pairs")
    typer.echo(f"  accuracy={sc.accuracy:.3f}  ref-accept={sc.ref_accept_rate:.3f}  "
               f"mutant-catch={sc.mutant_catch_rate:.3f}  "
               f"paired={sc.paired_correct}/{sc.n_rows}={sc.paired_rate:.3f}  unparsed={sc.unparsed}")
    if mean_cost:
        typer.echo(f"  judge cost = ${mean_cost:.4f}/call (${mean_cost*1000:.2f}/1k)")
    typer.echo("")
    typer.echo(f"  {'bucket':<14}{'accuracy':>12}{'(correct/parsed)':>20}")
    typer.echo("  " + "-" * 44)
    for bk, d in sc.by_bucket.items():
        frac = f"({d['correct']}/{d['parsed']})"
        typer.echo(f"  {bk:<14}{d['accuracy']:>12.3f}{frac:>20}")
    typer.echo("")
    typer.echo(f"  {'defect':<22}{'catch_rate':>12}{'(caught/parsed)':>18}")
    typer.echo("  " + "-" * 52)
    for df, d in sc.by_defect.items():
        frac = f"({d['caught']}/{d['parsed']})"
        typer.echo(f"  {df:<22}{d['catch_rate']:>12.3f}{frac:>18}")


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


def _load_routed_cases(path: Path) -> list[dict]:
    """RoutedCase rows from a `bench live-route` per-case JSONL."""
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        if "local_output" in row and "judge_verdict" in row:
            rows.append(row)
    return rows


@report_app.command("code-judge")
def code_judge_cmd(
    input_path: Path = typer.Option(
        ..., "--input", "-i",
        help="live-route per-case JSONL (must carry local_output/judge_verdict/cloud_passed)",
    ),
    judge_model: str = typer.Option("qwen3.5:latest", "--judge-model"),
    gen_samples: int = typer.Option(3, "--gen-samples", help="Reference generations per task (K)"),
    min_disagree: int = typer.Option(
        MIN_DISAGREEMENTS, "--min-disagree",
        help="Reject only on >= this many disagreeing consensus inputs "
             "(cached verdicts are re-derived, so sweeping this is free)",
    ),
    cache_path: Path = typer.Option(
        DEFAULT_JUDGE_CACHE, "--cache",
        help="Verdict cache (own ::code slot) so re-runs never re-generate",
    ),
    limit: Optional[int] = typer.Option(None, "--limit", help="Tool-check only the first N accepted rows"),
    misses_only: bool = typer.Option(
        False, "--misses-only",
        help="Smoke: tool-check only the recorded false-accepts (peeks at ground truth — "
             "measures catch potential, not the honest operating point)",
    ),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db"),
    output_json: bool = typer.Option(False, "--json"),
    notes: Optional[str] = typer.Option(None, "--notes"),
) -> None:
    """Replay the generated-test code judge over a recorded live-route run.

    Recall-only counterfactual: the recorded verdict is the base judge, and the
    differential check runs ONLY on rows the base judge ACCEPTED (a recorded
    reject already escalated; the tool cannot soften it). Because live-route
    records the always-cloud baseline per row (`run_cloud_always`), the routed
    pass@1 and $/1k under the new gate are computed EXACTLY from recorded
    outcomes — no local arm or cloud inference is spent, only local judge
    generations. Local egress only by construction.
    """
    if not input_path.exists():
        typer.echo(f"Results file not found: {input_path}", err=True)
        raise typer.Exit(1)
    rows = _load_routed_cases(input_path)
    if not rows:
        typer.echo(f"No RoutedCase rows in {input_path.name} — is this a live-route output?", err=True)
        raise typer.Exit(1)
    incomplete = sum(1 for r in rows if r.get("cloud_passed") is None)
    if incomplete:
        typer.echo(
            f"warning: {incomplete}/{len(rows)} rows lack a cloud baseline "
            "(run without run_cloud_always?) — projection treats their escalations as failed",
            err=True,
        )

    worker = OllamaWorker(model=judge_model, worker_id="ollama:judge", extra_payload={"think": False})
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    slot = cache.setdefault(f"{judge_model}::code", {})

    accepted = [r for r in rows if r.get("judge_verdict") is True]
    targets = accepted
    if misses_only:
        targets = [r for r in accepted if not r.get("local_passed")]
    if limit:
        targets = targets[:limit]
    target_prompts = {r["prompt_full"] for r in targets}

    tool_verdicts: dict[str, Optional[bool]] = {}
    tool_details: dict[str, str] = {}

    def _apply_threshold(verdict: Optional[bool], detail: str) -> Optional[bool]:
        """Re-derive a cached reject under --min-disagree (the detail carries
        the true mismatch count, so the threshold is sweepable for free)."""
        if verdict is not False:
            return verdict
        m = re.match(r"(\d+)/\d+ consensus inputs disagree", detail)
        if m and int(m.group(1)) < min_disagree:
            return None
        return verdict

    async def _check_all() -> None:
        for idx, row in enumerate(targets, 1):
            prompt = row["prompt_full"]
            hit = slot.get(prompt)
            if hit is not None:
                tool_verdicts[prompt] = _apply_threshold(hit.get("verdict"), hit.get("detail", ""))
                tool_details[prompt] = hit.get("detail", "")
                continue
            # fresh checks record the raw (threshold-1) verdict so the cache
            # stays sweepable; the effective verdict is derived above
            verdict, _cost, detail = await differential_check(
                worker, prompt, row["local_output"], k=gen_samples, min_disagreements=1
            )
            verdict_effective = _apply_threshold(verdict, detail)
            tool_verdicts[prompt] = verdict_effective
            tool_details[prompt] = detail
            slot[prompt] = {"verdict": verdict, "detail": detail}
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, indent=2))
            typer.echo(
                f"  [{idx}/{len(targets)}] tool={'REJECT' if verdict_effective is False else 'abstain'} "
                f"local_passed={row.get('local_passed')} — {detail[:100]}",
                err=True,
            )

    asyncio.run(_check_all())

    def _confusion(new_gate: bool) -> tuple[int, int, int, int]:
        tp = fp = tn = fn = 0
        for r in rows:
            v = r.get("judge_verdict")
            if new_gate and v is True and tool_verdicts.get(r["prompt_full"]) is False:
                v = False
            accept = v is True  # None already escalates in the live gate
            if accept and r["local_passed"]:
                tp += 1
            elif accept and not r["local_passed"]:
                fp += 1
            elif not accept and not r["local_passed"]:
                tn += 1
            else:
                fn += 1
        return tp, fp, tn, fn

    def _project(new_gate: bool) -> tuple[float, float, int]:
        passed = 0
        cost = 0.0
        escalations = 0
        for r in rows:
            v = r.get("judge_verdict")
            if new_gate and v is True and tool_verdicts.get(r["prompt_full"]) is False:
                v = False
            escalate = v is not True
            cost += float(r.get("judge_cost") or 0.0)
            if escalate:
                escalations += 1
                cost += float(r.get("cloud_cost") or 0.0)
                passed += 1 if r.get("cloud_passed") else 0
            else:
                passed += 1 if r.get("local_passed") else 0
        return passed / len(rows), cost / len(rows), escalations

    n = len(rows)
    n_fail = sum(1 for r in rows if not r["local_passed"])
    old_tp, old_fp, old_tn, old_fn = _confusion(new_gate=False)
    new_tp, new_fp, new_tn, new_fn = _confusion(new_gate=True)
    old_pass, old_cost, old_esc = _project(new_gate=False)
    new_pass, new_cost, new_esc = _project(new_gate=True)
    cloud_pass = sum(1 for r in rows if r.get("cloud_passed")) / n
    cloud_cost = sum(float(r.get("cloud_cost") or 0.0) for r in rows) / n
    converted = [
        r for r in rows
        if r.get("judge_verdict") is True and not r["local_passed"]
        and tool_verdicts.get(r["prompt_full"]) is False
    ]
    added_over = [
        r for r in rows
        if r.get("judge_verdict") is True and r["local_passed"]
        and tool_verdicts.get(r["prompt_full"]) is False
    ]
    checked = sum(1 for p in tool_verdicts if p in target_prompts)
    abstained = sum(1 for p, v in tool_verdicts.items() if v is None)

    scope = "misses-only (ground-truth-peeking smoke)" if misses_only else "all recorded accepts"
    auto_summary = (
        f"input={input_path.name} judge={judge_model} k={gen_samples} "
        f"min_disagree={min_disagree} scope={scope} "
        f"checked={checked} abstained={abstained} "
        f"recall {old_tn}/{n_fail}->{new_tn}/{n_fail} fp {old_fp}->{new_fp} "
        f"over-esc {old_fn}->{new_fn} "
        f"routed {old_pass:.4f}@${old_cost*1000:.2f}/1k -> {new_pass:.4f}@${new_cost*1000:.2f}/1k "
        f"(cloud {cloud_pass:.4f}@${cloud_cost*1000:.2f}/1k)"
    )
    log_offline_run(decisions_db, mode="code-judge", task_count=checked,
                    notes=f"{auto_summary} | {notes}" if notes else auto_summary)

    if output_json:
        typer.echo(json.dumps({
            "judge_model": judge_model, "gen_samples": gen_samples,
            "min_disagree": min_disagree, "scope": scope,
            "n": n, "checked": checked, "abstained": abstained,
            "old": {"confusion": {"tp": old_tp, "fp": old_fp, "tn": old_tn, "fn": old_fn},
                    "pass_rate": round(old_pass, 4), "cost_per_1k": round(old_cost * 1000, 2),
                    "escalations": old_esc},
            "new": {"confusion": {"tp": new_tp, "fp": new_fp, "tn": new_tn, "fn": new_fn},
                    "pass_rate": round(new_pass, 4), "cost_per_1k": round(new_cost * 1000, 2),
                    "escalations": new_esc},
            "always_cloud": {"pass_rate": round(cloud_pass, 4),
                             "cost_per_1k": round(cloud_cost * 1000, 2)},
            "converted_misses": [r["prompt_full"][:80] for r in converted],
            "added_over_escalations": [r["prompt_full"][:80] for r in added_over],
        }, indent=2))
        return

    typer.echo(f"code-judge replay — judge={judge_model} k={gen_samples}, {scope}")
    typer.echo(f"  tool-checked {checked} accepts ({abstained} abstained)")
    typer.echo(f"  fail-recall  {old_tn}/{n_fail} -> {new_tn}/{n_fail}   "
               f"wrong-answers-served {old_fp} -> {new_fp}   over-escalations {old_fn} -> {new_fn}")
    typer.echo("")
    typer.echo(f"  {'policy':<36}{'pass@1':>10}{'$/1k':>10}{'esc':>6}")
    typer.echo("  " + "-" * 62)
    typer.echo(f"  {'routed (recorded gate)':<36}{old_pass:>10.4f}{f'${old_cost*1000:.2f}':>10}{old_esc:>6}")
    typer.echo(f"  {'routed (+code-judge, projected)':<36}{new_pass:>10.4f}{f'${new_cost*1000:.2f}':>10}{new_esc:>6}")
    typer.echo(f"  {'always-cloud (recorded)':<36}{cloud_pass:>10.4f}{f'${cloud_cost*1000:.2f}':>10}{'':>6}")
    if cloud_cost:
        typer.echo("")
        typer.echo(f"  projected: {100 * (1 - new_cost / cloud_cost):.1f}% cost cut at "
                   f"{100 * new_pass / cloud_pass:.1f}% of cloud pass@1")
    for r in converted:
        typer.echo(f"  converted miss: {r['prompt_full'][:76]}…")
    for r in added_over:
        typer.echo(f"  added over-escalation: {r['prompt_full'][:76]}…")


@report_app.command("reward-judge")
def reward_judge_cmd(
    results: Optional[list[Path]] = typer.Option(
        None, "--results", "-r",
        help="Force-explore cross JSONL(s) (prompt_full/output_full/actual_agent); "
             "repeatable. Default: the recorded P1 HumanEval+ cross.",
    ),
    bank: Path = typer.Option(
        DEFAULT_HUMANEVAL_BANK, "--bank", help="Gold bank (prompt + hidden tests) for re-grading"
    ),
    orderings: int = typer.Option(20, "--orderings", help="Shuffled prompt orderings per variant (each gets a fresh cold-start bandit)"),
    seed: int = typer.Option(42, "--seed", help="RNG seed (shuffles + synthetic-judge sampling)"),
    alpha: float = typer.Option(1.0, "--alpha", help="LinUCB exploration coefficient"),
    decay: float = typer.Option(0.98, "--decay", help="dLinUCB discount factor"),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db"),
    output_json: bool = typer.Option(False, "--json"),
    notes: Optional[str] = typer.Option(None, "--notes", help="Why this replay — logged to bench_runs."),
) -> None:
    """Offline reward-fidelity replay: does the judge-fed correctness
    coefficient fix Era 20's saturated success term?

    Zero new inference — re-grades the recorded P1 force-explore cross against
    the bank's hidden tests (CPU-only sandbox runs), then plays a fresh
    cold-start LinUCB over shuffled orderings under four reward variants:
    legacy (correctness=None), oracle (true pass), and synthetic judges at the
    measured plain/code operating points. All rewards come from the real
    RewardCalculator. Prints pass@1 vs the round-robin / best-static / oracle
    baselines derived from the same matrix, and PASS/FAIL per replay criterion.
    """
    results_paths = [p for p in (results or list(DEFAULT_P1_CROSS))]
    missing = [p for p in results_paths if not p.exists()]
    if missing:
        typer.echo(f"Results file(s) not found: {', '.join(str(p) for p in missing)}", err=True)
        raise typer.Exit(1)
    if not bank.exists():
        typer.echo(f"Gold bank not found: {bank}", err=True)
        raise typer.Exit(1)

    typer.echo(
        f"grading {len(results_paths)} results file(s) against {bank.name} "
        "(a few hundred sandboxed runs, CPU only)…", err=True,
    )
    try:
        report = run_reward_replay(
            bank, results_paths,
            n_orderings=orderings, seed=seed, alpha=alpha, decay=decay,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    env = report["environment"]
    base = report["baselines"]
    variants = report["variants"]
    criteria = report["criteria"]

    verdicts = {c["criterion"].split(" ")[0]: c["verdict"] for c in criteria}
    auto_summary = (
        f"bank={bank.name} n_prompts={env['n_prompts']} orderings={orderings} seed={seed} "
        f"pass@1 " + " ".join(f"{name}={v['pass_at_1']}" for name, v in variants.items())
        + f" rr={base['round_robin']} best_static={base['best_static']} "
        f"oracle_router={base['oracle_router']} verdicts={verdicts}"
    )
    log_offline_run(
        decisions_db, mode="reward-judge", task_count=env["n_prompts"],
        notes=f"{auto_summary} | {notes}" if notes else auto_summary,
    )

    if output_json:
        typer.echo(json.dumps(report, indent=2))
        return

    typer.echo("")
    typer.echo(f"reward-judge — offline reward-fidelity replay over {', '.join(p.name for p in results_paths)}")
    typer.echo(
        f"  environment: {env['n_prompts']} prompts x {len(env['arms'])} arms "
        f"({', '.join(a.split(':')[-1] for a in env['arms'])}), "
        f"{env['n_discriminating']} arm-discriminating, "
        f"{env['n_latency_backfilled']} rows latency-backfilled, "
        f"{env['n_dropped_incomplete']} bank prompts dropped (incomplete matrix)"
    )
    statics = "  ".join(f"static {a.split(':')[-1]}={v}" for a, v in base["static"].items())
    typer.echo(
        f"  baselines: round-robin={base['round_robin']}  {statics}  "
        f"oracle-router={base['oracle_router']}"
    )
    typer.echo(f"  bandit: cold-start LinUCB d=9 alpha={alpha} decay={decay}, "
               f"{orderings} orderings, seed={seed}")
    typer.echo("")
    arms = env["arms"]
    picks_hdr = "picks(" + "/".join(a.split(":")[-1] for a in arms) + ")"
    typer.echo(f"  {'variant':<14}{'pass@1':>10}{'±std':>8}{'Δrr':>9}{picks_hdr:>26}"
               f"{'disc-acc':>10}{'rew-gap':>9}{'rew↔pass':>10}")
    typer.echo("  " + "-" * 96)
    for name, v in variants.items():
        shares = "/".join(f"{v['pick_share'].get(a, 0.0)*100:.0f}%" for a in arms)
        disc = f"{v['disc_accuracy']:.3f}" if v["disc_accuracy"] is not None else "n/a"
        corr = f"{v['reward_pass_corr']:.3f}" if v["reward_pass_corr"] is not None else "n/a"
        typer.echo(
            f"  {name:<14}{v['pass_at_1']:>10.4f}{v['pass_at_1_std']:>8.4f}"
            f"{v['pass_at_1'] - base['round_robin']:>+9.4f}{shares:>26}"
            f"{disc:>10}{v['reward_gap']:>9.4f}{corr:>10}"
        )
    typer.echo("")
    typer.echo("  (rew-gap = arm mean-reward gap under expected correctness; "
               "rew↔pass = Pearson r between per-pull reward and true pass)")
    typer.echo("")
    typer.echo("  criteria:")
    for c in criteria:
        typer.echo(f"    [{c['verdict']:<4}] {c['criterion']}")
        typer.echo(f"           {c['detail']}")


@report_app.command("route-ceiling")
def route_ceiling_cmd(
    results: Optional[list[Path]] = typer.Option(
        None, "--results", "-r",
        help="Force-explore cross JSONL(s) for the arm ceiling; repeatable. "
             "Default: the recorded P1 HumanEval+ cross.",
    ),
    bank: Path = typer.Option(
        DEFAULT_HUMANEVAL_BANK, "--bank", help="Gold bank (prompt + hidden tests) for re-grading"
    ),
    cascade: Optional[Path] = typer.Option(
        DEFAULT_LIVE_ROUTE, "--cascade",
        help="live-route cascade JSONL for the escalation ceiling. Pass a "
             "non-existent path to skip that section.",
    ),
    skip_arm: bool = typer.Option(False, "--skip-arm", help="Skip the arm-selection ceiling"),
    k_values: str = typer.Option("3,5,10,20", "--k", help="Comma-separated kNN neighbourhood sizes to sweep"),
    permutations: int = typer.Option(2000, "--permutations", help="Label-permutation resamples for the significance test (0 disables)"),
    seed: int = typer.Option(42, "--seed", help="RNG seed for the permutation test"),
    decisions_db: Path = typer.Option(DEFAULT_DECISIONS_DB, "--decisions-db"),
    output_json: bool = typer.Option(False, "--json"),
    notes: Optional[str] = typer.Option(None, "--notes", help="Why this analysis — logged to bench_runs."),
) -> None:
    """How much can *any* router learn from the recorded data?

    Two offline ceilings, zero new inference:

    Arm selection — the oracle-vs-round-robin gap is the algebraic identity
    split/(2n) for two arms, so it is guaranteed positive whenever the arms
    ever disagree, noise included. A leave-one-out kNN probe with
    full-information neighbours (strictly more than any online learner sees)
    asks whether the split prompts are actually predictable, and a
    label-permutation test says whether the answer clears chance.

    Escalation — places the judge's operating point on the oracle-gate
    frontier and asks whether cheap features add fail-recall on top of the
    judge verdict at matched escalation rate (matched cost).
    """
    try:
        ks = tuple(int(x) for x in k_values.split(",") if x.strip())
    except ValueError:
        typer.echo(f"Invalid --k value: {k_values!r} (expected comma-separated ints)", err=True)
        raise typer.Exit(1)
    if not ks:
        typer.echo("--k must name at least one neighbourhood size", err=True)
        raise typer.Exit(1)

    results_paths = [p for p in (results or list(DEFAULT_P1_CROSS))] if not skip_arm else []
    missing = [p for p in results_paths if not p.exists()]
    if missing:
        typer.echo(f"Results file(s) not found: {', '.join(str(p) for p in missing)}", err=True)
        raise typer.Exit(1)
    if results_paths and not bank.exists():
        typer.echo(f"Gold bank not found: {bank}", err=True)
        raise typer.Exit(1)
    cascade_path = cascade if (cascade and cascade.exists()) else None
    if not results_paths and cascade_path is None:
        typer.echo("Nothing to analyse: no cross results and no cascade file.", err=True)
        raise typer.Exit(1)

    if results_paths:
        typer.echo(
            f"grading {len(results_paths)} cross file(s) against {bank.name} "
            "(a few hundred sandboxed runs, CPU only)…", err=True,
        )
    try:
        report = run_ceiling(
            bank if results_paths else None,
            results_paths or None,
            cascade_path,
            k_values=ks,
            n_permutations=permutations,
            seed=seed,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    arm = report.get("arm_ceiling")
    esc = report.get("escalation_ceiling")

    auto_bits = []
    if arm:
        auto_bits.append(
            f"arm={arm['verdict']} n={arm['stats']['n_prompts']} "
            f"split={arm['stats']['split']} oracle_gap={arm['stats']['oracle_over_best_static']}"
        )
    if esc:
        auto_bits.append(
            f"esc={esc['verdict']} judge_pass@1={esc['judge']['pass_at_1']} "
            f"frontier@rate={(esc['frontier_at_judge_rate'] or {}).get('pass_at_1')}"
        )
    auto_summary = f"route-ceiling k={','.join(str(k) for k in ks)} perms={permutations} " + " ".join(auto_bits)
    try:
        log_offline_run(
            decisions_db, mode="route-ceiling",
            task_count=(arm or {}).get("stats", {}).get("n_prompts", 0) or (esc or {}).get("n_rows", 0),
            notes=f"{auto_summary} | {notes}" if notes else auto_summary,
        )
    except sqlite3.Error as exc:
        # The ledger is a nice-to-have; losing minutes of sandboxed grading
        # because the decisions DB is absent (fresh checkout, CI) is not.
        typer.echo(f"warning: could not log to the experiment ledger ({exc})", err=True)

    if output_json:
        typer.echo(json.dumps(report, indent=2))
        return

    if arm:
        s = arm["stats"]
        env = arm.get("environment", {})
        typer.echo("")
        typer.echo("route-ceiling A — arm-selection ceiling")
        typer.echo(
            f"  environment: {s['n_prompts']} prompts x {s['n_arms']} arms "
            f"({', '.join(a.split(':')[-1] for a in env.get('arms', []))})"
        )
        typer.echo(
            f"  outcomes: {s['all_pass']} all-pass  {s['none_pass']} none-pass  "
            f"{s['split']} split"
        )
        typer.echo(
            f"  baselines: round-robin={s['round_robin']}  best-static={s['best_static']}  "
            f"oracle={s['oracle']}"
        )
        if "split_over_2n" in s:
            held = "holds" if s["identity_holds"] else "BROKEN"
            typer.echo(
                f"  identity: oracle - round-robin = {s['oracle_over_round_robin']} "
                f"= split/(2n) = {s['split_over_2n']}  [{held}]"
            )
            typer.echo(
                "            (the gap is guaranteed positive whenever the arms disagree — "
                "it measures disagreement, not skill)"
            )
        typer.echo("")
        typer.echo(f"  {'representation':<14}{'best k':>8}{'pass@1':>10}{'Δbest-static':>14}{'p':>9}  detail")
        typer.echo("  " + "-" * 82)
        for p in arm["probes"]:
            if not p["available"]:
                typer.echo(f"  {p['representation']:<14}{'—':>8}{'n/a':>10}{'—':>14}{'—':>9}  {p['detail']}")
                continue
            typer.echo(
                f"  {p['representation']:<14}{p['best_k']:>8}{p['pass_at_1']:>10.4f}"
                f"{p['gain_over_best_static']:>+14.4f}{p['p_value']:>9.4f}  {p['detail']}"
            )
        typer.echo("")
        typer.echo(f"  verdict: {arm['verdict']}")
        typer.echo(f"    {arm['detail']}")

    if esc:
        typer.echo("")
        typer.echo("route-ceiling B — escalation ceiling")
        typer.echo(
            f"  {esc['n_rows']} cascade rows: always-local={esc['always_local']}  "
            f"always-cloud={esc['always_cloud']} @ ${esc['always_cloud_cost']}/1k"
        )
        j = esc["judge"]
        typer.echo(
            f"  judge: esc-rate={j['esc_rate']}  fail-recall={j['fail_recall']} "
            f"({j['n_caught']}/{j['n_failed']})  over-esc={j['over_escalations']}  "
            f"pass@1={j['pass_at_1']} @ ${j['cost_per_1k']}/1k"
        )
        fr = esc["frontier_at_judge_rate"]
        if fr:
            typer.echo(
                f"  oracle gate at the same rate ({fr['esc_rate']}): pass@1={fr['pass_at_1']} "
                f"@ ${fr['cost_per_1k']}/1k  → {fr['pass_at_1'] - j['pass_at_1']:+.4f} headroom at equal spend"
            )
        typer.echo("")
        typer.echo(f"  {'representation':<14}{'judge?':>8}{'k':>5}{'pass@1':>10}{'recall':>9}{'Δjudge':>9}")
        typer.echo("  " + "-" * 60)
        for p in esc["probes"]:
            if not p.get("available"):
                typer.echo(
                    f"  {p['representation']:<14}{'—':>8}{'—':>5}{'n/a':>10}{'—':>9}{'—':>9}"
                    f"   {p.get('detail', '')}"
                )
                continue
            typer.echo(
                f"  {p['representation']:<14}{str(p['with_judge']):>8}{p['k']:>5}"
                f"{p['pass_at_1']:>10.4f}{p['fail_recall']:>9.4f}{p['delta_vs_judge']:>+9.4f}"
            )
        typer.echo("")
        typer.echo(f"  verdict: {esc['verdict']}")
        typer.echo(f"    {esc['detail']}")
