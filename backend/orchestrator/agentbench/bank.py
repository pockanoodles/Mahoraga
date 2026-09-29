"""On-disk layout of one repo's bench, and the task record."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".mahoraga-v2" / "agentbench"


@dataclass
class Task:
    sha: str
    base: str  # the task-base commit, created inside this task's clone
    subject: str
    body: str
    gold_src: list[str]  # source .py files the real commit changed
    gold_lines: int  # added + removed lines across gold_src
    test_files: list[str]  # the commit's test modules: the targets
    f2p: list[str]  # pass at the commit, fail at the base: must pass after
    p2p: list[str]  # pass at the base across the full suite: must stay passing

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class SuiteConfig:
    """How to run this repo's tests. Fixed at mine time so every attempt is
    graded exactly the way its task was gated."""

    python: str
    pytest_args: list[str] = field(default_factory=list)


@dataclass
class Bench:
    """Paths for one benchmarked repo. `root` is overridable for tests."""

    repo: Path
    root: Path = field(default=DEFAULT_ROOT)

    def __post_init__(self) -> None:
        self.repo = Path(self.repo).resolve()
        self.root = Path(os.environ.get("MAHORAGA_AGENTBENCH_ROOT", self.root))

    @property
    def dir(self) -> Path:
        # Name for humans, hash so two checkouts called "app" don't collide.
        digest = hashlib.sha1(str(self.repo).encode()).hexdigest()[:8]
        return self.root / f"{self.repo.name}-{digest}"

    @property
    def tasks_path(self) -> Path:
        return self.dir / "tasks.jsonl"

    @property
    def dropped_path(self) -> Path:
        return self.dir / "dropped.json"

    @property
    def work(self) -> Path:
        return self.dir / "work"

    @property
    def home(self) -> Path:
        """Scratch HOME for every test run: tests at old commits may write to ~."""
        return self.dir / "home"

    @property
    def attempts(self) -> Path:
        return self.dir / "attempts"

    @property
    def config_path(self) -> Path:
        return self.dir / "config.json"

    def clone(self, sha: str) -> Path:
        return self.work / sha

    def load_config(self) -> SuiteConfig:
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"no bench for {self.repo} — run `orch agentbench mine` first")
        return SuiteConfig(**json.loads(self.config_path.read_text()))

    def save_config(self, config: SuiteConfig) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(asdict(config), indent=1))

    def load_tasks(self) -> list[Task]:
        if not self.tasks_path.exists():
            return []
        return [Task(**json.loads(line))
                for line in self.tasks_path.read_text().splitlines() if line.strip()]

    def save_tasks(self, tasks: list[Task]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tasks_path.write_text("".join(t.to_json() + "\n" for t in tasks))
