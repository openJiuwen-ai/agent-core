"""persist_batch — write a NormalizedBatch to the im_* tables (single transaction).

This is the only function that writes to ``im_messages`` /
``im_conversations`` / ``im_changelog``.  All writes happen in a single
transaction so either all or none are visible.  Raw platform payloads are
never stored.

Idempotency:
- im_conversations: upsert on (channel_id, external_id) -> update last_message_at_ms
- im_messages: ON CONFLICT(channel_id, external_id) DO UPDATE SET, guarded by
  a WHERE clause so an unchanged row is not rewritten at all
- im_changelog: appended only for messages actually inserted/updated — a
  steady-state cycle that re-sees already-stored messages appends nothing
  (consumers ack by seq; the digest check at FTS upsert additionally skips
  no-op FTS writes)
- learning_eligible is tri-state: 1=distillable, 0=out of learning scope,
  NULL=undecided (excluded from distill and search). A NULL never overwrites
  an existing verdict; 0 is sticky ("only downgrade").
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from openjiuwen.harness.personal_context.im.changelog import ChangelogRepository
from openjiuwen.harness.personal_context.im.normalize import NormalizedBatch, content_digest
from openjiuwen.harness.personal_context.im.schema import init_im_schema


@dataclass
class PersistResult:
    """Summary of one persist_batch call."""

    conversations_upserted: int = 0
    messages_inserted: int = 0
    messages_updated: int = 0
    messages_unchanged: int = 0
    changelog_entries_appended: int = 0
    changelog_head: int = 0

    @property
    def messages_written(self) -> int:
        """Messages actually inserted or updated (unchanged rows excluded)."""
        return self.messages_inserted + self.messages_updated


def _gen_id() -> str:
    # uuid4 hex is collision-free; the timestamp prefix is kept only for
    # debuggability (ids sort roughly by creation time).
    return f"{int(time.time() * 1000):x}-{uuid4().hex[:12]}"


def persist_batch(
    conn: sqlite3.Connection,
    batch: NormalizedBatch,
    *,
    now_ms: Optional[int] = None,
    ensure_schema: bool = True,
) -> PersistResult:
    """Write a NormalizedBatch to the im_* tables.

    All writes happen in a single transaction (BEGIN IMMEDIATE). Returns
    counts of affected rows for observability.

    NOTE: FTS indexing is NOT done here; it happens asynchronously in
    ``FtsConsumer.drain_once()`` after this commit. The transaction is
    kept short so the WAL lock is released before FTS work.
    """
    if ensure_schema:
        init_im_schema(conn)
    ts = int(now_ms if now_ms is not None else time.time() * 1000)
    result = PersistResult()

    # Manual BEGIN IMMEDIATE so all writes are atomic (the connection may
    # run with isolation_level=None autocommit mode).
    conn.execute("BEGIN IMMEDIATE")
    try:
        conversation_id_by_external: dict[str, str] = {}
        for conv in batch.conversations:
            conv_id = _gen_id()
            cur = conn.execute(
                """
                INSERT INTO im_conversations (
                    id, channel_id, external_id, target_kind, title,
                    created_at_ms, updated_at_ms, last_message_at_ms,
                    archived_at_ms, visible_in_recent, unread_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, 0)
                ON CONFLICT(channel_id, external_id) DO UPDATE SET
                    target_kind = excluded.target_kind,
                    title = COALESCE(excluded.title, im_conversations.title),
                    updated_at_ms = excluded.updated_at_ms,
                    visible_in_recent = 1
                RETURNING id
                """,
                (conv_id, conv.channel_id, conv.external_id, conv.target_kind, conv.title, ts, ts),
            )
            row = cur.fetchone()
            if row is not None:
                conversation_id_by_external[conv.external_id] = str(row[0])
            result.conversations_upserted += 1

        changelog_repo = ChangelogRepository(conn)
        conv_last_sent: dict[str, int] = {}
        for msg in batch.messages:
            conv_id = conversation_id_by_external.get(msg.conversation_external_id)
            if conv_id is None:
                # Implicit conversation: derive from (channel, external).
                # NOTE: real callers should always pass conversations first;
                # target_kind cannot be derived here, so a fresh row defaults
                # to 'group' while an existing row keeps its stored kind.
                conv_id = _gen_id()
                conv_row = conn.execute(
                    """
                    INSERT INTO im_conversations (
                        id, channel_id, external_id, target_kind, title,
                        created_at_ms, updated_at_ms, last_message_at_ms,
                        archived_at_ms, visible_in_recent, unread_count
                    )
                    VALUES (?, ?, ?, ?, NULL, ?, ?, ?, NULL, 0, 0)
                    ON CONFLICT(channel_id, external_id) DO UPDATE SET
                        updated_at_ms = excluded.updated_at_ms,
                        visible_in_recent = 1
                    RETURNING id
                    """,
                    (conv_id, msg.channel_id, msg.conversation_external_id, "group", ts, ts),
                ).fetchone()
                conv_id = str(conv_row[0]) if conv_row is not None else conv_id
                conversation_id_by_external[msg.conversation_external_id] = conv_id
            msg_row_id = _gen_id()
            eligible = None if msg.learning_eligible is None else int(msg.learning_eligible)
            if eligible not in (0, 1):
                eligible = None
            cur = conn.execute(
                """
                INSERT INTO im_messages (
                    id, channel_id, conversation_id, external_id, sender_account, sender_name,
                    content_text, content_type, sent_at, direction, is_self,
                    learning_eligible, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, external_id) DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    sender_account = COALESCE(excluded.sender_account, im_messages.sender_account),
                    sender_name = COALESCE(excluded.sender_name, im_messages.sender_name),
                    content_text = COALESCE(
                        NULLIF(excluded.content_text, ''),
                        im_messages.content_text
                    ),
                    content_type = COALESCE(excluded.content_type, im_messages.content_type),
                    sent_at = CASE
                        WHEN excluded.sent_at > im_messages.sent_at THEN excluded.sent_at
                        ELSE im_messages.sent_at
                    END,
                    direction = excluded.direction,
                    is_self = COALESCE(excluded.is_self, im_messages.is_self),
                    learning_eligible = CASE
                        WHEN excluded.learning_eligible IS NULL THEN im_messages.learning_eligible
                        WHEN im_messages.learning_eligible = 0 THEN 0
                        ELSE excluded.learning_eligible
                    END,
                    created_at = im_messages.created_at
                WHERE
                    im_messages.conversation_id IS NOT excluded.conversation_id
                    OR im_messages.sender_account IS NOT COALESCE(
                        excluded.sender_account, im_messages.sender_account
                    )
                    OR im_messages.sender_name IS NOT COALESCE(
                        excluded.sender_name, im_messages.sender_name
                    )
                    OR im_messages.content_text IS NOT COALESCE(
                        NULLIF(excluded.content_text, ''), im_messages.content_text
                    )
                    OR im_messages.content_type IS NOT COALESCE(
                        excluded.content_type, im_messages.content_type
                    )
                    OR excluded.sent_at > im_messages.sent_at
                    OR im_messages.direction IS NOT excluded.direction
                    OR im_messages.is_self IS NOT COALESCE(
                        excluded.is_self, im_messages.is_self
                    )
                    OR im_messages.learning_eligible IS NOT CASE
                        WHEN excluded.learning_eligible IS NULL THEN im_messages.learning_eligible
                        WHEN im_messages.learning_eligible = 0 THEN 0
                        ELSE excluded.learning_eligible
                    END
                RETURNING id
                """,
                (
                    msg_row_id,
                    msg.channel_id,
                    conv_id,
                    msg.msg_id,
                    msg.sender_account,
                    msg.sender_name,
                    msg.content_text,
                    msg.content_type,
                    msg.sent_at,
                    msg.direction,
                    (None if msg.is_self is None else (1 if msg.is_self else 0)),
                    eligible,
                    ts,
                ),
            )
            row = cur.fetchone()
            if row is None:
                # Conflict on an unchanged row: the DO UPDATE ... WHERE clause
                # skipped the rewrite, so there is nothing to publish.
                result.messages_unchanged += 1
                continue
            real_msg_id = str(row[0])
            changelog_repo.append(
                op="upsert",
                entity_type="message",
                entity_id=real_msg_id,
                channel_id=msg.channel_id,
                domain="chat",
                occurred_at=msg.sent_at,
                payload_ref=None,
                digest=content_digest(msg.content_text),
                emitted_at=ts,
            )
            if row[0] == msg_row_id:
                result.messages_inserted += 1
            else:
                result.messages_updated += 1
            result.changelog_entries_appended += 1
            conv_last_sent[conv_id] = max(conv_last_sent.get(conv_id, 0), msg.sent_at)
        for conv_id, last_ms in conv_last_sent.items():
            conn.execute(
                "UPDATE im_conversations SET last_message_at_ms = ? "
                "WHERE id = ? AND (last_message_at_ms IS NULL OR last_message_at_ms < ?)",
                (int(last_ms), conv_id, int(last_ms)),
            )
        result.changelog_head = changelog_repo.head()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result


__all__ = ["persist_batch", "PersistResult"]
