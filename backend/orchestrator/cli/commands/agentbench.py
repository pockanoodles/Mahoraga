"""
`orch agentbench` — benchmark coding agents on a repo's own history.

Subcommands
-----------

  orch agentbench mine [REPO]                  — turn commits into gated tasks
  orch agentbench run [REPO] --arm aider:qwen3.5:latest [--cond blind|feedback|both]
                             [--repeats K]
  orch agentbench report [REPO] [--json]

REPO defaults to the current directory and must be a git repo with a pytest
suite. State lives under ~/.mahoraga-v2/agentbench/; REPO is only read.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

import typer

from backend.orchestrator.agentbench.agents import parse_arm
from backend.orchestrator.agentbench.bank import Bench, SuiteConfig
from backend.orchestrator.agentbench.mine import find_candidates, mine
from backend.orchestrator.agentbench.report import outcome, render, summarise
from backend.orchestrator.agentbench.runner import (
    CONDITIONS, DEFAULT_TIMEOUT, load_attempts, run_matrix,
)

app = typer.Typer(
    name="agentbench",
    help="Benchmark coding agents + local models on a repo's own commit history.",
    no_args_is_help=True,
)

_REPO = typer.Argument(Path("."), help="Git repo to benchmark (default: cwd).")


def _default_python(repo: Path) -> str:
    venv = repo / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


@app.command("mine")
def mine_cmd(
    repo: Path = _REPO,
    limit: int = typer.Option(25, "--limit", help="Newest candidate commits to try."),
    max_lines: int = typer.Option(250, "--max-lines", help="Skip commits with more changed source lines."),
    rev: str = typer.Option("HEAD", "--rev", help="History to mine from."),
    commits: Optional[List[str]] = typer.Option(None, "--commit", help="Build exactly these commits."),
    python: Optional[str] = typer.Option(None, "--python", help="Interpreter for the repo's tests."),
    pytest_args: Optional[List[str]] = typer.Option(None, "--pytest-arg", help="Extra pytest arg, e.g. '-m not slow'."),
) -> None:
    """Build gated tasks from commits that changed both source and tests."""
    bench = Bench(repo)
    try:
        prior = bench.load_config()
    except FileNotFoundError:
        prior = None
    config = SuiteConfig(
        python=python or (prior.python if prior else _default_python(bench.repo)),
        pytest_args=list(pytest_args) if pytest_args else (prior.pytest_args if prior else []),
    )
    shas = list(commits) if commits else [
        c.sha for c in find_candidates(bench.repo, rev, limit, max_lines)]
    if not shas:
        typer.echo("no candidate commits (need commits changing both source and a test module)")
        raise typer.Exit(1)
    typer.echo(f"building {len(shas)} candidates into {bench.dir}")

    def progress(sha, task, reason):
        typer.echo(f"  {sha[:10]}  " + (
            f"ok  f2p={len(task.f2p)} p2p={len(task.p2p)} files={len(task.gold_src)} "
            f"lines={task.gold_lines}" if task else f"drop  {reason}"))

    tasks, dropped = mine(bench, config, shas, progress)
    typer.echo(f"{len(tasks)} tasks in the bench, {len(dropped)} dropped")


@app.command("run")
def run_cmd(
    repo: Path = _REPO,
    arms: List[str] = typer.Option(..., "--arm", help="<agent>:<model>, repeatable."),
    cond: str = typer.Option("blind", "--cond", help="blind, feedback, or both."),
    tasks: Optional[List[str]] = typer.Option(None, "--task", help="Only these task shas."),
    timeout: float = typer.Option(DEFAULT_TIMEOUT, "--timeout", help="Seconds per attempt."),
    repeats: int = typer.Option(1, "--repeats", min=1, help="Attempts per cell; >1 measures stability."),
) -> None:
    """Attempt every task with every arm. Resumable: finished attempts are skipped."""
    conds = CONDITIONS if cond == "both" else (cond,)
    if not set(conds) <= set(CONDITIONS):
        raise typer.BadParameter(f"--cond must be blind, feedback or both, not {cond!r}")
    try:
        agents = [parse_arm(a) for a in arms]
    except ValueError as e:
        raise typer.BadParameter(str(e))
    bench = Bench(repo)
    if not bench.load_tasks():
        typer.echo("no tasks — run `orch agentbench mine` first")
        raise typer.Exit(1)

    def progress(a):
        typer.echo(f"  {a.sha[:10]} {a.arm} {a.cond} r{a.rep}: {outcome(a)}  "
                   f"f2p={a.f2p_pass}/{a.f2p_total} regressions={a.regressions} "
                   f"{a.secs / 60:.1f}m")

    done = run_matrix(bench, agents, conds, set(tasks) if tasks else None, timeout,
                      repeats, progress)
    typer.echo(f"{len(done)} attempts run")


@app.command("report")
def report_cmd(
    repo: Path = _REPO,
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Per-arm resolve rates, failure modes, and a per-task matrix."""
    bench = Bench(repo)
    tasks, attempts = bench.load_tasks(), load_attempts(bench)
    if json_out:
        typer.echo(json.dumps({
            "repo": str(bench.repo), "tasks": len(tasks),
            "arms": [asdict(s) for s in summarise(tasks, attempts)],
        }, indent=2))
        return
    typer.echo(render(tasks, attempts))
