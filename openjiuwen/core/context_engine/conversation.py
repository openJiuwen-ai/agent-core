# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Validation of detached conversation data before an executor resumes from it."""
from copy import deepcopy
from typing import Any


def conversation_snapshot(state: dict[str, Any], *, allow_pending_tool_calls: bool = False) -> dict[str, Any]:
    """Copy a complete conversation, rejecting unresolved tool/offload data.

    Filesystem offloads need a host storage mapping and are deliberately rejected
    here: an otherwise valid message with an inaccessible path is not portable.
    No source runtime, tools, prompts or task state is copied.
    """
    if not isinstance(state, dict) or "messages" not in state:
        raise ValueError("conversation_snapshot_missing")
    snapshot = deepcopy({
        "messages": state["messages"],
        "offload_messages": state.get("offload_messages") or {},
    })
    from openjiuwen.core.context_engine.context.context_utils import ContextUtils

    batches = [snapshot["messages"], *snapshot["offload_messages"].values()]
    for batch in batches:
        ContextUtils.validate_messages(batch)
        for message in batch:
            handle = getattr(message, "offload_handle", None)
            if handle:
                if getattr(message, "offload_type", None) != "in_memory":
                    raise ValueError("conversation_filesystem_offload_not_portable")
                if handle not in snapshot["offload_messages"]:
                    raise ValueError("conversation_offload_missing")
    # A buffer entry may be a single offloaded tool result. Pairing applies to
    # the visible conversation, where its placeholder keeps the tool_call_id.
    pending: set[str] = set()
    for message in snapshot["messages"]:
        calls = getattr(message, "tool_calls", None) or []
        for call in calls:
            call_id = call.get("id") if isinstance(call, dict) else call.id
            if not call_id or call_id in pending:
                raise ValueError("conversation_tool_call_invalid")
            pending.add(call_id)
        result_id = getattr(message, "tool_call_id", None)
        if result_id:
            if result_id not in pending:
                raise ValueError("conversation_tool_result_unpaired")
            pending.remove(result_id)
        elif not calls and pending:
            raise ValueError("conversation_tool_call_unclosed")
    if pending and not allow_pending_tool_calls:
        raise ValueError("conversation_tool_call_unclosed")
    return snapshot

