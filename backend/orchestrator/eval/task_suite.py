from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class TaskDef:
    id: str
    text: str
    bucket: str
    difficulty: str
    tags: list[str] = field(default_factory=list)
    timeout_s: float | None = None
    expected_artifacts: list[str] = field(default_factory=list)


@dataclass
class TaskSuite:
    name: str
    seed: int
    tasks: list[TaskDef]


def load_suite(path: Path) -> TaskSuite:
    raw = yaml.safe_load(path.read_text())
    tasks = [
        TaskDef(
            id=t["id"],
            text=t["text"],
            bucket=t["bucket"],
            difficulty=t["difficulty"],
            tags=t.get("tags", []),
            timeout_s=t.get("timeout_s"),
            expected_artifacts=t.get("expected_artifacts", []),
        )
        for t in raw["tasks"]
    ]
    return TaskSuite(name=raw["suite"], seed=raw.get("seed", 42), tasks=tasks)
