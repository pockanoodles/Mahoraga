"""
`orch replay` — L3.2 episode replay engine CLI.

Subcommands
-----------

  orch replay run [--strategy NAME] [--alpha X] [--decay X] [--limit N]
                  [--estimator naive|constant] [--db PATH] [--json]

Re-executes the most-recent N decisions under a hypothetical strategy
config and prints cumulative reward + delta vs. actual.

Replay is a SCREENING tool. A config that loses on replay almost
certainly loses live; a config that wins on replay needs A/B
confirmation before adoption.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from backend.orchestrator.routing.counterfactual import get_estimator
from backend.orchestrator.routing.decision_log import _DEFAULT_DB_PATH
from backend.orchestrator.routing.replay import (
    ReplayResult,
    load_episodes,
    replay,
)


app = typer.Typer(
    name="replay",
    help="Re-execute logged decisions under a hypothetical strategy config.",
    no_args_is_help=True,
)


def _render(result: ReplayResult) -> str:
    lines: list[str] = []
    lines.append(f"=== Replay: {result.config_name} ===")
    lines.append(f"  episodes:        {result.n_episodes}")
    lines.append(
        f"  same-agent picks: {result.n_pick_matches}  "
        f"overrides: {result.n_overrides}"
    )
    lines.append(f"  estimator:       {result.estimator}")
    lines.append(
        f"  estimator hits/fallbacks: "
        f"{result.n_estimator_used}/{result.n_estimator_fallbacks}"
    )
    lines.append(
        f"  cumulative reward: actual={result.cumulative_actual_reward:.3f}  "
        f"replay={result.cumulative_replay_reward:.3f}  "
        f"Δ={result.delta:+.3f}"
    )
    if result.delta > 0:
        lines.append("  → replay would have OUTPERFORMED actual on this slice")
    elif result.delta < 0:
        lines.append("  → replay would have UNDERPERFORMED actual on this slice")
    else:
        lines.append("  → replay matched actual (delta=0)")
    return "\n".join(lines)


@app.command()
def run(
    strategy: str = typer.Option(
        "linucb_per_bucket", "--strategy",
        help="Strategy to replay: linucb | linucb_per_bucket",
    ),
    alpha: float = typer.Option(1.0, "--alpha"),
    decay: float = typer.Option(0.98, "--decay"),
    bucket_pooling_weight: float = typer.Option(
        0.5, "--bucket-pooling-weight",
        help="Per-bucket bandit only: cross-bucket prior weight (0–1).",
    ),
    estimator: str = typer.Option(
        "naive", "--estimator",
        help="Counterfactual estimator: naive | constant",
    ),
    estimator_default: float = typer.Option(
        0.5, "--estimator-default",
        help="Reward to use when estimator returns nothing (e.g. agent never observed).",
    ),
    estimator_min_support: int = typer.Option(
        5, "--estimator-min-support",
        help="Min observations needed for naive_mean to return an estimate.",
    ),
    estimator_value: float = typer.Option(
        0.5, "--estimator-value",
        help="ConstantEstimator return value (only used when --estimator=constant).",
    ),
    strategy_filter: Optional[str] = typer.Option(
        None, "--filter-strategy",
        help="Only replay episodes logged under this strategy name.",
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-n",
        help="Replay the most-recent N decisions; default = all.",
    ),
    db: Path = typer.Option(_DEFAULT_DB_PATH, "--db"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run replay and print cumulative reward + delta."""
    episodes = load_episodes(
        db_path=db, limit=limit, strategy_filter=strategy_filter,
    )
    if not episodes:
        typer.echo(
            f"no replay-eligible episodes in {db} "
            "(need rows with context_vector + reward)",
            err=True,
        )
        raise typer.Exit(code=1)

    if estimator == "constant":
        est = get_estimator("constant", db_path=db, value=estimator_value)
    else:
        est = get_estimator(
            estimator, db_path=db, min_support=estimator_min_support,
        )

    result = replay(
        episodes,
        strategy_name=strategy,
        db_path=db,
        estimator=est,
        estimator_default=estimator_default,
        alpha=alpha,
        decay=decay,
        bucket_pooling_weight=bucket_pooling_weight,
    )
    if json_out:
        typer.echo(json.dumps(result.to_dict(), indent=2))
        return
    typer.echo(_render(result))
