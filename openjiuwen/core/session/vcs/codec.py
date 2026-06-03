# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Message <-> json-dict codec for vcs.

``BaseMessage`` has no discriminated union, so calling ``model_validate`` on
the base class would silently drop subclass fields (``tool_calls`` /
``tool_call_id``). We therefore dispatch by ``role`` to the concrete subclass.
``AssistantMessage.model_dump`` emits OpenAI-shaped ``tool_calls`` and its
``model_validator(mode="before")`` accepts that same shape back, so the
encode/decode round-trip is symmetric.
"""
from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)

_ROLE_TO_CLASS: dict[str, type[BaseMessage]] = {
    "user": UserMessage,
    "assistant": AssistantMessage,
    "system": SystemMessage,
    "tool": ToolMessage,
}


def encode_message(message: BaseMessage) -> dict:
    """Encode a BaseMessage to a json-native dict."""
    return message.model_dump(mode="json")


def decode_message(data: dict) -> BaseMessage:
    """Decode a json dict back to the proper BaseMessage subclass by role."""
    cls = _ROLE_TO_CLASS.get(data.get("role"), BaseMessage)
    return cls.model_validate(data)


def encode_context_state(state: dict) -> dict:
    """Encode a ``SessionModelContext.save_state()`` result to json-native form.

    Args:
        state: ``{"messages": [BaseMessage], "offload_messages": {handle: [BaseMessage]}}``.

    Returns:
        The same shape with every message replaced by its json dict.
    """
    messages = [encode_message(m) for m in state.get("messages", [])]
    offload_raw = state.get("offload_messages") or {}
    offload = {
        handle: [encode_message(m) for m in msgs]
        for handle, msgs in offload_raw.items()
    }
    return {"messages": messages, "offload_messages": offload}


def decode_context_state(state: dict) -> dict:
    """Decode a json context state back to BaseMessage objects.

    Inverse of :func:`encode_context_state`.
    """
    messages = [decode_message(d) for d in state.get("messages", [])]
    offload_raw = state.get("offload_messages") or {}
    offload = {
        handle: [decode_message(d) for d in msgs]
        for handle, msgs in offload_raw.items()
    }
    return {"messages": messages, "offload_messages": offload}
