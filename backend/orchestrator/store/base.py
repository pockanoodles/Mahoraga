from __future__ import annotations
from pathlib import Path
import aiosqlite

from .missions import MissionStore
from .tasks import TaskStore
from .artifacts import ArtifactStore
from .events import EventStore
from .chat_log import ChatLogStore
from .metrics import MetricsStore

DEFAULT_DB_PATH = Path.home() / ".mahoraga" / "mahoraga.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    background TEXT NOT NULL DEFAULT '',
    success_condition TEXT NOT NULL DEFAULT '',
    context_refs TEXT NOT NULL DEFAULT '[]',
    global_constraints TEXT NOT NULL DEFAULT '[]',
    preferences TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    phases TEXT NOT NULL DEFAULT '[]',
    worker_strategy TEXT NOT NULL DEFAULT '{}',
    validation_strategy TEXT NOT NULL DEFAULT '{}',
    task_graph_shape TEXT NOT NULL DEFAULT 'linear',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'paused',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    parent_task_id TEXT,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '[]',
    context_refs TEXT NOT NULL DEFAULT '[]',
    done_criteria TEXT NOT NULL DEFAULT '',
    dependencies TEXT NOT NULL DEFAULT '[]',
    constraints TEXT NOT NULL DEFAULT '[]',
    preferred_worker_type TEXT,
    required_capabilities TEXT NOT NULL DEFAULT '[]',
    escalation_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_run_id ON tasks(run_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS task_attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'assigned',
    error_code TEXT NOT NULL DEFAULT '',
    blocking_reason TEXT NOT NULL DEFAULT '',
    started_at REAL,
    ended_at REAL,
    summary TEXT NOT NULL DEFAULT '',
    output TEXT NOT NULL DEFAULT '',
    artifact_refs TEXT NOT NULL DEFAULT '[]',
    validator_refs TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_attempts_task_id ON task_attempts(task_id);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    type TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_task_id ON artifacts(task_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_run_id ON artifacts(run_id);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT,
    attempt_id TEXT,
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
"""


_MIGRATIONS = [
    # v1: add output column to task_attempts (was missing from original schema)
    "ALTER TABLE task_attempts ADD COLUMN output TEXT NOT NULL DEFAULT ''",
    # v2: implicit quality signal column
    "ALTER TABLE task_metrics ADD COLUMN implicit_quality REAL DEFAULT NULL",
]


async def migrate(conn: aiosqlite.Connection) -> None:
    await conn.executescript(_SCHEMA)

    # Additive migrations: run each once, skip if column/index already exists.
    for sql in _MIGRATIONS:
        try:
            await conn.execute(sql)
            await conn.commit()
        except Exception:
            pass  # Column already exists — safe to ignore

    await conn.commit()


class Store:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self.missions = MissionStore(conn)
        self.tasks = TaskStore(conn)
        self.artifacts = ArtifactStore(conn)
        self.events = EventStore(conn)
        self.chat_log = ChatLogStore(conn)
        self.metrics = MetricsStore(conn)

    async def close(self) -> None:
        await self._conn.close()

    @classmethod
    async def connect(cls, db_path: Path | str = DEFAULT_DB_PATH) -> Store:
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        await migrate(conn)
        store = cls(conn)
        # chat_log runs its own migration — must come after base schema
        await store.chat_log.migrate()
        # metrics runs its own migration for task_metrics table
        await store.metrics.migrate()
        return store
