"""
`orch quality` — A3 learned quality scorer (train / eval / predict).

Subcommands
-----------

  orch quality train  [--db PATH] [--out PATH] [--accept-threshold X]
  orch quality eval   [--db PATH] [--seed N] [--test-frac X]
  orch quality predict --task "..." --agent NAME

The trainer reads `~/.mahoraga-v2/routing_decisions.db` (the bandit's
decision log), assembles (handcraft_9 ⊕ agent-onehot) features and
binary success/quality labels, fits a logistic regression, and writes
`~/.mahoraga-v2/quality_predictor.json`.

It is NOT yet wired into the live reward pipeline — `predict_proba()` is
exposed for opt-in use by callers (escalation, dashboards) while we
calibrate against the heuristic quality score on more data.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import typer

from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.quality_predictor import (
    DECISION_DB_PATH,
    DEFAULT_ACCEPT_THRESHOLD,
    DEFAULT_ITERS,
    DEFAULT_L2,
    DEFAULT_LR,
    QUALITY_PREDICTOR_META_PATH,
    QUALITY_PREDICTOR_PATH,
    QualityModel,
    _read_meta,
    evaluate,
    fit,
    load_training_rows,
    maybe_retrain,
    reset_loaded_model,
    retrain_and_swap,
    staleness_check,
    write_meta,
)


app = typer.Typer(
    name="quality",
    help="Train/evaluate the A3 learned quality predictor.",
    no_args_is_help=True,
)


@app.command()
def train(
    db: Path = typer.Option(DECISION_DB_PATH, help="Path to routing_decisions.db"),
    out: Path = typer.Option(QUALITY_PREDICTOR_PATH, help="Output model path"),
    accept_threshold: float = typer.Option(
        DEFAULT_ACCEPT_THRESHOLD,
        help="quality_score >= this counts as a positive label",
    ),
    l2: float = typer.Option(DEFAULT_L2, help="L2 regularisation"),
    lr: float = typer.Option(DEFAULT_LR, help="SGD learning rate"),
    iters: int = typer.Option(DEFAULT_ITERS, help="Gradient descent iterations"),
) -> None:
    """Fit the predictor from the decisions DB and save it to disk."""
    rows = load_training_rows(db_path=db, accept_threshold=accept_threshold)
    if not rows:
        typer.echo("No labelled rows in decisions DB.", err=True)
        raise typer.Exit(code=1)
    model = fit(
        rows,
        l2=l2,
        lr=lr,
        iters=iters,
        accept_threshold=accept_threshold,
    )
    model.save(out)
    # A3: persist the metadata file alongside the model so staleness
    # checks have something to compare against on next startup.
    write_meta(model, episode_count=len(rows))
    reset_loaded_model()
    typer.echo(
        f"trained on {model.n_train} rows ({len(model.agents)} agents), "
        f"train AUC={model.train_auc:.3f}, "
        f"loss={model.train_loss:.3f}, "
        f"pos_rate={model.train_pos_rate:.3f}"
    )
    typer.echo(f"saved → {out}")


@app.command()
def eval(
    db: Path = typer.Option(DECISION_DB_PATH, help="Path to routing_decisions.db"),
    seed: int = typer.Option(42),
    test_frac: float = typer.Option(0.25, help="Held-out fraction"),
    accept_threshold: float = typer.Option(DEFAULT_ACCEPT_THRESHOLD),
    l2: float = typer.Option(DEFAULT_L2),
    lr: float = typer.Option(DEFAULT_LR),
    iters: int = typer.Option(DEFAULT_ITERS),
) -> None:
    """Held-out AUC + Spearman correlation vs. observed quality_score."""
    rows = load_training_rows(db_path=db, accept_threshold=accept_threshold)
    if len(rows) < 4:
        typer.echo(
            f"Only {len(rows)} labelled rows — need at least 4 to split.",
            err=True,
        )
        raise typer.Exit(code=1)
    _, report = evaluate(
        rows,
        test_frac=test_frac,
        seed=seed,
        accept_threshold=accept_threshold,
        l2=l2, lr=lr, iters=iters,
    )
    typer.echo(json.dumps(report.__dict__, indent=2))


@app.command()
def predict(
    task: str = typer.Option(..., help="Task description (goal text)"),
    agent: str = typer.Option(..., help="Candidate agent name"),
    model_path: Path = typer.Option(QUALITY_PREDICTOR_PATH),
) -> None:
    """Score a single (task, agent) pair using the trained predictor."""
    if not Path(model_path).exists():
        typer.echo(
            f"No model at {model_path}. Run `orch quality train` first.",
            err=True,
        )
        raise typer.Exit(code=1)
    model = QualityModel.load(model_path)

    class _T:  # minimal task shim
        title = task
        goal = task

    ctx = TaskContext.from_task(_T())
    proba = model.predict_proba(ctx.to_vector(), agent)
    typer.echo(
        json.dumps(
            {
                "task": task,
                "agent": agent,
                "p_success": round(proba, 4),
                "model_n_train": model.n_train,
                "model_train_auc": round(model.train_auc, 4),
            },
            indent=2,
        )
    )


@app.command()
def inspect(
    meta_path: Path = typer.Option(QUALITY_PREDICTOR_META_PATH),
    db: Path = typer.Option(DECISION_DB_PATH),
) -> None:
    """Print the trained-model metadata + current staleness state."""
    meta = _read_meta(meta_path)
    report = staleness_check(db_path=db, meta_path=meta_path)
    out = {
        "meta": meta,
        "staleness": report.to_dict(),
    }
    typer.echo(json.dumps(out, indent=2, default=str))


@app.command()
def retrain(
    db: Path = typer.Option(DECISION_DB_PATH),
    model_path: Path = typer.Option(QUALITY_PREDICTOR_PATH),
    meta_path: Path = typer.Option(QUALITY_PREDICTOR_META_PATH),
    force: bool = typer.Option(
        False, "--force",
        help="Skip staleness check and retrain unconditionally.",
    ),
) -> None:
    """Run the staleness check and retrain if needed (or force).

    Refuses to swap the model in if test AUC < the safeguard threshold —
    a degenerate retrain (corrupt DB, drift) leaves the prior model
    untouched and reports `accepted: false` in the output.
    """
    if force:
        result = retrain_and_swap(
            db_path=db, model_path=model_path, meta_path=meta_path,
        )
        typer.echo(json.dumps(
            {"forced": True, "outcome": result}, indent=2, default=str,
        ))
        return
    result = maybe_retrain(
        db_path=db, model_path=model_path, meta_path=meta_path,
    )
    typer.echo(json.dumps(result, indent=2, default=str))
