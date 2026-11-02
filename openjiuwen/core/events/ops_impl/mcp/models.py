# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Input and output models for ``mcp.call`` / ``mcp.connect``."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from openjiuwen.core.events.ops_impl.common import OpsError

DEFAULT_MCP_CONNECT_TIMEOUT_MS: Final[int] = 60_000
DEFAULT_MCP_CALL_TIMEOUT_MS: Final[int] = 120_000


class McpStatus(StrEnum):
    """Outcome category of one ``mcp.*`` call."""

    OK = "ok"
    INVALID_INPUT = "invalid_input"
    SERVER_NOT_FOUND = "server_not_found"
    TOOL_NOT_FOUND = "tool_not_found"
    TIMEOUT = "timeout"
    DENIED = "denied"
    ERROR = "error"


def _empty_arguments() -> dict[str, object]:
    return {}


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    """Tool metadata returned by ``mcp.connect``."""

    name: str
    description: str
    input_schema: dict[str, object]

    @classmethod
    def from_info(cls, info: object) -> McpToolDescriptor:
        """Build a descriptor from a registered ``ToolInfo``."""

        schema = getattr(info, "parameters", None)
        return cls(
            name=str(getattr(info, "name", "")),
            description=str(getattr(info, "description", "")),
            input_schema=dict(schema) if isinstance(schema, dict) else {},
        )


@dataclass(frozen=True, slots=True)
class McpCallInput:
    """Arguments of one ``mcp.call`` event."""

    server_id: str
    tool: str
    arguments: dict[str, object] = field(default_factory=_empty_arguments)
    timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class McpCallOutput:
    """Result of one ``mcp.call`` event."""

    status: McpStatus
    result: object | None = None
    is_error: bool = False
    error: OpsError | None = None


@dataclass(frozen=True, slots=True)
class McpConnectInput:
    """Arguments of one ``mcp.connect`` event.

    A caller may name an already registered ``server_id``, or the package
    declaration it wants opened: ``package_id`` plus ``server_name``. The
    facade fills ``package_id`` from the calling handler's metadata.
    """

    server_id: str = ""
    package_id: str = ""
    server_name: str = ""
    timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class McpConnectOutput:
    """Result of one ``mcp.connect`` event."""

    status: McpStatus
    server_id: str = ""
    server_name: str | None = None
    connected: bool = False
    agent_visible: bool = False
    tools: tuple[McpToolDescriptor, ...] = ()
    tool_count: int = 0
    error: OpsError | None = None
