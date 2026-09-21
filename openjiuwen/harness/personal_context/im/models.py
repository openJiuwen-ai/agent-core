"""Read-only IM learning DTOs.

These types are the PersonalContext view of normalized chat messages. They do
not include send/identity operations and must not reference CLI implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ImLearningTargetKind = Literal["group", "user"]


@dataclass(frozen=True)
class ImLearningTarget:
    """One conversation the learning fetch may read."""

    channel_id: str
    kind: ImLearningTargetKind
    external_id: str
    title: str | None = None


@dataclass(frozen=True)
class ImLearningCursor:
    """Opaque-enough fetch cursor for one target.

    ``message_id`` / ``query_direction`` match the WeLink CLI pagination flags
    when the host connector supports them. Other platforms may ignore them and
    use ``extra``.
    """

    message_id: str | None = None
    query_direction: int | None = None
    count: int = 50
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImLearningMessage:
    """Normalized message stored/indexed by PersonalContext IM learning."""

    channel_id: str
    msg_id: str
    conversation_external_id: str
    content_text: str
    sent_at: int
    sender_account: str | None = None
    sender_name: str | None = None
    content_type: str | None = None
    is_self: bool | None = None


@dataclass(frozen=True)
class ImMessageBatch:
    """One fetch page plus the cursor to continue with."""

    messages: tuple[ImLearningMessage, ...]
    next_cursor: ImLearningCursor | None = None
