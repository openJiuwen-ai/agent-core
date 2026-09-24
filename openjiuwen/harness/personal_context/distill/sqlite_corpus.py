"""SQLite-backed CorpusPort over PersonalContext ``im_context.db``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from openjiuwen.harness.personal_context.distill.corpus import _downsample
from openjiuwen.harness.personal_context.distill.types import CorpusMessage
from openjiuwen.harness.personal_context.im.scheduler import open_im_context_db

_ELIGIBLE_SELECT = """
SELECT
    id,
    channel_id,
    conversation_id,
    content_text,
    sent_at,
    is_self,
    sender_account,
    sender_name,
    learning_eligible
FROM im_messages
WHERE learning_eligible = 1
  AND sent_at >= ?
  AND sent_at < ?
  AND content_text IS NOT NULL
  AND TRIM(content_text) != ''
ORDER BY sent_at ASC
"""


def _db_path(home: str | Path) -> Path:
    return Path(home).expanduser() / "im" / "im_context.db"


def _map_is_self(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _row_to_message(row: sqlite3.Row) -> CorpusMessage:
    return CorpusMessage(
        id=str(row["id"]),
        channel_id=str(row["channel_id"]),
        conversation_id=str(row["conversation_id"]),
        content_text=str(row["content_text"] or ""),
        sent_at_ms=int(row["sent_at"]),
        is_self=_map_is_self(row["is_self"]),
        sender_account=row["sender_account"],
        sender_name=row["sender_name"],
        learning_eligible=int(row["learning_eligible"]),
    )


class SqliteImCorpus:
    """Read-only corpus adapter over ``<home>/im/im_context.db``."""

    def __init__(self, home: str | Path) -> None:
        self._home = Path(home).expanduser()

    def list_messages(
        self,
        *,
        window_start_ms: int,
        window_end_ms: int,
        max_messages: int,
    ) -> tuple[list[CorpusMessage], bool]:
        selected = self._load_eligible(
            window_start_ms=int(window_start_ms),
            window_end_ms=int(window_end_ms),
        )
        return _downsample(selected, max_messages)

    def count_eligible_since(self, *, cursor_ms: int, until_ms: int) -> int:
        return len(
            self._load_eligible(
                window_start_ms=int(cursor_ms),
                window_end_ms=int(until_ms),
            )
        )

    def _load_eligible(
        self,
        *,
        window_start_ms: int,
        window_end_ms: int,
    ) -> list[CorpusMessage]:
        db_path = _db_path(self._home)
        if not db_path.is_file():
            return []
        conn = open_im_context_db(self._home)
        try:
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                _ELIGIBLE_SELECT,
                (window_start_ms, window_end_ms),
            ).fetchall()
            return [_row_to_message(row) for row in rows]
        finally:
            conn.close()


__all__ = ["SqliteImCorpus"]
