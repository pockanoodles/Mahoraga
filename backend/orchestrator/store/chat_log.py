from __future__ import annotations
import dataclasses
import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_log (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    user_message TEXT NOT NULL,
    assistant_response TEXT NOT NULL,
    worker_id TEXT NOT NULL DEFAULT '',
    cost_usd REAL NOT NULL DEFAULT 0.0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_log_user_id ON chat_log(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_log_created_at ON chat_log(created_at);
"""


@dataclasses.dataclass
class ChatLogEntry:
    id: str
    user_id: str
    mission_id: str
    user_message: str
    assistant_response: str
    worker_id: str
    cost_usd: float
    created_at: float


class ChatLogStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def migrate(self) -> None:
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def save(self, entry: ChatLogEntry) -> None:
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO chat_log
                (id, user_id, mission_id, user_message, assistant_response,
                 worker_id, cost_usd, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.id,
                entry.user_id,
                entry.mission_id,
                entry.user_message,
                entry.assistant_response,
                entry.worker_id,
                entry.cost_usd,
                entry.created_at,
            ),
        )
        await self._conn.commit()

    async def list_recent(self, user_id: str, limit: int = 20) -> list[ChatLogEntry]:
        async with self._conn.execute(
            """
            SELECT id, user_id, mission_id, user_message, assistant_response,
                   worker_id, cost_usd, created_at
            FROM chat_log
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            ChatLogEntry(
                id=row["id"],
                user_id=row["user_id"],
                mission_id=row["mission_id"],
                user_message=row["user_message"],
                assistant_response=row["assistant_response"],
                worker_id=row["worker_id"],
                cost_usd=row["cost_usd"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
