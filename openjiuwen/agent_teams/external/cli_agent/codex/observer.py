# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Feed raw Codex SDK notifications into the team OTel span bridge."""

from __future__ import annotations

from typing import Any, Callable

from openjiuwen.harness_providers.codex.mapping import (
    TOOL_ITEM_TYPES,
    enum_value,
    item_type,
    thread_item,
    tool_args,
    tool_name,
    tool_result,
)
from openjiuwen.harness_providers.jsonsafe import to_json_safe

_REASONING_METHODS = frozenset({"item/reasoning/summaryTextDelta", "item/reasoning/textDelta"})


def _raw_notification_param(payload: Any, name: str) -> Any:
    """Read one field from an SDK raw-event payload or UnknownNotification."""
    params = getattr(payload, "params", payload)
    if isinstance(params, dict):
        return params.get(name)
    value = getattr(params, name, None)
    if value is not None:
        return value
    snake_name = "".join(f"_{char.lower()}" if char.isupper() else char for char in name)
    return getattr(params, snake_name, None)


def build_codex_notification_observer(span_bridge: Any) -> Callable[[Any], None]:
    """Return the provider-private observer that traces one member's notifications."""

    def _observe(notification: Any) -> None:
        method = getattr(notification, "method", "")
        payload = getattr(notification, "payload", None)
        if method == "rawResponseItem/completed":
            span_bridge.append_raw_response_item(_raw_notification_param(payload, "item"))
            return
        if method == "rawResponse/completed":
            span_bridge.complete_model_response(
                response_id=_raw_notification_param(payload, "responseId"),
                usage=_raw_notification_param(payload, "usage"),
            )
            return
        if method == "item/agentMessage/delta":
            span_bridge.append_output(str(getattr(payload, "delta", "") or ""))
            return
        if method in _REASONING_METHODS:
            span_bridge.append_reasoning(str(getattr(payload, "delta", "") or ""))
            return
        if method == "thread/tokenUsage/updated":
            usage = getattr(payload, "token_usage", None)
            last = getattr(usage, "last", None)
            total = getattr(usage, "total", None)
            if last is not None:
                span_bridge.record_model_usage(
                    input_tokens=int(getattr(last, "input_tokens", 0) or 0),
                    cached_input_tokens=int(getattr(last, "cached_input_tokens", 0) or 0),
                    output_tokens=int(getattr(last, "output_tokens", 0) or 0),
                    reasoning_output_tokens=int(getattr(last, "reasoning_output_tokens", 0) or 0),
                    total_tokens=int(getattr(last, "total_tokens", 0) or 0),
                    thread_total_tokens=int(getattr(total, "total_tokens", 0) or 0),
                )
            return
        if method in {"item/started", "item/completed"}:
            item = thread_item(payload)
            kind = item_type(item)
            if kind not in TOOL_ITEM_TYPES:
                return
            call_id = str(getattr(item, "id", "") or "")
            name = tool_name(item)
            args = tool_args(item)
            server_name = str(getattr(item, "server", "") or "") if kind == "mcpToolCall" else None
            if method == "item/started":
                span_bridge.start_tool(
                    call_id=call_id,
                    tool_name=name,
                    tool_args=args,
                    item_type=kind,
                    server_name=server_name,
                )
                return
            item_error = getattr(item, "error", None)
            item_status = enum_value(getattr(item, "status", None))
            if item_error is None and item_status in {"failed", "declined"}:
                item_error = {"status": item_status}
            span_bridge.finish_tool(
                call_id=call_id,
                tool_name=name,
                tool_args=args,
                tool_result=tool_result(item),
                item_type=kind,
                server_name=server_name,
                error=to_json_safe(item_error) if item_error is not None else None,
            )
            return
        if method == "error":
            span_bridge.record_error(
                to_json_safe(getattr(payload, "error", None)),
                will_retry=bool(getattr(payload, "will_retry", False)),
            )

    return _observe


__all__ = ["build_codex_notification_observer"]
