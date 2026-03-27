from __future__ import annotations
import aiosqlite


class TaskStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
