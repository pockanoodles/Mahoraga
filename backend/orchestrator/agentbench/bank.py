"""On-disk layout of one repo's bench, and the task record."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".mahoraga-v2" / "agentbench"

_HASH_SUFFIX = re.compile(r"-[0-9a-f]{8}$")


def bench_root() -> Path:
    """Where benches live: $MAHORAGA_AGENTBENCH_ROOT, else ~/.mahoraga-v2/agentbench."""
    return Path(os.environ.get("MAHORAGA_AGENTBENCH_ROOT", DEFAULT_ROOT))


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
    """Paths for one benchmarked repo. `root` is overridable for tests.

    A bench found on disk (`from_dir`) has no repo to hash its directory from,
    so it keeps the directory it was found in, and its repo is None."""

    repo: Path | None
    root: Path = field(default=DEFAULT_ROOT)
    found_at: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.found_at is not None:
            self.found_at = Path(self.found_at)
            self.root = self.found_at.parent
            return
        self.repo = Path(self.repo).resolve()
        self.root = Path(os.environ.get("MAHORAGA_AGENTBENCH_ROOT", self.root))

    @classmethod
    def from_dir(cls, path: str | Path) -> Bench:
        """A bench already on disk, found by its directory. The directory name
        hashes the repo's path, which can't be run backwards. Where the repo was
        is in the run manifests (manifest.py), if the bench has been run."""
        return cls(repo=None, found_at=Path(path))

    @property
    def dir(self) -> Path:
        if self.found_at is not None:
            return self.found_at
        # Name for humans, hash so two checkouts called "app" don't collide.
        digest = hashlib.sha1(str(self.repo).encode()).hexdigest()[:8]
        return self.root / f"{self.repo.name}-{digest}"

    @property
    def name(self) -> str:
        """The repo's name for people: the directory name without its hash."""
        return _HASH_SUFFIX.sub("", self.dir.name)

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
                f"no bench for {self.repo or self.name} — run `mahoraga audit mine` first")
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


def find_benches(root: Path | None = None) -> list[Bench]:
    """Every bench under `root`: a directory holding a config.json or a tasks.jsonl.
    Anything else in there (agent-home, a log) is not a bench. In name order."""
    root = bench_root() if root is None else Path(root)
    if not root.is_dir():
        return []
    return [Bench.from_dir(d) for d in sorted(root.iterdir())
            if d.is_dir() and ((d / "config.json").is_file() or (d / "tasks.jsonl").is_file())]
