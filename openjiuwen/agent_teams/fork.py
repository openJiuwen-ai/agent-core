# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ForkContext — serializable snapshot of an agent's conversation context.

Captures the full conversation history of a source agent so it can be
injected into a newly spawned member's context engine. All ``SystemMessage``
entries are stripped during capture — the target agent's role is injected
by ``TeamPolicyRail``, never leaked from the fork source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openjiuwen.core.foundation.llm.schema.message import SystemMessage
from openjiuwen.core.session.vcs.codec import decode_message, encode_message

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm.schema.message import BaseMessage


@dataclass
class ForkContext:
    """Serializable fork context for team spawning.

    Attributes:
        messages: Encoded message dicts (json-native), ready for
            cross-process transport.
        compact_split: When not ``None``, ``compact_context`` will
            compress messages *before* this index; messages at and
            after are kept verbatim.  Set by the caller after capture.
    """

    messages: list[dict]
    compact_split: int | None = None

    @classmethod
    def from_agent(
        cls,
        agent,
        *,
        session_id: str | None = None,
        checkpoint: int | None = None,
    ) -> "ForkContext":
        """Snapshot an agent's current context.

        Strips all ``SystemMessage`` entries so the source agent's role
        identity never leaks into the target.

        Args:
            agent: A ``DeepAgent`` (or ``NativeHarness``) whose
                ``get_current_context()`` yields the live messages.
            session_id: Optional session id passed to
                ``get_current_context``.
            checkpoint: Truncate messages *before* this index.
                ``None`` returns the full context.
        """
        msgs = agent.get_current_context(session_id=session_id)
        msgs = [m for m in msgs if not isinstance(m, SystemMessage)]

        if checkpoint is not None:
            if 0 <= checkpoint < len(msgs):
                msgs = msgs[:checkpoint]

        return cls(messages=[encode_message(m) for m in msgs])

    def to_messages(self) -> list["BaseMessage"]:
        """Decode back to ``BaseMessage`` list for context injection."""
        return [decode_message(d) for d in self.messages]

    def is_empty(self) -> bool:
        """Return ``True`` when no messages were captured."""
        return len(self.messages) == 0
