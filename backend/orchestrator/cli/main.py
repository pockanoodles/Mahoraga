from __future__ import annotations
import typer
from .commands.mission import app as mission_app
from .commands.plan import app as plan_app
from .commands.run import app as run_app
from .commands.task import app as task_app
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

# Flat commands
app.command("status")(ops.status)
app.command("events")(ops.events)
app.command("approve")(ops.approve)
app.command("reject")(ops.reject)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
