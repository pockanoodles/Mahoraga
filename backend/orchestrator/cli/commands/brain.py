"""
`orch brain` — A4 brain retrieval inspector.

Subcommands
-----------

  orch brain status                   — index size + per-kind counts
  orch brain query "task description" — top-k similar brain entries

Read-only by design; the index is rebuilt in-process on each call.
"""
from __future__ import annotations

import json

import typer

from backend.orchestrator.routing.brain_retrieval import (
    BrainIndex,
    resolve_brain_dir,
    resolve_top_k,
)


app = typer.Typer(
    name="brain",
    help="Inspect the brain/ retrieval index.",
    no_args_is_help=True,
)


@app.command()
def status() -> None:
    """Show index size, brain dir, and per-kind counts."""
    idx = BrainIndex()
    n = idx.build()
    by_kind: dict[str, int] = {}
    for e in idx.entries:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
    typer.echo(
        json.dumps(
            {
                "brain_dir": str(resolve_brain_dir()),
                "indexed": n,
                "available": idx.available,
                "by_kind": by_kind,
            },
            indent=2,
        )
    )


@app.command()
def query(
    text: str = typer.Argument(..., help="Task description / question"),
    k: int = typer.Option(None, "--k", help="Top-k (defaults to env / 3)"),
) -> None:
    """Query the brain index and print top-k hits with similarity scores."""
    idx = BrainIndex()
    n = idx.build()
    if not idx.available:
        typer.echo(
            json.dumps({"available": False, "indexed": n, "hits": []}, indent=2)
        )
        raise typer.Exit(code=0)
    hits = idx.query(text, k=k or resolve_top_k())
    typer.echo(
        json.dumps(
            {
                "query": text,
                "indexed": n,
                "available": True,
                "hits": [h.to_dict() for h in hits],
            },
            indent=2,
        )
    )
