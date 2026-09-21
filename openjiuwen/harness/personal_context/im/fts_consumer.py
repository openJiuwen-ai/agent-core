"""FtsConsumer — drains im_changelog and feeds FtsIndexRepository.

Pull-based: ``drain_once()`` is called after persist_batch commits (by the
IM learning scheduler, as an independent stage with its own lease).  The
FTS consumer is the simplest consumer; a vector consumer would follow the
same pattern.

Indexing is deliberately NOT gated by ``learning_eligible`` (decision D8):
the index stays complete; consumers filter at query time.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from openjiuwen.core.common.logging import LogManager

from openjiuwen.harness.personal_context.im.changelog import ChangelogRepository
from openjiuwen.harness.personal_context.im.consumer_cursor import ConsumerCursorRepository
from openjiuwen.harness.personal_context.im.fts_index import FtsIndexRepository

im_logger = LogManager.get_logger("im_learning")

FTS_CONSUMER_ID = "im-fts"


class FtsConsumer:
    """Drain changelog entries into the FTS index."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._changelog = ChangelogRepository(conn)
        self._cursors = ConsumerCursorRepository(conn)
        self._fts = FtsIndexRepository(conn)

    def ensure_registered(self, *, now_ms: Optional[int] = None) -> None:
        self._cursors.register(FTS_CONSUMER_ID)

    def acked_seq(self) -> int:
        return self._cursors.get_acked(FTS_CONSUMER_ID)

    def drain_once(self, *, now_ms: Optional[int] = None, max_entries: int = 500) -> int:
        """Process up to ``max_entries`` changelog rows. Returns count processed."""
        self.ensure_registered()
        after = self.acked_seq()
        entries = self._changelog.changes_since(after, domain="chat", limit=max_entries)
        if not entries:
            return 0
        # Pull content_text for all upserts in one round-trip.
        upsert_ids = [e.entity_id for e in entries if e.op == "upsert" and e.entity_type == "message"]
        msg_lookup = self._fetch_messages_batch(upsert_ids) if upsert_ids else {}
        processed = 0
        last_seq = after
        for entry in entries:
            try:
                if entry.op == "upsert" and entry.entity_type == "message":
                    msg = msg_lookup.get(entry.entity_id)
                    if msg is None:
                        # Message row may have been deleted concurrently; skip.
                        last_seq = max(last_seq, entry.seq)
                        continue
                    # Intentionally no learning_eligible gate here; see module docstring (D8).
                    self._fts.upsert(
                        message_id=entry.entity_id,
                        conversation_id=msg["conversation_id"],
                        content_text=msg["content_text"],
                        now_ms=now_ms,
                    )
                elif entry.op == "delete" and entry.entity_type == "message":
                    self._fts.remove(message_id=entry.entity_id)
                processed += 1
                last_seq = max(last_seq, entry.seq)
            except Exception:  # noqa: BLE001
                im_logger.exception(
                    "im.fts.consumer.entry_failed entry_seq=%s entity_id=%s", entry.seq, entry.entity_id
                )
                # Continue processing other entries; do not advance cursor past
                # the failure so a retry will pick up this entry again.
                break
        if last_seq > after:
            self._conn.commit()
            self._cursors.ack(FTS_CONSUMER_ID, acked_seq=last_seq, now_ms=now_ms)
            self._conn.commit()
        return processed

    def _fetch_messages_batch(self, message_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not message_ids:
            return {}
        placeholders = ",".join("?" for _ in message_ids)
        rows = self._conn.execute(
            f"""
            SELECT id, conversation_id, content_text
            FROM im_messages
            WHERE id IN ({placeholders})
            """,
            [str(m) for m in message_ids],
        ).fetchall()
        return {
            str(r[0]): {
                "id": str(r[0]),
                "conversation_id": str(r[1]),
                "content_text": r[2] if r[2] is not None else "",
            }
            for r in rows
        }


__all__ = ["FtsConsumer", "FTS_CONSUMER_ID"]
