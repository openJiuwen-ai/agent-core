"""normalize — convert learning DTOs into a ``NormalizedBatch``.

Pure function, no DB access.  Channel-agnostic.

Unlike the JiuwenSpirit original (which received full hosting ``ImMessage``
objects), the openjiuwen wire contract (OJ-01) is a read-only learning
projection without ``direction`` / ``learning_eligible`` fields.  This module
derives them (decision D6):

- ``conversations`` are derived from the ``ImLearningTarget``;
- ``direction`` is derived from ``is_self`` (True -> outbound);
- ``learning_eligible`` is computed by ``learning_scope`` and passed in
  alongside the messages.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from openjiuwen.harness.personal_context.im.models import (
    ImLearningCursor,
    ImLearningMessage,
    ImLearningTarget,
    ImMessageBatch,
)


@dataclass
class NormalizedConversation:
    """Channel-agnostic conversation record to upsert."""

    channel_id: str
    external_id: str
    target_kind: str  # 'group' / 'user'
    title: Optional[str] = None


@dataclass
class NormalizedMessage:
    """Channel-agnostic message record to upsert."""

    channel_id: str
    msg_id: str  # external id (platform-stable)
    conversation_external_id: str
    sender_account: Optional[str]
    sender_name: Optional[str]
    content_text: str
    sent_at: int
    content_type: Optional[str]
    direction: str = "inbound"
    is_self: Optional[bool] = None
    learning_eligible: Optional[int] = None


@dataclass
class NormalizedBatch:
    """Output of normalize_batch: ready to persist in a single transaction."""

    channel_id: str
    fetched_at_ms: int
    conversations: list[NormalizedConversation] = field(default_factory=list)
    messages: list[NormalizedMessage] = field(default_factory=list)


def content_digest(content: str) -> str:
    """sha256 of content text; used for changelog digests and FTS skip-writes."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def derive_direction(is_self: Optional[bool]) -> str:
    """Derive direction from the tri-state ``is_self`` flag (decision D6)."""
    return "outbound" if is_self is True else "inbound"


def _conversation_from_target(target: ImLearningTarget) -> NormalizedConversation:
    return NormalizedConversation(
        channel_id=target.channel_id,
        external_id=target.external_id,
        target_kind=target.kind,
        title=target.title,
    )


def normalize_batch(
    *,
    target: ImLearningTarget,
    messages: list[ImLearningMessage] | tuple[ImLearningMessage, ...],
    fetched_at_ms: int,
    learning_eligible_map: Optional[dict[str, int]] = None,
) -> NormalizedBatch:
    """Pure conversion: learning messages -> NormalizedBatch.

    Dedups messages by (channel_id, msg_id) preserving first occurrence.
    The conversation is derived from the target.  ``learning_eligible_map``
    maps msg_id -> 1/0 as computed by ``learning_scope``.
    """
    eligible_map = learning_eligible_map or {}
    seen_msg: set[tuple[str, str]] = set()
    out_msgs: list[NormalizedMessage] = []
    for msg in messages:
        key = (msg.channel_id, msg.msg_id)
        if not msg.msg_id or key in seen_msg:
            # Skip messages without stable external ids; the dedup key would
            # collapse on empty string.  Safety net only.
            continue
        seen_msg.add(key)
        eligible = eligible_map.get(msg.msg_id)
        out_msgs.append(
            NormalizedMessage(
                channel_id=msg.channel_id,
                msg_id=msg.msg_id,
                conversation_external_id=msg.conversation_external_id,
                sender_account=msg.sender_account,
                sender_name=msg.sender_name,
                content_text=msg.content_text or "",
                sent_at=int(msg.sent_at or 0),
                content_type=msg.content_type,
                direction=derive_direction(msg.is_self),
                is_self=msg.is_self,
                learning_eligible=eligible,
            )
        )
    return NormalizedBatch(
        channel_id=target.channel_id,
        fetched_at_ms=fetched_at_ms,
        conversations=[_conversation_from_target(target)],
        messages=out_msgs,
    )


def newest_message(messages: list[ImLearningMessage] | tuple[ImLearningMessage, ...]) -> ImLearningMessage | None:
    """Return the message with the largest (sent_at, msg_id) ordering."""
    if not messages:
        return None
    return max(messages, key=lambda m: (int(m.sent_at or 0), m.msg_id))


def oldest_message(messages: list[ImLearningMessage] | tuple[ImLearningMessage, ...]) -> ImLearningMessage | None:
    """Return the message with the smallest (sent_at, msg_id) ordering."""
    if not messages:
        return None
    return min(messages, key=lambda m: (int(m.sent_at or 0), m.msg_id))


def batch_messages(batch: ImMessageBatch) -> tuple[ImLearningMessage, ...]:
    """Flatten a fetch page into its message tuple."""
    return batch.messages


def cursor_count(cursor: ImLearningCursor | None, *, default: int = 50) -> int:
    """Page size carried by the cursor, or the default when absent."""
    if cursor is None:
        return default
    try:
        count = int(cursor.count)
    except (TypeError, ValueError):
        return default
    return count if count > 0 else default


__all__ = [
    "NormalizedBatch",
    "NormalizedConversation",
    "NormalizedMessage",
    "batch_messages",
    "content_digest",
    "cursor_count",
    "derive_direction",
    "newest_message",
    "normalize_batch",
    "oldest_message",
]
