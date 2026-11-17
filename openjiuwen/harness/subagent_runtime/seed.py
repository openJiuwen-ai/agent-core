# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Copy a caller's finished turns into a fork seed."""

from __future__ import annotations

from typing import Any


def message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role") or "")
    return str(getattr(message, "role", "") or "")


def message_tool_calls(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("tool_calls")
    return getattr(message, "tool_calls", None)


def _copy_message(message: Any) -> Any:
    model_copy = getattr(message, "model_copy", None)
    if callable(model_copy):
        return model_copy(deep=True)
    return message


def finished_seed(messages: list[Any] | None) -> list[Any]:
    """Return messages from turns that have already ended.

    The in-progress turn is the tail after the last assistant message that has
    no tool calls. That tail holds the spawn that is creating this fork, so it
    stays out of the seed. System messages stay out too: the fork receives the
    caller's system prompt through its own configuration.
    """
    body = [
        message
        for message in (messages or [])
        if message_role(message) != "system"
    ]
    end = len(body)
    while end > 0:
        message = body[end - 1]
        if message_role(message) == "assistant" and not message_tool_calls(message):
            return [_copy_message(item) for item in body[:end]]
        end -= 1
    return []
