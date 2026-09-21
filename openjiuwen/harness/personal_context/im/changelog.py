"""im_changelog repository (Outbox pattern).

Records "what changed" so consumers (FTS, vector, ...) can catch up
asynchronously.  FTS failure never blocks persist because the changelog is
written in the same transaction as the message but consumed later.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ChangelogEntry:
    seq: int
    op: str  # 'upsert' / 'delete'
    entity_type: str  # 'message'
    entity_id: str  # im_messages.id
    channel_id: str
    domain: str  # 'chat'
    occurred_at: int  # business time (sent_at)
    emitted_at: int  # write time
    payload_ref: Optional[str]  # always None (raw records removed, decision D2)
    digest: str  # sha256(content_text)


class ChangelogRepository:
    """CRUD over ``im_changelog``."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(
        self,
        *,
        op: str,
        entity_type: str,
        entity_id: str,
        channel_id: str,
        domain: str,
        occurred_at: int,
        payload_ref: Optional[str],
        digest: str,
        emitted_at: Optional[int] = None,
    ) -> int:
        """Append one row, return the autoincrement seq."""
        ts = int(emitted_at if emitted_at is not None else time.time() * 1000)
        cursor = self._conn.execute(
            """
            INSERT INTO im_changelog (
                op, entity_type, entity_id, channel_id, domain,
                occurred_at, emitted_at, payload_ref, digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (op, entity_type, entity_id, channel_id, domain, int(occurred_at), ts, payload_ref, digest),
        )
        lastrowid = cursor.lastrowid
        return int(lastrowid) if lastrowid is not None else 0

    def head(self) -> int:
        """Return the highest seq in the changelog, or 0 if empty."""
        row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM im_changelog").fetchone()
        return int(row[0]) if row is not None else 0

    def changes_since(self, after_seq: int, *, domain: str = "chat", limit: int = 500) -> list[ChangelogEntry]:
        """Return up to ``limit`` entries with seq > after_seq for ``domain``."""
        rows = self._conn.execute(
            """
            SELECT seq, op, entity_type, entity_id, channel_id, domain,
                   occurred_at, emitted_at, payload_ref, digest
            FROM im_changelog
            WHERE domain = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (domain, int(after_seq), int(limit)),
        ).fetchall()
        return [
            ChangelogEntry(
                seq=int(r[0]),
                op=str(r[1]),
                entity_type=str(r[2]),
                entity_id=str(r[3]),
                channel_id=str(r[4]),
                domain=str(r[5]),
                occurred_at=int(r[6]),
                emitted_at=int(r[7]),
                payload_ref=(str(r[8]) if r[8] is not None else None),
                digest=str(r[9]),
            )
            for r in rows
        ]


__all__ = ["ChangelogRepository", "ChangelogEntry"]
