# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fork-aware context compaction — summarise older messages before injection.

When a forked context is large, ``compact_context`` compresses the
portion *before* a split point into a model-generated summary while
leaving the portion *after* the split point verbatim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.foundation.llm.schema.message import SystemMessage, UserMessage
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    pass

_COMPACTION_PROMPT = (
    "Your task is to create a detailed summary of the conversation history below. "
    "Pay close attention to all user requests, file reads, search results, "
    "analysis conclusions, and technical decisions that were made.\n\n"
    "The summary must retain:\n"
    "- Every file path that was read or written\n"
    "- Key code patterns and architectural decisions\n"
    "- Errors encountered and how they were resolved\n"
    "- User feedback and changes of direction\n\n"
    "Summarise concisely but do not omit any fact that would be necessary "
    "to continue development work without losing context.\n\n"
    "=== BEGIN CONVERSATION HISTORY ===\n"
)


async def compact_context(deep_agent, *, split_at: int, session_id: str | None = None) -> None:
    """Compress messages before ``split_at`` into a summary.

    Messages at and after ``split_at`` are kept verbatim.
    All ``SystemMessage`` entries are stripped before compaction.

    Args:
        deep_agent: The target ``DeepAgent`` whose context engine
            already holds the forked messages.
        split_at: Index in the (SystemMessage-stripped) message list
            that separates old / recent.  ``0 <= split_at < len(msgs)``.
        session_id: Optional session id for the new context engine.
    """
    msgs = deep_agent.get_current_context(session_id=session_id)
    msgs = [m for m in msgs if not isinstance(m, SystemMessage)]

    if split_at <= 0 or split_at >= len(msgs):
        team_logger.info(
            "[fork] compact_context: skipping (split_at=%d len=%d)",
            split_at, len(msgs),
        )
        return

    old_msgs = msgs[:split_at]
    recent_msgs = msgs[split_at:]

    team_logger.info(
        "[fork] compact_context: compressing %d messages, keeping %d",
        len(old_msgs), len(recent_msgs),
    )

    summary = await _run_compaction(deep_agent, old_msgs)
    compacted = [
        UserMessage(content=(
            f"[Compacted context — earlier conversation summarised]\n\n{summary}"
        )),
        *recent_msgs,
    ]
    # Directly replace messages in the existing context engine.
    # ``create_new_context_engine`` re-creates a fresh ``Session``
    # whose ``get_state()`` returns ``None``, so ``_load_state_from_session``
    # silently exits without applying the new messages.
    react = deep_agent.react_agent
    if react is not None:
        context = react.context_engine.get_context(
            context_id="default_context_id",
            session_id=session_id or "default_session_id",
        )
        if context is not None:
            context.set_messages(compacted)

    team_logger.info(
        "[fork] compact_context: done — %d messages after compaction",
        len(compacted),
    )


async def _run_compaction(deep_agent, old_msgs) -> str:
    """Run a single-turn summarisation using the agent's own model.

    The model still has the forked context in its KV cache, so the
    summarisation call only costs the output tokens.
    """
    try:
        config = deep_agent.deep_config
        if config is None or config.model is None:
            return "[compaction skipped: no model available]"

        texts = _render_message_texts(old_msgs)
        prompt = _COMPACTION_PROMPT + texts

        response = await config.model.invoke(
            messages=[UserMessage(content=prompt)],
            max_tokens=4096,
        )
        return response.content if response else "[compaction returned empty response]"
    except Exception as exc:
        team_logger.warning("[fork] compaction failed: %s", exc)
        return f"[compaction failed: {exc}]"


def _render_message_texts(messages) -> str:
    """Render a list of messages as plain text for the compaction prompt."""
    parts: list[str] = []
    for msg in messages:
        role = getattr(msg, "role", "unknown")
        content = getattr(msg, "content", "")
        if content:
            parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)
