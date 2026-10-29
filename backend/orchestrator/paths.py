"""Where Mahoraga keeps its state: $MAHORAGA_HOME, else ~/.mahoraga-v2.

Every module that stores something (benches, the decision log, the bandit,
config.json) builds its path from home(), so one variable moves all of it, for
a second install, the demo or a test. A path a module resolves when it loads
sees the variable as it was then, the way it saw HOME before, so set it in the
shell and not in .env, which the service reads later.
"""
from __future__ import annotations

import os
from pathlib import Path

# The directory under the user's home when MAHORAGA_HOME isn't set. Renaming it
# would strand every install's state, so a new default needs a one-time move.
DIRNAME = ".mahoraga-v2"


def home() -> Path:
    """The state directory. Nothing is created here: whatever writes first does."""
    override = os.environ.get("MAHORAGA_HOME")
    # Made absolute, so the service hands launchd the directory the CLI uses: the
    # daemon runs from a working directory of its own.
    return Path(override).expanduser().absolute() if override else Path.home() / DIRNAME
