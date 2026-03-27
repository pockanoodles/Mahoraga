from __future__ import annotations
import aiosqlite


class MissionStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
