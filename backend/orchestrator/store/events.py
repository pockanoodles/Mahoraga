from __future__ import annotations
import aiosqlite


class EventStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
