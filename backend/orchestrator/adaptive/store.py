from __future__ import annotations
import time
from typing import Optional
import aiosqlite

from .models import AdaptationCategory, UserAdaptation, UserProfile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS user_adaptations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL,
    last_reinforced REAL NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_adaptations_user_id ON user_adaptations(user_id);
CREATE INDEX IF NOT EXISTS idx_adaptations_category ON user_adaptations(category);
"""


class AdaptiveStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def migrate(self) -> None:
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    # ── Profiles ────────────────────────────────────────────────────────────

    async def save_profile(self, profile: UserProfile) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO user_profiles VALUES (?, ?, ?)",
            (profile.user_id, profile.created_at, profile.updated_at),
        )
        await self._conn.commit()

    async def get_profile(self, user_id: str) -> Optional[UserProfile]:
        async with self._conn.execute(
            "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return UserProfile(
            user_id=row["user_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── Adaptations ─────────────────────────────────────────────────────────

    async def save_adaptation(self, adapt: UserAdaptation) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO user_adaptations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                adapt.id,
                adapt.user_id,
                adapt.category.value,
                adapt.key,
                adapt.value,
                adapt.confidence,
                adapt.last_reinforced,
                adapt.created_at,
            ),
        )
        await self._conn.commit()

    async def list_adaptations(
        self,
        user_id: str,
        category: Optional[AdaptationCategory] = None,
    ) -> list[UserAdaptation]:
        if category is not None:
            sql = (
                "SELECT * FROM user_adaptations WHERE user_id = ? AND category = ?"
                " ORDER BY confidence DESC"
            )
            params = (user_id, category.value)
        else:
            sql = (
                "SELECT * FROM user_adaptations WHERE user_id = ?"
                " ORDER BY confidence DESC"
            )
            params = (user_id,)

        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()

        return [
            UserAdaptation(
                id=row["id"],
                user_id=row["user_id"],
                category=AdaptationCategory(row["category"]),
                key=row["key"],
                value=row["value"],
                confidence=row["confidence"],
                last_reinforced=row["last_reinforced"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def reinforce(self, adaptation_id: str, new_confidence: float) -> None:
        await self._conn.execute(
            "UPDATE user_adaptations SET confidence = ?, last_reinforced = ? WHERE id = ?",
            (new_confidence, time.time(), adaptation_id),
        )
        await self._conn.commit()

    async def decay_stale(
        self, user_id: str, days: int = 30, factor: float = 0.5
    ) -> int:
        cutoff = time.time() - days * 86400
        async with self._conn.execute(
            "SELECT id, confidence FROM user_adaptations"
            " WHERE user_id = ? AND last_reinforced < ?",
            (user_id, cutoff),
        ) as cur:
            rows = await cur.fetchall()

        count = 0
        for row in rows:
            new_conf = row["confidence"] * factor
            await self._conn.execute(
                "UPDATE user_adaptations SET confidence = ? WHERE id = ?",
                (new_conf, row["id"]),
            )
            count += 1

        if count:
            await self._conn.commit()

        return count
