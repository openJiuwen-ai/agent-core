"""SQLite/FTS5 implementation of ``ImSearchPort`` (OJ-06, decision D10).

All SQL and FTS5 syntax for IM search lives in this module; ``search.py``
stays storage-agnostic.  Reads use short-lived read-only connections
(``PRAGMA query_only=ON``) so the search path never blocks or races the
learning scheduler's writer connection (WAL mode), never creates the
database, and never mutates it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

from openjiuwen.core.common.logging import LogManager
from openjiuwen.harness.personal_context.im.bigram import (
    build_match_expr,
    to_query_token_tiers,
)
from openjiuwen.harness.personal_context.im.scheduler import open_im_context_db
from openjiuwen.harness.personal_context.im.search import (
    ImSearchHit,
    ImSearchQuery,
)
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

im_logger = LogManager.get_logger("im_learning")

DEFAULT_MAX_CONTENT_CHARS = 2000
MAX_LIMIT = 50
TRUNCATION_SUFFIX = "…[截断]"


def _escape_like(text: str) -> str:
    """Escape LIKE wildcards (``%`` ``_`` ``\\``) so user input matches literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqliteImSearchStore:
    """``ImSearchPort`` over ``<home>/im/im_context.db`` (read-only)."""

    def __init__(self, home: str | Path, *, max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS) -> None:
        self._home = Path(home).expanduser()
        self._max_content_chars = max(1, int(max_content_chars))

    def _db_path(self) -> Path:
        return self._home / "im" / "im_context.db"

    def search(self, query: ImSearchQuery) -> tuple[list[ImSearchHit], int, bool]:
        keyword = (query.keyword or "").strip()
        if not keyword:
            raise build_error(
                StatusCode.CONTEXT_PROACTIVE_IM_SEARCH_EXECUTION_ERROR,
                msg="keyword is required (OJ-06 D9)",
            )
        db_path = self._db_path()
        if not db_path.is_file():
            # Read path never creates the database: missing store = empty corpus.
            return [], 0, False
        conn = open_im_context_db(self._home)
        try:
            conn.execute("PRAGMA query_only=ON")
            return self._search_on_conn(conn, query, keyword)
        except sqlite3.DatabaseError as exc:
            im_logger.exception("im.search.failed db=%s", db_path)
            raise build_error(
                StatusCode.CONTEXT_PROACTIVE_IM_SEARCH_EXECUTION_ERROR,
                msg="im search failed",
                cause=exc,
            ) from exc
        finally:
            try:
                conn.close()
            except sqlite3.Error:
                im_logger.exception("im.search.conn_close_failed")

    # ------------------------------------------------------------------ internals

    def _search_on_conn(
        self,
        conn: sqlite3.Connection,
        query: ImSearchQuery,
        keyword: str,
    ) -> tuple[list[ImSearchHit], int, bool]:
        tiers = to_query_token_tiers(keyword)
        if not tiers:
            # Punctuation-only keyword: no indexable tokens, no matches.
            return [], 0, False
        conversation_ids = self._resolve_conversation_ids(conn, query)
        if conversation_ids == []:
            # refs were given but none resolved: empty result, not an error.
            return [], 0, False

        match_expr = build_match_expr([tiers[0]])
        rows, total = self._run_match(conn, match_expr, query, conversation_ids)
        if not rows and len(tiers) > 1:
            # strict tier found nothing: retry with the relaxed tier (D8 strategy).
            match_expr = build_match_expr([tiers[1]])
            rows, total = self._run_match(conn, match_expr, query, conversation_ids)

        offset = max(0, int(query.offset))
        hits = [self._to_hit(row) for row in rows]
        truncated = (offset + len(hits)) < total
        return hits, total, truncated

    def _resolve_conversation_ids(
        self,
        conn: sqlite3.Connection,
        query: ImSearchQuery,
    ) -> Optional[list[str]]:
        """Resolve ``conversation_refs`` to internal conversation ids.

        Returns None when no refs were given (no filter); an empty list when
        refs were given but none resolved (empty result).  Resolution order
        (D12): exact ``(channel_id, external_id)`` first, then title LIKE.
        """
        refs = [ref.strip() for ref in query.conversation_refs if ref and ref.strip()]
        if not refs:
            return None
        resolved: list[str] = []
        for ref in refs:
            row = self._find_conversation_exact(conn, query.channel_id, ref)
            if row is not None:
                resolved.append(row)
                continue
            resolved.extend(self._find_conversations_by_title(conn, query.channel_id, ref))
        return list(dict.fromkeys(resolved))

    @staticmethod
    def _find_conversation_exact(
        conn: sqlite3.Connection,
        channel_id: Optional[str],
        external_id: str,
    ) -> Optional[str]:
        if channel_id:
            row = conn.execute(
                "SELECT id FROM im_conversations WHERE channel_id = ? AND external_id = ?",
                (channel_id, external_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM im_conversations WHERE external_id = ?",
                (external_id,),
            ).fetchone()
        return str(row[0]) if row is not None else None

    @staticmethod
    def _find_conversations_by_title(
        conn: sqlite3.Connection,
        channel_id: Optional[str],
        ref: str,
    ) -> list[str]:
        pattern = f"%{_escape_like(ref)}%"
        sql = "SELECT id FROM im_conversations WHERE title LIKE ? ESCAPE '\\'"
        params: list[Any] = [pattern]
        if channel_id:
            sql += " AND channel_id = ?"
            params.append(channel_id)
        rows = conn.execute(sql, params).fetchall()
        return [str(row[0]) for row in rows]

    def _run_match(
        self,
        conn: sqlite3.Connection,
        match_expr: str,
        query: ImSearchQuery,
        conversation_ids: Optional[list[str]],
    ) -> tuple[list[sqlite3.Row], int]:
        """Run one MATCH expression with the full filter set; return (rows, total).

        All filtering happens in SQL (never in memory) so ``limit``/``offset``
        keep their meaning: a page of 50 FTS hits of which only 3 match the
        sender filter yields exactly 3 rows with total=3.
        """
        where_sql, params = self._build_filters(query, conversation_ids, match_expr)
        base_sql = (
            "FROM im_messages_fts "
            "JOIN im_messages_fts_state s ON s.rowid_alias = im_messages_fts.rowid "
            "JOIN im_messages m ON m.id = s.message_id AND m.learning_eligible = 1 "
            "JOIN im_conversations c ON c.id = m.conversation_id "
            f"WHERE {where_sql}"
        )
        total = int(conn.execute(f"SELECT COUNT(*) {base_sql}", params).fetchone()[0])
        if total == 0:
            return [], 0
        limit = max(1, min(MAX_LIMIT, int(query.limit)))
        offset = max(0, int(query.offset))
        rows = conn.execute(
            "SELECT m.id, m.channel_id, m.conversation_id, c.title, "
            "m.sender_account, m.sender_name, m.is_self, m.sent_at, m.content_text "
            f"{base_sql} ORDER BY bm25(im_messages_fts) LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return list(rows), total

    @staticmethod
    def _build_filters(
        query: ImSearchQuery,
        conversation_ids: Optional[list[str]],
        match_expr: str,
    ) -> tuple[str, list[Any]]:
        clauses = ["im_messages_fts MATCH ?"]
        params: list[Any] = [match_expr]
        if query.channel_id:
            clauses.append("m.channel_id = ?")
            params.append(query.channel_id)
        if conversation_ids is not None:
            placeholders = ",".join("?" for _ in conversation_ids)
            clauses.append(f"m.conversation_id IN ({placeholders})")
            params.extend(conversation_ids)
        if query.sender:
            sender = query.sender.strip()
            clauses.append("(m.sender_account = ? OR LOWER(m.sender_name) LIKE ? ESCAPE '\\')")
            params.append(sender)
            params.append(f"%{_escape_like(sender.lower())}%")
        if query.since_ms is not None:
            clauses.append("m.sent_at >= ?")
            params.append(int(query.since_ms))
        if query.until_ms is not None:
            clauses.append("m.sent_at <= ?")
            params.append(int(query.until_ms))
        return " AND ".join(clauses), params

    def _to_hit(self, row: sqlite3.Row) -> ImSearchHit:
        content = row[8] if row[8] is not None else ""
        if len(content) > self._max_content_chars:
            content = content[: self._max_content_chars] + TRUNCATION_SUFFIX
        is_self = row[6]
        return ImSearchHit(
            message_id=str(row[0]),
            channel_id=str(row[1]),
            conversation_id=str(row[2]),
            conversation_title=row[3] if row[3] is not None else None,
            sender_account=row[4] if row[4] is not None else None,
            sender_name=row[5] if row[5] is not None else None,
            is_self=None if is_self is None else bool(is_self),
            sent_at=int(row[7]),
            content_text=content,
        )


__all__ = ["SqliteImSearchStore", "MAX_LIMIT"]
