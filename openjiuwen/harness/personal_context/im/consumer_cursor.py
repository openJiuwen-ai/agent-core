"""im_consumer_cursors repository.

Each consumer (e.g. ``im-fts``) has one row with its acked seq.  The
``lease_until`` column is reserved for future cross-process leasing; the
local FTS consumer is single-process so the lease is best-effort.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Optional


class ConsumerCursorRepository:
    """CRUD over ``im_consumer_cursors``."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def register(self, consumer_id: str) -> None:
        """Ensure the consumer row exists (acked_seq defaults to 0)."""
        self._conn.execute(
            """
            INSERT INTO im_consumer_cursors (consumer_id, acked_seq, lease_until, updated_at)
            VALUES (?, 0, NULL, ?)
            ON CONFLICT(consumer_id) DO NOTHING
            """,
            (consumer_id, int(time.time() * 1000)),
        )

    def get_acked(self, consumer_id: str) -> int:
        row = self._conn.execute(
            "SELECT acked_seq FROM im_consumer_cursors WHERE consumer_id = ?",
            (consumer_id,),
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def ack(self, consumer_id: str, *, acked_seq: int, now_ms: Optional[int] = None) -> None:
        ts = int(now_ms if now_ms is not None else time.time() * 1000)
        self._conn.execute(
            """
            INSERT INTO im_consumer_cursors (consumer_id, acked_seq, lease_until, updated_at)
            VALUES (?, ?, NULL, ?)
            ON CONFLICT(consumer_id) DO UPDATE SET
                acked_seq = excluded.acked_seq,
                updated_at = excluded.updated_at
            WHERE excluded.acked_seq > acked_seq
            """,
            (consumer_id, int(acked_seq), ts),
        )

    def list_all(self) -> list[dict[str, object]]:
        rows = self._conn.execute(
            "SELECT consumer_id, acked_seq, lease_until, updated_at FROM im_consumer_cursors"
        ).fetchall()
        return [
            {
                "consumer_id": str(r[0]),
                "acked_seq": int(r[1]),
                "lease_until": r[2],
                "updated_at": int(r[3]),
            }
            for r in rows
        ]


__all__ = ["ConsumerCursorRepository"]
