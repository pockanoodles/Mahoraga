from __future__ import annotations
import typer
from .commands.mission import app as mission_app
from .commands.plan import app as plan_app
from .commands.run import app as run_app
from .commands.task import app as task_app
from .commands.benchmark import app as benchmark_app
from .commands.bench import app as bench_app
from .commands.eval import app as eval_app
from .commands.rankings import app as rankings_app
from .commands.agent_cmd import app as agent_app
from .commands.memory import app as memory_app
from .commands.quality import app as quality_app
from .commands.brain import app as brain_app
from .commands.metrics import app as metrics_app
from .commands.budget import app as budget_app
from .commands.quarantine import app as quarantine_app
from .commands import ops

app = typer.Typer(
    name="orch",
    help="Orchestrator v2 CLI",
    no_args_is_help=True,
)

app.add_typer(mission_app, name="mission")
app.add_typer(plan_app, name="plan")
app.add_typer(run_app, name="run")
app.add_typer(task_app, name="task")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(bench_app, name="bench")
app.add_typer(eval_app, name="eval")
app.add_typer(rankings_app, name="rankings")
app.add_typer(agent_app, name="agent")
app.add_typer(memory_app, name="memory")
app.add_typer(quality_app, name="quality")
app.add_typer(brain_app, name="brain")
app.add_typer(metrics_app, name="metrics")
app.add_typer(budget_app, name="budget")
app.add_typer(quarantine_app, name="quarantine")

# Flat commands
app.command("status")(ops.status)
app.command("events")(ops.events)
app.command("approve")(ops.approve)
app.command("reject")(ops.reject)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
