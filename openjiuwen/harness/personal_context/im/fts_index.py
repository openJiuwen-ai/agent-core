"""FtsIndexRepository — upsert/search/remove on im_messages_fts + state.

The ``seg`` column is the only indexed column; original content lives in
``im_messages`` and is fetched back via the state table's rowid mapping.

Search deliberately does NOT filter by ``learning_eligible`` (decision D8):
the index stays complete and consumers that need the learning scope filter
at query time (e.g. the future im_search tool joins ``im_messages`` with
``learning_eligible = 1``), because eligibility can only transition 1 -> 0
and query-time filtering is equivalent.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from typing import Any, Optional

from openjiuwen.harness.personal_context.im.bigram import build_match_expr, to_index_segment, to_query_token_tiers


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

    def search(
        self,
        query: str,
        *,
        scope: Optional[dict[str, Any]] = None,
        limit: int = 50,
        learning_eligible_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Run a two-tier FTS query (strict -> relaxed) and return hits.

        Each hit: {message_id, conversation_id, score}.
        ``scope`` may contain:
            channel_id: str
            conversation_ids: list[str]
            since_ms: int

        ``learning_eligible_only`` (decision D8): when True, join
        ``im_messages`` and keep only rows with ``learning_eligible = 1``.
        """
        tiers = to_query_token_tiers(query)
        if not tiers:
            return []
        match_expr = build_match_expr(tiers)
        hits = self._run_match(match_expr, scope=scope, limit=limit, learning_eligible_only=learning_eligible_only)
        if not hits and len(tiers) > 1:
            relaxed_expr = build_match_expr([tiers[1]])
            hits = self._run_match(
                relaxed_expr, scope=scope, limit=limit, learning_eligible_only=learning_eligible_only
            )
        return hits

    def _run_match(
        self,
        match_expr: str,
        *,
        scope: Optional[dict[str, Any]] = None,
        limit: int,
        learning_eligible_only: bool = False,
    ) -> list[dict[str, Any]]:
        if not match_expr:
            return []
        sql = (
            "SELECT s.message_id AS message_id, s.conversation_id AS conversation_id, "
            "bm25(im_messages_fts) AS score "
            "FROM im_messages_fts "
            "JOIN im_messages_fts_state s ON s.rowid_alias = im_messages_fts.rowid "
        )
        if learning_eligible_only:
            sql += "JOIN im_messages m ON m.id = s.message_id AND m.learning_eligible = 1 "
        sql += "WHERE im_messages_fts MATCH ? "
        params: list[Any] = [match_expr]
        if scope:
            channel_id = scope.get("channel_id")
            if isinstance(channel_id, str) and channel_id:
                sql += "AND s.conversation_id IN (SELECT id FROM im_conversations WHERE channel_id = ?) "
                params.append(channel_id)
            conversation_ids = scope.get("conversation_ids")
            if isinstance(conversation_ids, list) and conversation_ids:
                placeholders = ",".join("?" for _ in conversation_ids)
                sql += f"AND s.conversation_id IN ({placeholders}) "
                params.extend(str(c) for c in conversation_ids)
        sql += "ORDER BY score LIMIT ?"
        params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "message_id": str(r[0]),
                "conversation_id": str(r[1]),
                "score": float(r[2]),
            }
            for r in rows
        ]

    def fetch_metadata_batch(
        self,
        message_ids: list[str],
        *,
        learning_eligible_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Pull sender / sent_at / content_text from im_messages for the hits.

        The FTS table stores only segmented tokens; callers backfill the
        result structure through this method.  ``learning_eligible_only``
        filters at query time (decision D8).
        """
        if not message_ids:
            return []
        placeholders = ",".join("?" for _ in message_ids)
        sql = f"""
            SELECT id, channel_id, conversation_id, sender_account, sender_name,
                   content_text, sent_at
            FROM im_messages
            WHERE id IN ({placeholders})
        """
        if learning_eligible_only:
            sql += " AND learning_eligible = 1"
        rows = self._conn.execute(sql, [str(m) for m in message_ids]).fetchall()
        return [
            {
                "id": str(r[0]),
                "channel_id": str(r[1]),
                "conversation_id": str(r[2]),
                "sender_account": r[3] if r[3] is not None else None,
                "sender_name": r[4] if r[4] is not None else None,
                "content_text": r[5] if r[5] is not None else "",
                "sent_at": int(r[6]),
            }
            for r in rows
        ]

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
