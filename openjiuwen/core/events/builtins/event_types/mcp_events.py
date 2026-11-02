# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``mcp.call`` / ``mcp.connect`` event types and their CORE handlers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import anyio

from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.ops_impl.common import (
    OpsError,
    no_match_pattern,
    resolve_timeout_ms,
    timeout_is_valid,
)
from openjiuwen.core.events.ops_impl.mcp.models import (
    DEFAULT_MCP_CALL_TIMEOUT_MS,
    McpCallInput,
    McpCallOutput,
    McpConnectInput,
    McpConnectOutput,
    McpStatus,
)
from openjiuwen.core.events.ops_impl.mcp.service import McpService
from openjiuwen.core.events.types import Event, EventInput

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus

MCP_CALL_EVENT_TYPE = "mcp.call"
MCP_CONNECT_EVENT_TYPE = "mcp.connect"


def call_input_validator(event_input: EventInput) -> bool:
    """Validate the fields of one ``McpCallInput``."""

    return (
        isinstance(event_input, McpCallInput)
        and isinstance(event_input.server_id, str)
        and bool(event_input.server_id)
        and isinstance(event_input.tool, str)
        and bool(event_input.tool)
        and isinstance(event_input.arguments, Mapping)
        and timeout_is_valid(event_input.timeout_ms)
    )


def connect_input_validator(event_input: EventInput) -> bool:
    """Validate the fields of one ``McpConnectInput``.

    A request names either a registered ``server_id`` or the package
    declaration to open (``package_id`` plus ``server_name``).
    """

    if not isinstance(event_input, McpConnectInput):
        return False
    if not isinstance(event_input.server_id, str):
        return False
    if not isinstance(event_input.package_id, str) or not isinstance(event_input.server_name, str):
        return False
    if not (event_input.server_id or (event_input.package_id and event_input.server_name)):
        return False
    return timeout_is_valid(event_input.timeout_ms)


async def mcp_call_core(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> McpCallOutput:
    """Run ``mcp.call`` against the session's MCP connection."""

    del event_next
    request = event.event_input
    if not isinstance(request, McpCallInput):
        raise TypeError("mcp.call requires McpCallInput")
    if ops is None or ops.session_context is None:
        raise RuntimeError("mcp.call requires EventHandlerOps")
    try:
        with anyio.fail_after(resolve_timeout_ms(request.timeout_ms, DEFAULT_MCP_CALL_TIMEOUT_MS) / 1000):
            return await McpService.from_ops(ops).call(request)
    except TimeoutError:
        return McpCallOutput(
            status=McpStatus.TIMEOUT,
            error=OpsError(kind="timeout", message="mcp.call exceeded its budget"),
        )


async def mcp_connect_core(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> McpConnectOutput:
    """Publish one registered server's tools to the session's agent."""

    del event_next
    request = event.event_input
    if not isinstance(request, McpConnectInput):
        raise TypeError("mcp.connect requires McpConnectInput")
    if ops is None or ops.session_context is None:
        raise RuntimeError("mcp.connect requires EventHandlerOps")
    return await McpService.from_ops(ops).connect(request)


def register(bus: ExtensionEventBus) -> None:
    """Register both ``mcp.*`` event types."""

    bus.register_event_type(
        MCP_CALL_EVENT_TYPE,
        no_match_pattern,
        mcp_call_core,
        input_validator=call_input_validator,
        output_validator=lambda value: isinstance(value, McpCallOutput),
    )
    bus.register_event_type(
        MCP_CONNECT_EVENT_TYPE,
        no_match_pattern,
        mcp_connect_core,
        input_validator=connect_input_validator,
        output_validator=lambda value: isinstance(value, McpConnectOutput),
    )
