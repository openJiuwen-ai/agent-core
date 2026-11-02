# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Entry point plugins use for ``ops.mcp.*``."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TypeVar

from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.ops_impl.common import OpsError
from openjiuwen.core.events.ops_impl.guard import OpsCallGuard
from openjiuwen.core.events.ops_impl.mcp.models import (
    McpCallInput,
    McpCallOutput,
    McpConnectInput,
    McpConnectOutput,
    McpStatus,
)
from openjiuwen.core.events.ops_impl.runtime import build_context
from openjiuwen.core.events.types import Event, SessionContext

_Output = TypeVar("_Output")


@dataclass(frozen=True, slots=True)
class McpOpsFacade:
    """Trigger one ``mcp.*`` event on behalf of a Handler."""

    bus: ExtensionEventBus
    session: SessionContext
    package_id: str = ""

    @classmethod
    def from_ops(cls, bus: ExtensionEventBus, ops: EventHandlerOps) -> McpOpsFacade:
        """Build the facade from the calling handler's ops view."""

        metadata = ops.package_metadata
        return cls(
            bus=bus,
            session=build_context(ops),
            package_id=metadata.package_id if metadata is not None else "",
        )

    async def call(self, request: McpCallInput) -> McpCallOutput:
        """Trigger ``mcp.call`` for one server tool."""

        with OpsCallGuard.enter("mcp.call") as allowed:
            if not allowed:
                return McpCallOutput(
                    status=McpStatus.DENIED,
                    error=OpsError(kind="reentrant_call", message="mcp.call is already active"),
                )
            return await self._trigger("mcp.call", request, McpCallOutput)

    async def connect(self, request: McpConnectInput) -> McpConnectOutput:
        """Trigger ``mcp.connect`` for one registered or declared server."""

        if not request.package_id and self.package_id:
            request = replace(request, package_id=self.package_id)

        with OpsCallGuard.enter("mcp.connect") as allowed:
            if not allowed:
                return McpConnectOutput(
                    status=McpStatus.DENIED,
                    server_id=request.server_id,
                    error=OpsError(kind="reentrant_call", message="mcp.connect is already active"),
                )
            return await self._trigger("mcp.connect", request, McpConnectOutput)

    async def _trigger(self, event_type: str, request: object, output_type: type[_Output]) -> _Output:
        """Run the event chain and return its typed result."""

        output = await self.bus.trigger(Event(event_type, request), self.session)
        if not isinstance(output, output_type):
            raise TypeError(f"{event_type} returned {type(output).__name__}")
        return output
