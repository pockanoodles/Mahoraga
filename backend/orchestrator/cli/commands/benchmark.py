"""
benchmark.py — offline simulation CLI for Mahoraga routing strategies.

Runs a configurable number of synthetic tasks through one or more routing
strategies and reports cumulative regret, win-rate, and per-strategy totals.
"""
from __future__ import annotations
import random
import typer
from typing import Optional

app = typer.Typer(
    name="benchmark",
    help="Offline routing benchmark / simulation tools",
    no_args_is_help=True,
)

# Synthetic task pool: (goal, bucket, oracle_agent, latency_s, oracle_qual)
# oracle_agent = the agent that *should* win this task type
# oracle_qual  = expected quality when routed correctly (used as oracle reward)
_SYNTHETIC_TASKS: list[tuple[str, str, str, float, float]] = [
    # code tasks — aider wins
    ("Implement a binary search tree with insert and delete", "code", "aider", 12.0, 0.90),
    ("Write a Python decorator that retries on exception", "code", "aider", 8.0, 0.88),
    ("Create a REST API endpoint for user registration", "code", "aider", 15.0, 0.87),
    ("Refactor this module to use dependency injection", "code", "aider", 10.0, 0.86),
    ("Add type hints to all functions in this file", "code", "aider", 6.0, 0.85),
    # debug tasks — aider wins (has_error_kw)
    ("Fix the NullPointerException in auth.py line 42", "debug", "aider", 9.0, 0.88),
    ("Debug the crash when parsing malformed JSON input", "debug", "aider", 11.0, 0.86),
    ("Traceback shows KeyError in router.py — fix it", "debug", "aider", 8.0, 0.87),
    ("The timeout error in executor.py needs to be resolved", "debug", "aider", 10.0, 0.85),
    ("Broken import loop between context.py and reward.py", "debug", "aider", 7.0, 0.84),
    # research / general — ollama wins (fast, no overhead)
    ("Explain how transformer attention mechanisms work", "research", "ollama", 4.0, 0.82),
    ("What is the difference between TCP and UDP?", "research", "ollama", 3.0, 0.80),
    ("How does gradient descent find a minimum?", "research", "ollama", 3.5, 0.81),
    ("Compare SQL vs NoSQL databases for time-series data", "research", "ollama", 5.0, 0.79),
    ("Why does Python use GIL and when does it matter?", "research", "ollama", 4.0, 0.80),
    # plan tasks — ollama wins
    ("Design a microservices architecture for an e-commerce site", "plan", "ollama", 6.0, 0.78),
    ("Create a rollout plan for the new authentication system", "plan", "ollama", 5.0, 0.76),
    ("Outline the steps to migrate from SQLite to Postgres", "plan", "ollama", 4.5, 0.77),
    ("Draft a testing strategy for the bandit router", "plan", "ollama", 5.5, 0.75),
    ("Plan the refactoring of the legacy scheduler module", "plan", "ollama", 4.0, 0.74),
    # review tasks — ollama wins
    ("Review this PR for security issues in the auth flow", "review", "ollama", 5.0, 0.76),
    ("Analyse the performance of this database query", "review", "ollama", 4.0, 0.74),
    ("Summarize the differences in this diff", "review", "ollama", 3.0, 0.73),
    # refactor — aider wins
    ("Refactor the monolithic executor into smaller modules", "refactor", "aider", 13.0, 0.86),
    ("Extract constants from reward.py into a config file", "refactor", "aider", 7.0, 0.84),
    # general
    ("Write a brief summary of the Mahoraga project", "general", "ollama", 3.0, 0.75),
    ("Describe the LinUCB algorithm in plain English", "general", "ollama", 3.5, 0.74),
    ("What time zone should I use for a global SaaS product?", "general", "ollama", 2.5, 0.72),
]


def _make_task(goal: str):
    """Minimal task object compatible with TaskContext.from_task."""
    class _Task:
        pass
    t = _Task()
    t.goal = goal
    return t


def _simulated_reward(selected: str, oracle_agent: str, oracle_qual: float) -> float:
    """Return reward: oracle_qual when routed correctly, degraded otherwise."""
    if selected == oracle_agent:
        return oracle_qual
    # Wrong agent: oracle_qual * 0.5 ± small noise
    return max(0.0, oracle_qual * 0.50 + random.gauss(0, 0.05))


@app.command("simulate")
def simulate(
    tasks: int = typer.Option(50, "--tasks", "-n", help="Number of synthetic tasks to simulate"),
    strategies: Optional[str] = typer.Option(None, "--strategies", "-s", help="Comma-separated strategies (linucb,ucb1,thompson,static). Default: all"),
    seed: int = typer.Option(42, "--seed", help="Random seed for reproducibility"),
    warm_start: bool = typer.Option(False, "--warm-start", help="Warm-start LinUCB from ~/.mahoraga-v2/compatibility_matrix.json"),
    save_matrix: bool = typer.Option(False, "--save-matrix", help="Write oracle rewards to ~/.mahoraga-v2/compatibility_matrix.json after sim"),
):
    """Run an offline simulation of routing strategies on synthetic tasks."""
    from backend.orchestrator.routing.strategies.linucb import LinUCBRouter
    from backend.orchestrator.routing.strategies.ucb1 import UCB1Router
    from backend.orchestrator.routing.strategies.thompson import ThompsonSamplingRouter
    from backend.orchestrator.routing.strategies.static import StaticRouter
    from backend.orchestrator.routing.context import TaskContext
    from backend.orchestrator.routing.reward import RewardCalculator, TaskOutcome

    random.seed(seed)

    strategy_map = {
        "linucb":   LinUCBRouter,
        "ucb1":     UCB1Router,
        "thompson": ThompsonSamplingRouter,
        "static":   StaticRouter,
    }

    if strategies:
        selected_strategies = [s.strip() for s in strategies.split(",")]
        for s in selected_strategies:
            if s not in strategy_map:
                typer.echo(f"Unknown strategy: {s!r}. Options: {list(strategy_map)}", err=True)
                raise typer.Exit(1)
    else:
        selected_strategies = list(strategy_map)

    all_agents = ["ollama", "aider", "codex-cli", "gemini-cli"]
    reward_calc = RewardCalculator()

    results: dict[str, dict] = {}

    for sname in selected_strategies:
        typer.echo(f"\nStrategy: {sname}")
        router = strategy_map[sname]()

        if warm_start and sname == "linucb":
            from backend.orchestrator.routing.warm_start import (
                load_compatibility_matrix, warm_start_from_matrix,
            )
            matrix = load_compatibility_matrix()
            if matrix:
                warm_start_from_matrix(router, matrix)
                typer.echo(f"  [warm-start] Injected {sum(len(v) for v in matrix.values())} pseudo-observations")
            else:
                typer.echo("  [warm-start] No compatibility_matrix.json found — running cold", err=True)

        total_reward = 0.0
        oracle_reward = 0.0
        correct = 0

        for i in range(tasks):
            spec = _SYNTHETIC_TASKS[i % len(_SYNTHETIC_TASKS)]
            goal, bucket, oracle_agent, latency_s, oracle_qual = spec

            task_obj = _make_task(goal)
            context = TaskContext.from_task(task_obj)

            selected = router.select_agent(context, all_agents)
            reward = _simulated_reward(selected, oracle_agent, oracle_qual)
            oracle_reward += oracle_qual

            outcome = TaskOutcome(
                success=True,
                latency_s=latency_s,
                cost_usd=0.001,
                quality_score=reward,
                agent_name=selected,
            )
            computed_reward = reward_calc.compute(outcome)
            total_reward += computed_reward

            router.update(context, selected, computed_reward)

            if selected == oracle_agent:
                correct += 1

        win_rate = correct / tasks
        regret = oracle_reward - total_reward
        results[sname] = {
            "total_reward": round(total_reward, 3),
            "oracle_reward": round(oracle_reward, 3),
            "regret": round(regret, 3),
            "win_rate": round(win_rate, 3),
            "correct": correct,
            "tasks": tasks,
        }

        typer.echo(f"  tasks={tasks}  win_rate={win_rate:.1%}  total_reward={total_reward:.2f}  regret={regret:.2f}")

    typer.echo("\n--- Results ---")
    for sname, r in results.items():
        typer.echo(
            f"  {sname:<12}  win={r['win_rate']:.1%}  reward={r['total_reward']:.2f}"
            f"  regret={r['regret']:.2f}  correct={r['correct']}/{r['tasks']}"
        )

    if save_matrix:
        from backend.orchestrator.routing.warm_start import save_compatibility_matrix
        oracle_matrix: dict[str, dict[str, float]] = {}
        for t in _SYNTHETIC_TASKS:
            _, bucket, oracle_agent, _, oracle_qual = t
            oracle_matrix.setdefault(oracle_agent, {})[bucket] = round(oracle_qual, 3)
        save_compatibility_matrix(oracle_matrix)
        typer.echo("\n[saved] compatibility_matrix.json → ~/.mahoraga-v2/")


@app.command("report")
def report(
    json_out: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
    dpi: int = typer.Option(150, "--dpi", help="Ignored (reserved for future chart export)"),
):
    """Print a summary report from the last simulate run.

    Reads benchmark/results/strategy_results.json. Use --json for
    machine-readable output.
    """
    import json as _json
    from pathlib import Path as _Path
    results_path = (
        _Path(__file__).parent.parent.parent
        / "routing" / "benchmark" / "results" / "strategy_results.json"
    )
    if not results_path.exists():
        typer.echo("No results found. Run 'orch benchmark simulate' first.", err=True)
        raise typer.Exit(1)
    data = _json.loads(results_path.read_text())
    if json_out:
        typer.echo(_json.dumps(data, indent=2))
        return
    typer.echo("\n=== Benchmark Report ===\n")
    for name, r in data.items():
        typer.echo(
            f"  {name:<12}  reward={r.get('mean_reward', 0):.4f}"
            f"  regret={r.get('total_regret', 0):.2f}"
            f"  beta={r.get('regret_growth_exponent', 0):.3f}"
            f"  sublinear={'yes' if r.get('is_sublinear') else 'no'}"
        )


@app.command("ablation")
def ablation(
    tasks: int = typer.Option(200, "--tasks", "-n", help="Tasks per experiment run"),
    seed: int = typer.Option(42, "--seed", help="Random seed"),
    output: Optional[str] = typer.Option(None, "--output", help="Output directory (default: benchmark/results/ablation/)"),
    dpi: int = typer.Option(150, "--dpi", help="Chart DPI (use 300 for publication-quality)"),
):
    """Run full ablation study: 5 experiments, 5 regret charts, JSON + MD summary.

    Experiments: strategy comparison, warm-start, episodic memory,
    swap penalty, and bucket granularity.
    """
    from backend.orchestrator.routing.benchmark.ablation_study import run_ablation
    run_ablation(n_tasks=tasks, seed=seed, output_dir=output, dpi=dpi)


@app.command("live-report")
def live_report(
    db: Optional[str] = typer.Option(None, "--db", help="Path to routing_decisions.db"),
    output: Optional[str] = typer.Option(None, "--output", help="Output directory for charts"),
    json_out: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
    dpi: int = typer.Option(150, "--dpi", help="Chart DPI"),
):
    """Analyse real routing decisions from routing_decisions.db.

    Prints a text report and generates 3 charts: reward over time,
    exploration rate, and bucket distribution.
    """
    from backend.orchestrator.routing.benchmark.live_report import run_live_report
    run_live_report(db_path=db, output_dir=output, as_json=json_out, dpi=dpi)


@app.command("pareto-sweep")
def pareto_sweep(
    tasks: int = typer.Option(200, "--tasks", "-n", help="Tasks per config run (default 200)"),
    seed: int = typer.Option(42, "--seed", help="Random seed"),
    output: Optional[str] = typer.Option(None, "--output", help="Output directory (default: benchmark/results/)"),
    dpi: int = typer.Option(150, "--dpi", help="Chart DPI (use 300 for publication-quality)"),
):
    """Sweep (alpha, gamma, beta_swap) grid and find the Pareto knee-point config.

    Runs 100 configs × N tasks. Writes tuned_hyperparams.json to ~/.mahoraga-v2/
    for automatic loading by BanditRouter on next startup.
    """
    from backend.orchestrator.routing.benchmark.pareto_sweep import run_pareto_sweep
    run_pareto_sweep(n_tasks=tasks, seed=seed, output_dir=output, dpi=dpi)


@app.command("memory-mode")
def memory_mode(
    prompts: str = typer.Option(
        "adversarial",
        "--prompts",
        help="Prompt set: 'adversarial' (the 30 keyword-collision clusters) "
        "or 'synthetic' (the 28 well-separated baseline tasks).",
    ),
    seeds: int = typer.Option(
        10, "--seeds", help="Number of seeds (locked design decision #7: N=10)."
    ),
    repeats: int = typer.Option(
        5,
        "--repeats",
        help="Times each prompt is replayed within a seed run. Lower = "
        "memory accumulates less; higher = more chances for retrieval to "
        "engage. Default 5.",
    ),
    modes: str = typer.Option(
        "semantic,keyword,off",
        "--modes",
        help="Comma-separated memory modes to evaluate.",
    ),
    alphas: str = typer.Option(
        "0.20",
        "--alphas",
        help="Comma-separated α values to sweep (memory bias weight). "
        "Example: '0.0,0.05,0.10,0.20,0.30'. Off-mode runs once at α=0.0.",
    ),
    confidence_weighting: str = typer.Option(
        "off",
        "--confidence-weighting",
        help="'off' (default), 'on', or 'both' to compare confidence-weighted "
        "blending against the unweighted blend.",
    ),
    alpha_per_bucket: Optional[str] = typer.Option(
        None,
        "--alpha-per-bucket",
        help="JSON dict mapping bucket name to α override "
        "(e.g. '{\"research\": 0.0, \"code_editing\": 0.15}'). "
        "Applied to every non-off condition; missing buckets fall through "
        "to the per-condition global α.",
    ),
    strategy: str = typer.Option(
        "linucb",
        "--strategy",
        help="Bandit strategy. Options: linucb (global θ, default), "
        "linucb_per_bucket (per-classifier-bucket θ), ucb1, thompson, "
        "static.",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        help="Result directory (default: benchmarks/results/memory_mode_<set>_<ts>).",
    ),
    adversarial_path: str = typer.Option(
        "benchmarks/adversarial_prompts.json",
        "--adversarial-path",
        help="Path to the adversarial prompt JSON.",
    ),
) -> None:
    """Phase-4 evaluation: compare memory modes (semantic vs keyword vs off)
    on a held-out prompt set with N seeds and statistical aggregation."""
    from datetime import datetime, timezone
    from pathlib import Path as _Path
    from backend.orchestrator.routing.benchmark.memory_mode_eval import (
        load_adversarial, load_synthetic, run_eval,
    )

    if prompts == "adversarial":
        prompt_set = load_adversarial(_Path(adversarial_path).expanduser())
    elif prompts == "synthetic":
        prompt_set = load_synthetic()
    else:
        typer.echo(f"Unknown prompt set {prompts!r}. Use 'adversarial' or 'synthetic'.",
                   err=True)
        raise typer.Exit(1)

    mode_list = [m.strip() for m in modes.split(",") if m.strip()]
    seed_list = list(range(seeds))
    try:
        alpha_list = [float(a.strip()) for a in alphas.split(",") if a.strip()]
    except ValueError as exc:
        typer.echo(f"Failed to parse --alphas={alphas!r}: {exc}", err=True)
        raise typer.Exit(1) from exc
    cw_lower = confidence_weighting.strip().lower()
    if cw_lower in ("off", "false", "no", "0"):
        cw_list = [False]
    elif cw_lower in ("on", "true", "yes", "1"):
        cw_list = [True]
    elif cw_lower == "both":
        cw_list = [False, True]
    else:
        typer.echo(
            f"--confidence-weighting must be 'off', 'on', or 'both' "
            f"(got {confidence_weighting!r})",
            err=True,
        )
        raise typer.Exit(1)

    if output is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output = f"benchmarks/results/memory_mode_{prompts}_{ts}"
    out_dir = _Path(output).expanduser()

    pba_dict: Optional[dict] = None
    if alpha_per_bucket:
        try:
            import json as _json
            parsed = _json.loads(alpha_per_bucket)
            if not isinstance(parsed, dict):
                raise ValueError("not a JSON object")
            pba_dict = {
                str(k): float(v) for k, v in parsed.items()
                if isinstance(v, (int, float))
            }
        except (ValueError, _json.JSONDecodeError) as exc:
            typer.echo(
                f"--alpha-per-bucket is not valid JSON dict: {exc}", err=True
            )
            raise typer.Exit(1) from exc

    n_conditions = sum(
        len(alpha_list) * len(cw_list) if m != "off" else 1
        for m in mode_list
    )

    typer.echo(f"Prompts    : {prompts} ({len(prompt_set)} × {repeats} repeats)")
    typer.echo(f"Seeds      : {seeds}")
    typer.echo(f"Modes      : {', '.join(mode_list)}")
    typer.echo(f"α values   : {alpha_list}")
    typer.echo(f"Conf weight: {cw_list}")
    if pba_dict:
        typer.echo(f"Per-bucket α: {pba_dict}")
    typer.echo(f"Conditions : {n_conditions} × {seeds} seeds = {n_conditions * seeds} runs")
    typer.echo(f"Output     : {out_dir}")
    typer.echo("")
    typer.echo("Running…")

    summary = run_eval(
        prompts=prompt_set, modes=mode_list, seeds=seed_list,
        result_dir=out_dir, repeats=repeats,
        alphas=alpha_list, confidence_weighting=cw_list,
        alpha_per_bucket=pba_dict, strategy=strategy,
    )

    typer.echo("")
    typer.echo(f"Wrote {out_dir}/summary.md")
    typer.echo(f"Wrote {out_dir}/summary.json")
    typer.echo(f"Wrote {out_dir}/raw_results.json")
    typer.echo("")
    typer.echo("─── Headline (sorted by mean reward) ──────────")
    sorted_conds = sorted(
        summary["by_condition"].items(),
        key=lambda kv: -kv[1]["cumulative_reward"]["mean"],
    )
    for cond, m in sorted_conds:
        cr = m["cumulative_reward"]
        ac = m["accuracy"]
        typer.echo(
            f"  {cond:30s}  reward={cr['mean']:6.2f}±{cr['std']:5.2f}  "
            f"acc={ac['mean']:6.2%}"
        )


@app.command("paraphrase")
def paraphrase(
    pairs_path: str = typer.Option(
        "benchmarks/paraphrase_pairs.json",
        "--pairs-path",
        help="Path to paraphrase-pairs JSON (training_prompt + test_paraphrases per pair).",
    ),
    seeds: int = typer.Option(
        10, "--seeds", help="Number of seeds (locked design decision #7: N=10)."
    ),
    train_repeats: int = typer.Option(
        6,
        "--train-repeats",
        help="Times each training prompt is replayed during the train phase. "
        "Lower = less memory accumulation; higher = bandit converges harder.",
    ),
    modes: str = typer.Option(
        "semantic,keyword,off",
        "--modes",
        help="Comma-separated memory modes to evaluate.",
    ),
    alphas: str = typer.Option(
        "0.10",
        "--alphas",
        help="Comma-separated α values to sweep. Off-mode runs once at α=0.0.",
    ),
    confidence_weighting: str = typer.Option(
        "off",
        "--confidence-weighting",
        help="'off' (default), 'on', or 'both'.",
    ),
    strategy: str = typer.Option(
        "linucb",
        "--strategy",
        help="Bandit strategy. Options: linucb (default), linucb_per_bucket.",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        help="Result directory (default: benchmarks/results/paraphrase_<ts>).",
    ),
) -> None:
    """Phase-4 paraphrase-transfer benchmark — the actual A1 hypothesis test.

    Train phase: replay each training_prompt N times, accumulating bandit +
    memory state. Test phase: present each test_paraphrase ONCE (no
    observe(), no learning) and measure routing accuracy.

    Hypothesis: semantic-mode achieves higher test-phase accuracy than
    keyword/off, because semantic retrieval generalises across surface-form
    paraphrases via embedding similarity. Keyword retrieval should fail
    when the test paraphrase shares meaning but not keywords with training.
    """
    from datetime import datetime, timezone
    from pathlib import Path as _Path
    from backend.orchestrator.routing.benchmark.paraphrase_eval import (
        load_pairs, run_eval,
    )

    pairs = load_pairs(_Path(pairs_path).expanduser())
    mode_list = [m.strip() for m in modes.split(",") if m.strip()]
    seed_list = list(range(seeds))
    try:
        alpha_list = [float(a.strip()) for a in alphas.split(",") if a.strip()]
    except ValueError as exc:
        typer.echo(f"Failed to parse --alphas={alphas!r}: {exc}", err=True)
        raise typer.Exit(1) from exc
    cw_lower = confidence_weighting.strip().lower()
    if cw_lower in ("off", "false", "no", "0"):
        cw_list = [False]
    elif cw_lower in ("on", "true", "yes", "1"):
        cw_list = [True]
    elif cw_lower == "both":
        cw_list = [False, True]
    else:
        typer.echo(
            "--confidence-weighting must be 'off', 'on', or 'both'", err=True,
        )
        raise typer.Exit(1)

    if output is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output = f"benchmarks/results/paraphrase_{ts}"
    out_dir = _Path(output).expanduser()

    typer.echo(
        f"Pairs        : {len(pairs)} "
        f"({sum(len(p.test_paraphrases) for p in pairs)} test paraphrases)"
    )
    typer.echo(f"Seeds        : {seeds}")
    typer.echo(f"Train repeats: {train_repeats}")
    typer.echo(f"Modes        : {', '.join(mode_list)}")
    typer.echo(f"α values     : {alpha_list}")
    typer.echo(f"Conf weight  : {cw_list}")
    typer.echo(f"Output       : {out_dir}")
    typer.echo("")
    typer.echo("Running…")

    summary = run_eval(
        pairs=pairs, modes=mode_list, seeds=seed_list,
        result_dir=out_dir, train_repeats=train_repeats,
        alphas=alpha_list, confidence_weighting=cw_list,
        strategy=strategy,
    )

    typer.echo("")
    typer.echo(f"Wrote {out_dir}/summary.md")
    typer.echo(f"Wrote {out_dir}/summary.json")
    typer.echo(f"Wrote {out_dir}/raw_results.json")
    typer.echo("")
    typer.echo("─── Test-phase accuracy (sorted) ──────────────")
    sorted_conds = sorted(
        summary["by_condition"].items(),
        key=lambda kv: -kv[1]["test_accuracy"]["mean"],
    )
    for cond, m in sorted_conds:
        ac = m["test_accuracy"]
        typer.echo(
            f"  {cond:30s}  acc={ac['mean']:6.2%}±{ac['std']:5.2%}  "
            f"CI95=[{ac['ci95'][0]:6.2%}, {ac['ci95'][1]:6.2%}]"
        )


@app.command("bootstrap")
def bootstrap(
    tasks: int = typer.Option(200, "--tasks", "-n", help="Number of synthetic tasks to write"),
    seed: int = typer.Option(42, "--seed", help="Random seed for reproducibility"),
    strategy: str = typer.Option(
        "linucb_per_bucket", "--strategy",
        help="Bandit strategy to use (linucb, linucb_per_bucket, ucb1, thompson)",
    ),
    state_path: Optional[str] = typer.Option(
        None, "--state-path",
        help="Bandit state path (default: temp file — does not pollute ~/.mahoraga-v2/)",
    ),
    db_path: Optional[str] = typer.Option(
        None, "--db",
        help="Decisions DB path (default: ~/.mahoraga-v2/routing_decisions.db)",
    ),
    reset: bool = typer.Option(
        False, "--reset",
        help="Drop existing decisions DB before writing (use with caution).",
    ),
):
    """Bootstrap routing_decisions.db with synthetic labelled rows.

    Pipes the synthetic task pool through the *real* BanditRouter so that
    log_decision + log_outcome populate the DB. Generates training data for
    A3 (`orch quality train|eval`) without waiting for organic traffic.

    Each task gets a TaskOutcome with success=True and quality_score derived
    from the same oracle reward simulator `simulate` uses.
    """
    import random as _random
    import tempfile
    from pathlib import Path as _Path
    import sqlite3 as _sqlite3

    from backend.orchestrator.routing.bandit_router import BanditRouter
    from backend.orchestrator.routing.decision_log import DecisionLogger
    from backend.orchestrator.routing.reward import TaskOutcome
    from backend.orchestrator.routing.strategies.static import classify_bucket
    from backend.orchestrator.routing.context import TaskContext

    _random.seed(seed)
    db = _Path(db_path).expanduser() if db_path else _Path.home() / ".mahoraga-v2" / "routing_decisions.db"

    if reset and db.exists():
        # Clear *only* the decisions table; keep bench_runs intact.
        with _sqlite3.connect(str(db)) as conn:
            conn.execute("DELETE FROM decisions")
        typer.echo(f"  [reset] cleared decisions in {db}")

    sp = _Path(state_path) if state_path else _Path(tempfile.mkstemp(prefix="mahoraga_bootstrap_", suffix=".json")[1])

    logger = DecisionLogger(db_path=db)
    router = BanditRouter(
        strategy=strategy,
        registry=None,
        logger=logger,
        state_path=sp,
    )

    all_agents = ["ollama", "aider", "codex-cli", "gemini-cli"]

    written = 0
    correct = 0
    bucket_counts: dict[str, int] = {}

    for i in range(tasks):
        spec = _SYNTHETIC_TASKS[i % len(_SYNTHETIC_TASKS)]
        goal, _bucket_label, oracle_agent, latency_s, oracle_qual = spec
        task_obj = _make_task(goal)
        ctx = TaskContext.from_task(task_obj)
        bucket = classify_bucket(ctx)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        selected = router.route(task_obj, available_agents=all_agents)
        reward = _simulated_reward(selected, oracle_agent, oracle_qual)
        if selected == oracle_agent:
            correct += 1

        outcome = TaskOutcome(
            success=True,
            latency_s=latency_s,
            cost_usd=0.001,
            quality_score=reward,
            agent_name=selected,
        )
        # observe() runs strategy.update + memory + logger.log_outcome.
        router.observe(task_obj, outcome)
        written += 1

    win_rate = correct / max(1, tasks)
    typer.echo(f"  wrote {written} rows  win_rate={win_rate:.1%}  strategy={strategy}")
    typer.echo(f"  bucket distribution: {bucket_counts}")
    typer.echo(f"  db: {db}")


@app.command("refresh")
def refresh(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Re-run the local harness and refresh stored rankings."""
    import httpx
    _BASE_URL = "http://localhost:8001"
    typer.echo("Refreshing rankings from live history + harness data...")
    try:
        r = httpx.get(f"{_BASE_URL}/api/rankings", params={"refresh": "true"}, timeout=120.0)
        r.raise_for_status()
        data = r.json()
    except httpx.ConnectError:
        typer.echo("Cannot connect to server. Is it running?", err=True)
        raise typer.Exit(1)

    if json_output:
        import json
        print(json.dumps(data, indent=2))
    else:
        rows = data.get("rankings", [])
        typer.echo(f"Rankings refreshed. {len(rows)} agents ranked overall.")
        if rows:
            typer.echo(f"Top agent: {rows[0]['agent']}")
