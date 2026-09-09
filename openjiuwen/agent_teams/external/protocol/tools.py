# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native-tool and MCP service descriptions for external harnesses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

from openjiuwen.agent_teams.external.protocol.models import JsonObject, JsonValue, freeze_json_object, freeze_json_value


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Provider-neutral definition of a tool exposed by OpenJiuwen."""

    name: str
    description: str
    input_schema: JsonObject

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tool definition name must not be empty")
        object.__setattr__(self, "input_schema", freeze_json_object(self.input_schema))


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """One native tool invocation requested by an external harness."""

    call_id: str
    name: str
    arguments: JsonObject

    def __post_init__(self) -> None:
        if not self.call_id or not self.name:
            raise ValueError("tool invocation call_id and name must not be empty")
        object.__setattr__(self, "arguments", freeze_json_object(self.arguments))


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """Provider-neutral result of a native tool invocation."""

    content: JsonValue
    is_error: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", freeze_json_value(self.content))


@runtime_checkable
class ExternalToolGateway(Protocol):
    """Native SDK tool surface supplied by the OpenJiuwen host."""

    async def definitions(self) -> tuple[ToolDefinition, ...]:
        """Return the tools visible to this member."""
        ...

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """Execute one visible tool under the host's permission policy."""
        ...


class McpTransport(str, Enum):
    """Transport used to expose an MCP server to an external harness."""

    STDIO = "stdio"
    HTTP = "http"
    IN_PROCESS = "in_process"


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """Provider-neutral MCP server configuration.

    Exactly one transport-specific target is required: ``command`` for stdio,
    ``url`` for HTTP, or ``instance`` for an in-process SDK server.
    """

    name: str
    transport: McpTransport
    command: tuple[str, ...] = ()
    url: str | None = None
    instance: object | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Reject incomplete transport configurations early."""
        if not self.name:
            raise ValueError("MCP server name must not be empty")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.env.items()):
            raise TypeError("MCP env must map strings to strings")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.headers.items()):
            raise TypeError("MCP headers must map strings to strings")
        object.__setattr__(self, "command", tuple(self.command))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        if self.transport is McpTransport.STDIO and not self.command:
            raise ValueError("stdio MCP server requires a non-empty command")
        if self.transport is McpTransport.HTTP and not self.url:
            raise ValueError("HTTP MCP server requires url")
        if self.transport is McpTransport.IN_PROCESS and self.instance is None:
            raise ValueError("in-process MCP server requires instance")
        if self.transport is not McpTransport.STDIO and self.env:
            raise ValueError("MCP env is only valid for stdio transport")
        if self.transport is not McpTransport.HTTP and self.headers:
            raise ValueError("MCP headers are only valid for HTTP transport")
        target_count = int(bool(self.command)) + int(self.url is not None) + int(self.instance is not None)
        if target_count != 1:
            raise ValueError("MCP server requires exactly one of command, url, or instance")


__all__ = [
    "ExternalToolGateway",
    "McpServerConfig",
    "McpTransport",
    "ToolDefinition",
    "ToolExecutionResult",
    "ToolInvocation",
]
