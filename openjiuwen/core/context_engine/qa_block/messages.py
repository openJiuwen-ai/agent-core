# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.qa_block.schema import (
    QA_BLOCK_L0_END,
    QA_BLOCK_L0_START_PREFIX,
    QA_ID_METADATA_KEY,
    SOURCE_QA_ID_METADATA_KEY,
)
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, SystemMessage, UserMessage

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.base import ModelContext
    from openjiuwen.core.context_engine.qa_block.history_buffer import HistoryQABuffer
    from openjiuwen.core.context_engine.qa_block.schema import QABlockRegistry
    from openjiuwen.core.context_engine.qa_block.store import QABlockStore

FULL_COMPACT_MARKER = "[FULL_COMPACT_BOUNDARY]"
_CONTEXT_MESSAGE_ID_KEY = "context_message_id"
_CHARS_PER_TOKEN_ESTIMATE = 4


def message_qa_id(message: BaseMessage) -> str | None:
    meta = getattr(message, "metadata", None) or {}
    qa_id = meta.get(QA_ID_METADATA_KEY) or meta.get(SOURCE_QA_ID_METADATA_KEY)
    return str(qa_id) if qa_id else None


def tag_messages_with_qa_id(messages: list[BaseMessage], qa_id: str) -> list[BaseMessage]:
    """Return copies tagged with metadata.qa_id for history hydrate."""
    tagged: list[BaseMessage] = []
    for message in messages:
        copy = message.model_copy(deep=True)
        meta = dict(copy.metadata or {})
        meta[QA_ID_METADATA_KEY] = qa_id
        copy.metadata = meta
        tagged.append(copy)
    return tagged


def _message_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "") or ""
    if isinstance(content, str):
        return content
    return str(content)


def _context_message_id(message: BaseMessage) -> str | None:
    cmid = getattr(message, "context_message_id", None)
    if cmid:
        return str(cmid)
    metadata = getattr(message, "metadata", None) or {}
    if isinstance(metadata, dict):
        value = metadata.get(_CONTEXT_MESSAGE_ID_KEY)
        if value:
            return str(value)
    return None


def had_full_compact_in_messages(messages: list[BaseMessage]) -> bool:
    for message in messages:
        if isinstance(message, SystemMessage) and FULL_COMPACT_MARKER in _message_text(message):
            return True
    return False


def extract_user_query(messages: list[BaseMessage]) -> str:
    for message in messages:
        if isinstance(message, UserMessage):
            text = _message_text(message).strip()
            if text:
                return text
    return ""


def extract_final_answer(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if not isinstance(message, AssistantMessage):
            continue
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            continue
        text = _message_text(message).strip()
        if text:
            return text
    return ""


def message_id_range(messages: list[BaseMessage]) -> tuple[str, str] | None:
    ids = [_context_message_id(message) for message in messages]
    ids = [item for item in ids if item]
    if not ids:
        return None
    return ids[0], ids[-1]


def truncate_excerpt(text: str, max_chars: int) -> str:
    normalized = (text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 1)] + "…"


def _is_legacy_preload_message(message: BaseMessage) -> bool:
    """Detect deprecated preload wrapper messages still present in old buffers."""
    meta = getattr(message, "metadata", None) or {}
    if meta.get("qa_block_preload"):
        return True
    content = (getattr(message, "content", "") or "").strip()
    if content.startswith(QA_BLOCK_L0_START_PREFIX):
        return True
    return content == QA_BLOCK_L0_END


def is_other_qa_message(message: BaseMessage, current_qa_id: str | None) -> bool:
    """True when message belongs to a hydrated history QA."""
    if _is_legacy_preload_message(message):
        return True
    qa_id = message_qa_id(message)
    if qa_id is None:
        return False
    if current_qa_id is None:
        return True
    return qa_id != current_qa_id


def extract_qa_native_messages(
    messages: list[BaseMessage],
    registry: QABlockRegistry | None = None,
) -> list[BaseMessage]:
    """Return messages belonging to the current QA, excluding hydrated history."""
    current_qa_id = getattr(registry, "current_qa_id", None) if registry is not None else None
    native = [message for message in messages if not is_other_qa_message(message, current_qa_id)]
    logger.info(
        "[QABlockNative] extract total=%s native=%s excluded=%s current_qa_id=%s",
        len(messages),
        len(native),
        len(messages) - len(native),
        current_qa_id,
    )
    return native


def strip_active_buffer(context: ModelContext, registry: QABlockRegistry | None = None) -> None:
    """Clear active buffer after freeze commit (next invoke rebuilds via assembly)."""
    before = len(context.get_messages())
    context.set_messages([], with_history=True)
    logger.info(
        "[QABlockNative] strip_active_buffer cleared=%s current_qa_id=%s",
        before,
        getattr(registry, "current_qa_id", None) if registry is not None else None,
    )


def estimate_messages_tokens(
    messages: list[BaseMessage],
    *,
    token_counter: Any | None = None,
) -> int:
    if token_counter is not None:
        try:
            return token_counter.count_messages(messages)
        except Exception as exc:
            logger.debug("[QABlockMessages] token counter failed for messages estimate: %s", exc)
    total_chars = sum(len(getattr(message, "content", "") or "") for message in messages)
    return max(0, total_chars // _CHARS_PER_TOKEN_ESTIMATE)


async def load_qa_l0(
    qa_id: str,
    history_buffer: HistoryQABuffer,
    store: QABlockStore,
) -> list[BaseMessage]:
    cached = history_buffer.get(qa_id)
    if cached:
        return cached
    return await store.read_l0(qa_id)
