"""FtsIndexRepository — index maintenance on im_messages_fts + state.

The ``seg`` column is the only indexed column; original content lives in
``im_messages``.  This repository is write-path only (upsert / remove) plus
indexing diagnostics (``integrity_check``); the production search path is
``SqliteImSearchStore``, which filters by ``learning_eligible`` at query
time over the same tables.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from typing import Optional

from openjiuwen.harness.personal_context.im.bigram import to_index_segment


class FtsIndexRepository:
    """FTS5 index + rowid <-> message_id mapping."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(self, *, message_id: str, conversation_id: str, content_text: str, now_ms: Optional[int] = None) -> bool:
        """Insert/update one FTS row. Returns True if the index was modified.

        content_hash check skips no-op writes (avoids FTS churn when the
        same content is re-persisted).
        """
        if not message_id or not content_text:
            return False
        ts = int(now_ms if now_ms is not None else time.time() * 1000)
        content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
        existing = self._conn.execute(
            "SELECT rowid_alias, content_hash FROM im_messages_fts_state WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if existing is not None and str(existing[1]) == content_hash:
            return False
        seg = to_index_segment(content_text)
        if existing is not None:
            rowid_alias = int(existing[0])
            self._conn.execute(
                "UPDATE im_messages_fts SET seg = ? WHERE rowid = ?",
                (seg, rowid_alias),
            )
            self._conn.execute(
                """
                UPDATE im_messages_fts_state
                SET conversation_id = ?, content_hash = ?, indexed_at = ?
                WHERE message_id = ?
                """,
                (conversation_id, content_hash, ts, message_id),
            )
            return True
        cursor = self._conn.execute(
            "INSERT INTO im_messages_fts (seg) VALUES (?)",
            (seg,),
        )
        lastrowid = cursor.lastrowid
        if lastrowid is None:
            return False
        rowid_alias = int(lastrowid)
        self._conn.execute(
            """
            INSERT INTO im_messages_fts_state
                (rowid_alias, message_id, conversation_id, content_hash, indexed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (rowid_alias, message_id, conversation_id, content_hash, ts),
        )
        return True

    def remove(self, *, message_id: str) -> bool:
        """Delete one FTS row. Returns True if a row was removed."""
        if not message_id:
            return False
        row = self._conn.execute(
            "SELECT rowid_alias FROM im_messages_fts_state WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            return False
        rowid_alias = int(row[0])
        self._conn.execute("DELETE FROM im_messages_fts WHERE rowid = ?", (rowid_alias,))
        self._conn.execute(
            "DELETE FROM im_messages_fts_state WHERE rowid_alias = ?",
            (rowid_alias,),
        )
        return True

    def integrity_check(self) -> dict[str, int]:
        """Return counts of {fts_rows, state_rows, orphan_rows}.

        Orphan = state row whose rowid_alias has no matching FTS row.
        """
        fts_rows = int(self._conn.execute("SELECT COUNT(*) FROM im_messages_fts").fetchone()[0])
        state_rows = int(self._conn.execute("SELECT COUNT(*) FROM im_messages_fts_state").fetchone()[0])
        orphan_rows = int(
            self._conn.execute(
                """
                SELECT COUNT(*) FROM im_messages_fts_state s
                WHERE NOT EXISTS (SELECT 1 FROM im_messages_fts WHERE rowid = s.rowid_alias)
                """
            ).fetchone()[0]
        )
        return {"fts_rows": fts_rows, "state_rows": state_rows, "orphan_rows": orphan_rows}


__all__ = ["FtsIndexRepository"]
