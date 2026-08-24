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

from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
)
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
            compress the side opposite ``compact_direction``.  Set by
            the caller after capture.
        compact_direction: Which side of ``compact_split`` is kept
            verbatim — ``"before"`` (default, compress the tail) or
            ``"after"`` (compress the head, keep the tail verbatim).
    """

    messages: list[dict]
    compact_split: int | None = None
    compact_direction: str = "before"

    @classmethod
    def from_agent(
        cls,
        agent,
        *,
        session_id: str | None = None,
        checkpoint: int | None = None,
        keep: str = "before",
    ) -> "ForkContext":
        """Snapshot an agent's current context.

        Strips all ``SystemMessage`` entries so the source agent's role
        identity never leaks into the target.

        Args:
            agent: A ``DeepAgent`` (or ``NativeHarness``) whose
                ``get_current_context()`` yields the live messages.
            session_id: Optional session id passed to
                ``get_current_context``.
            checkpoint: Split index for truncation. ``None`` returns the
                full context.
            keep: Which side of ``checkpoint`` to keep — ``"before"``
                (messages before the checkpoint, default) or ``"after"``
                (messages from the checkpoint onward). Ignored when
                ``checkpoint`` is ``None``.
        """
        try:
            msgs = agent.get_current_context(session_id=session_id)
        except Exception as exc:  # noqa: BLE001 - fallback is attempted first
            # The source native may have been rebuilt (pause/resume or
            # restart) before its context was lazily materialized in the
            # engine pool. Fall back to the conversation persisted in the
            # source's bound child session so fork inheritance still works.
            persisted = cls._read_persisted_messages(agent)
            if persisted is None:
                raise exc
            msgs = persisted
        msgs = [m for m in msgs if not isinstance(m, SystemMessage)]

        if checkpoint is not None:
            if 0 <= checkpoint < len(msgs):
                if keep == "after":
                    # The checkpoint is recorded at tool-invoke time, so the
                    # after-window starts at the closing ToolMessage block of
                    # the assistant that carried the checkpoint call. Drop
                    # leading orphan ToolMessages (their assistant is not
                    # inherited), then keep the rest.
                    msgs = cls._trim_leading_orphan_tool_messages(msgs[checkpoint:])
                else:
                    truncated = msgs[:checkpoint]
                    last = truncated[-1] if truncated else None
                    # The checkpoint is recorded at tool-invoke time (len(messages)),
                    # which lands right after the assistant carrying the checkpoint
                    # call and before its ToolMessage result is appended. Carry the
                    # closing ToolMessage(s) across the boundary so the injected
                    # context has no dangling tool call — the product rail would
                    # otherwise mark it as "[工具执行被中断]".
                    if (
                        isinstance(last, AssistantMessage)
                        and getattr(last, "tool_calls", None)
                        and isinstance(msgs[checkpoint], ToolMessage)
                    ):
                        i = checkpoint
                        while i < len(msgs) and isinstance(msgs[i], ToolMessage):
                            truncated.append(msgs[i])
                            i += 1
                    msgs = truncated

        return cls(messages=[encode_message(m) for m in msgs])

    @staticmethod
    def _trim_leading_orphan_tool_messages(messages: list) -> list:
        """Drop leading ``ToolMessage`` entries that have no inherited assistant.

        The after-window of a checkpoint starts at the checkpoint call's result
        block; the assistant that produced those calls is not inherited, so the
        results would otherwise appear as orphans to the context rail.
        """
        start = 0
        while start < len(messages) and isinstance(messages[start], ToolMessage):
            start += 1
        return messages[start:]

    @classmethod
    def _read_persisted_messages(cls, agent) -> list | None:
        """Read a source agent's conversation from its bound child session.

        The context engine persists each round's ``ModelContext.save_state()``
        (including ``messages``) into the agent's child ``AgentSession``
        state under ``state["context"][context_id]["messages"]``. That state
        is checkpoint-restored on ``pre_run`` even when the in-memory pool is
        empty, so it is the durable source for fork capture.

        Returns ``None`` when no bound session / saved context is available;
        the caller then re-raises the original live-context error.
        """
        session = getattr(agent, "loop_session", None)
        if session is None:
            return None
        try:
            states = session.get_state("context")
        except Exception:  # noqa: BLE001 - defensive read of optional state
            return None
        if not isinstance(states, dict):
            return None
        ctx_state = states.get("default_context_id")
        if not isinstance(ctx_state, dict):
            return None
        messages = ctx_state.get("messages")
        if not isinstance(messages, list):
            return None
        return cls._normalize_messages(messages)

    @classmethod
    def _normalize_messages(cls, messages: list) -> list | None:
        """Return messages as ``BaseMessage`` objects, or ``None`` on malformed input.

        The checkpoint round-trip currently preserves ``BaseMessage`` objects
        (both checkpointer backends use pickle), so dicts are not expected
        today. Normalizing keeps the fallback robust if a future serializer
        stores them as json dicts instead of silently degrading fork
        inheritance.
        """
        from openjiuwen.core.foundation.llm.schema.message import BaseMessage

        normalized: list = []
        for message in messages:
            if isinstance(message, BaseMessage):
                normalized.append(message)
            elif isinstance(message, dict):
                try:
                    normalized.append(decode_message(message))
                except Exception:  # noqa: BLE001 - malformed persisted message
                    return None
            else:
                return None
        return normalized

    def to_messages(self) -> list["BaseMessage"]:
        """Decode back to ``BaseMessage`` list for context injection."""
        return [decode_message(d) for d in self.messages]

    def is_empty(self) -> bool:
        """Return ``True`` when no messages were captured."""
        return len(self.messages) == 0
