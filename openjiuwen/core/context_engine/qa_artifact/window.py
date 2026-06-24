# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.context.session_memory_manager import (
    find_message_index_by_context_message_id,
)
from openjiuwen.core.context_engine.qa_artifact.catalog import CatalogBuilder
from openjiuwen.core.context_engine.qa_artifact.schema import QAArtifacts
from openjiuwen.core.context_engine.qa_block.history_buffer import HistoryQABuffer
from openjiuwen.core.context_engine.qa_block.layer import QABlockLayer
from openjiuwen.core.context_engine.qa_block.messages import message_qa_id
from openjiuwen.core.context_engine.qa_block.registry import load_registry
from openjiuwen.core.context_engine.qa_block.schema import QA_ID_METADATA_KEY, SOURCE_QA_ID_METADATA_KEY
from openjiuwen.core.context_engine.qa_block.store import QABlockStore
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, ToolMessage

_CHARS_PER_TOKEN_ESTIMATE = 4
_COMPACT_BOUNDARY = "[QA_ARTIFACT_BOUNDARY]"


def render_artifact_text(qa_id: str, artifacts: QAArtifacts) -> str:
    catalog = CatalogBuilder.render(qa_id, artifacts.entries)
    overview = (artifacts.overview or "").strip()
    if overview and catalog:
        return f"{overview}\n\n{catalog}".strip()
    return overview or catalog


def compact_replacement_message(qa_id: str, text: str) -> AssistantMessage:
    return AssistantMessage(
        content=f"{_COMPACT_BOUNDARY}\n{text}".strip(),
        metadata={QA_ID_METADATA_KEY: qa_id, "qa_artifact_compacted": True},
    )


def tail_after(messages: list[BaseMessage], covers_upto_message_id: str | None) -> list[BaseMessage]:
    """Return messages strictly after covers_upto_message_id (§3.4 保尾)."""
    if not messages or not covers_upto_message_id:
        return []
    cutoff = find_message_index_by_context_message_id(messages, covers_upto_message_id)
    if cutoff < 0 or cutoff + 1 >= len(messages):
        return []
    return list(messages[cutoff + 1:])


def split_qa_message_indices(
    messages: list[BaseMessage],
    qa_id: str,
    *,
    covers_upto_message_id: str | None,
    via_tool: bool,
    message_qa_id_fn,
    source_qa_key: str,
) -> tuple[list[int], list[int]]:
    """Split in-window QA indices into replace (≤ covers_upto) vs keep (tail)."""
    all_indices: list[int] = []
    for index, message in enumerate(messages):
        meta = getattr(message, "metadata", None) or {}
        if via_tool:
            if meta.get(source_qa_key) == qa_id:
                all_indices.append(index)
        elif message_qa_id_fn(message) == qa_id:
            all_indices.append(index)

    if not all_indices:
        return [], []

    if not covers_upto_message_id:
        return all_indices, []

    cutoff = find_message_index_by_context_message_id(messages, covers_upto_message_id)
    if cutoff < 0:
        return all_indices, []

    replace_indices = [index for index in all_indices if index <= cutoff]
    keep_indices = [index for index in all_indices if index > cutoff]
    return replace_indices, keep_indices


def compute_fold_slice(
    all_messages: list[BaseMessage],
    active_messages: list[BaseMessage],
    current_qa_id: str,
    covers_upto_message_id: str | None,
    kept_messages: list[BaseMessage],
) -> list[BaseMessage]:
    """Messages of current QA in the whole-window fold range, excluding preserve_tail."""
    all_current = [message for message in all_messages if message_qa_id(message) == current_qa_id]
    if not all_current:
        return []

    kept_ids = {id(message) for message in kept_messages}
    active_ids = {id(message) for message in active_messages}

    fold_upper_idx = -1
    for index in range(len(all_current) - 1, -1, -1):
        message = all_current[index]
        if id(message) in active_ids and id(message) not in kept_ids:
            fold_upper_idx = index
            break
    if fold_upper_idx < 0:
        return []

    if covers_upto_message_id:
        cutoff = find_message_index_by_context_message_id(all_current, covers_upto_message_id)
        fold_lower_idx = cutoff + 1 if cutoff >= 0 else 0
    else:
        fold_lower_idx = 0

    if fold_lower_idx > fold_upper_idx:
        return []

    fold_slice: list[BaseMessage] = []
    for index in range(fold_lower_idx, fold_upper_idx + 1):
        message = all_current[index]
        if id(message) in active_ids and id(message) not in kept_ids:
            fold_slice.append(message)
    return fold_slice


def _estimate_text_tokens(text: str, token_counter: Any | None) -> int:
    if not text:
        return 0
    if token_counter is not None:
        try:
            return token_counter.count_messages([AssistantMessage(content=text)])
        except Exception as exc:
            logger.debug("[QAArtifactWindow] token counter failed for text estimate: %s", exc)
    return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


def _estimate_messages_tokens(messages: list[BaseMessage], token_counter: Any | None) -> int:
    if token_counter is not None:
        try:
            return token_counter.count_messages(messages)
        except Exception as exc:
            logger.debug("[QAArtifactWindow] token counter failed for messages estimate: %s", exc)
    return sum(ContextUtils.estimate_message_tokens(message) for message in messages)


def estimate_context_messages_tokens(messages: list[BaseMessage], token_counter: Any | None) -> int:
    return _estimate_messages_tokens(messages, token_counter)


def _already_compact_in_window(
    messages: list[BaseMessage],
    qa_id: str,
    *,
    covers_upto_message_id: str | None,
) -> bool:
    if _tool_expanded_for_qa(messages, qa_id):
        return False

    for message in messages:
        meta = getattr(message, "metadata", None) or {}
        if meta.get(QA_ID_METADATA_KEY) != qa_id:
            continue
        if meta.get("qa_artifact_compacted"):
            return True
        content = getattr(message, "content", "") or ""
        if content.startswith(_COMPACT_BOUNDARY):
            return True

    replace_indices, _keep_indices = split_qa_message_indices(
        messages,
        qa_id,
        covers_upto_message_id=covers_upto_message_id,
        via_tool=False,
        message_qa_id_fn=message_qa_id,
        source_qa_key=SOURCE_QA_ID_METADATA_KEY,
    )
    if not replace_indices:
        return True
    if len(replace_indices) != 1:
        return False
    message = messages[replace_indices[0]]
    meta = getattr(message, "metadata", None) or {}
    return bool(meta.get("qa_artifact_compacted")) and meta.get(QA_ID_METADATA_KEY) == qa_id


def is_qa_already_compact_in_window(
    messages: list[BaseMessage],
    qa_id: str,
    *,
    covers_upto_message_id: str | None,
) -> bool:
    """Return True when in-window QA messages are already replaced by compact artifact."""
    return _already_compact_in_window(
        messages,
        qa_id,
        covers_upto_message_id=covers_upto_message_id,
    )


def _tool_expanded_for_qa(messages: list[BaseMessage], qa_id: str) -> bool:
    for message in messages:
        meta = getattr(message, "metadata", None) or {}
        if meta.get(SOURCE_QA_ID_METADATA_KEY) == qa_id:
            return True
    return False


def apply_artifact_to_context(
    context: Any,
    qa_id: str,
    artifacts: QAArtifacts,
    *,
    token_counter: Any | None = None,
    covers_upto_message_id: str | None = None,
    force_reapply: bool = False,
) -> int:
    """Replace in-window QA messages with overview+catalog; return tokens reduced."""
    text = render_artifact_text(qa_id, artifacts)
    if not text.strip():
        logger.info("[QAArtifactWindow] skip empty artifact qa_id=%s", qa_id)
        return 0

    messages = context.get_messages()
    via_tool = _tool_expanded_for_qa(messages, qa_id)
    if (
        not via_tool
        and not force_reapply
        and _already_compact_in_window(
            messages,
            qa_id,
            covers_upto_message_id=covers_upto_message_id,
        )
    ):
        logger.info("[QAArtifactWindow] skip already compact qa_id=%s", qa_id)
        return 0

    replace_indices, _keep_indices = split_qa_message_indices(
        messages,
        qa_id,
        covers_upto_message_id=covers_upto_message_id,
        via_tool=via_tool,
        message_qa_id_fn=message_qa_id,
        source_qa_key=SOURCE_QA_ID_METADATA_KEY,
    )
    if not replace_indices:
        return 0

    before_tokens = sum(_estimate_messages_tokens([messages[index]], token_counter) for index in replace_indices)

    replacement = compact_replacement_message(qa_id, text)
    drop = set(replace_indices)
    if via_tool:
        first = replace_indices[0]
        new_messages = list(messages)
        new_messages[first] = ToolMessage(
            content=text,
            tool_call_id=getattr(messages[first], "tool_call_id", None) or "qa_artifact",
            metadata={SOURCE_QA_ID_METADATA_KEY: qa_id, "qa_artifact_compacted": True},
        )
        drop.discard(first)
        new_messages = [msg for idx, msg in enumerate(new_messages) if idx not in drop]
    else:
        first = replace_indices[0]
        new_messages = [msg for idx, msg in enumerate(messages) if idx not in drop]
        new_messages.insert(first, replacement)

    context.set_messages(new_messages, with_history=True)
    after_tokens = _estimate_text_tokens(text, token_counter)
    reduced = max(0, before_tokens - after_tokens)
    logger.info(
        "[QAArtifactWindow] applied qa_id=%s via_tool=%s reduced=%s",
        qa_id,
        via_tool,
        reduced,
    )
    return reduced


def _resolve_history_buffer(context: Any, history_buffer: HistoryQABuffer | None) -> HistoryQABuffer:
    if history_buffer is not None:
        return history_buffer
    engine = getattr(context, "_context_engine", None)
    if engine is not None and hasattr(engine, "get_history_qa_buffer"):
        session_id = context.session_id() if hasattr(context, "session_id") else ""
        context_id = context.context_id() if hasattr(context, "context_id") else "default_context_id"
        return engine.get_history_qa_buffer(session_id, context_id)
    return HistoryQABuffer()


def build_window_qas_from_context(
    context: Any,
    *,
    sys_operation: Any | None = None,
    history_buffer: HistoryQABuffer | None = None,
) -> list[Any]:
    session = context.get_session_ref() if hasattr(context, "get_session_ref") else None
    if session is None:
        return []
    registry = load_registry(session)
    if not registry.blocks and registry.current_qa_id is None:
        return []

    workspace_root = ""
    if hasattr(context, "workspace_dir"):
        workspace_root = context.workspace_dir() or ""
    store = QABlockStore(workspace_root, context.session_id(), sys_operation)
    token_counter = None
    counter_getter = getattr(context, "token_counter", None)
    if callable(counter_getter):
        token_counter = counter_getter()

    buffer = _resolve_history_buffer(context, history_buffer)
    layer = QABlockLayer(registry, buffer, store, token_counter=token_counter)
    return layer.build_window_qas(context)


def make_processor_ctx(context: Any, *, sys_operation: Any | None = None) -> Any:
    workspace_root = context.workspace_dir() if hasattr(context, "workspace_dir") else ""
    return type(
        "ProcessorQAContext",
        (),
        {
            "context": context,
            "session": context.get_session_ref() if hasattr(context, "get_session_ref") else None,
            "sys_operation": sys_operation or getattr(context, "_sys_operation", None),
            "workspace": type("Workspace", (), {"root_path": workspace_root or ""})(),
            "inputs": None,
        },
    )()
