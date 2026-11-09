# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Conversation-only checkpoint exchange. Runtime state stays with its owner."""
from copy import deepcopy
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.context_engine import ContextEngine
    from openjiuwen.core.session.agent import Session


def conversation_snapshot(state: dict[str, Any]) -> dict[str, Any]:
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
    if pending:
        raise ValueError("conversation_tool_call_unclosed")
    return snapshot


def export_conversation(
    session: "Session", engine: "ContextEngine | None" = None, *, context_id: str = "default_context_id",
) -> dict[str, Any]:
    """Export live messages or an already restored Session without running it."""
    context = engine.get_context(context_id, session.get_session_id()) if engine else None
    state = context.save_state() if context is not None else (session.get_state("context") or {}).get(context_id)
    return conversation_snapshot(state)


async def replace_conversation(
    session: "Session", engine: "ContextEngine", snapshot: dict[str, Any], marker: dict[str, Any], *,
    context_id: str = "default_context_id", reset_state_keys: tuple[str, ...] = (),
) -> None:
    """Replace messages and commit a receipt, restoring memory on commit failure.

    The host must exclude execution until this operation finishes. A failure
    after an uncertain storage commit is resolved using the durable receipt.
    """
    snapshot = conversation_snapshot(snapshot)
    context = engine.get_context(context_id, session.get_session_id())
    if context is None:
        context = await engine.create_context(context_id=context_id, session=session)
    old_context = deepcopy(context.save_state())
    keys = ("context", "context_handoff", *reset_state_keys)
    old_state = {key: deepcopy(session.get_state(key)) for key in keys}
    try:
        context.load_state({context.context_id(): snapshot})
        await engine.save_contexts(session)
        # Keep contexts that are not currently materialized in this engine.
        contexts = dict(old_state["context"] or {})
        contexts.update(session.get_state("context") or {})
        session.update_state({"context": None})
        session.update_state({"context": contexts, "context_handoff": marker})
        for key in reset_state_keys:
            session.update_state({key: None})
        await session.commit()
    except BaseException:
        context.load_state({context.context_id(): old_context})
        for key, value in old_state.items():
            session.update_state({key: None})
            session.update_state({key: value})
        raise
