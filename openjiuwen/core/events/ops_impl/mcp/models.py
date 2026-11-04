# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Input and output models for ``mcp.*`` events."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal
from urllib.parse import urlparse

from openjiuwen.core.events.ops_impl.common import OpsError, timeout_is_valid

DEFAULT_MCP_CONNECT_TIMEOUT_MS: Final[int] = 60_000
DEFAULT_MCP_REGISTER_TIMEOUT_MS: Final[int] = 60_000
DEFAULT_MCP_CALL_TIMEOUT_MS: Final[int] = 120_000

McpTransport = Literal["stdio", "sse", "streamable_http"]

_MCP_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
MCP_TRANSPORT_CLIENT_TYPES: Final[dict[str, str]] = {
    "stdio": "stdio",
    "sse": "sse",
    "streamable_http": "streamable-http",
}


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


def _empty_string_mapping() -> Mapping[str, str]:
    return MappingProxyType({})


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
class McpStdioSpec:
    """Pure-data stdio declaration for dynamic MCP registration."""

    command: str
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=_empty_string_mapping)
    env_credentials: Mapping[str, str] = field(default_factory=_empty_string_mapping)


@dataclass(frozen=True, slots=True)
class McpHttpSpec:
    """Pure-data HTTP declaration for dynamic MCP registration."""

    url: str
    headers: Mapping[str, str] = field(default_factory=_empty_string_mapping)
    header_credentials: Mapping[str, str] = field(default_factory=_empty_string_mapping)


@dataclass(frozen=True, slots=True)
class McpRegisterSpec:
    """Input accepted by ``ops.mcp.register``."""

    name: str
    transport: McpTransport
    connection: McpStdioSpec | McpHttpSpec
    include_image_content: bool = False
    timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class McpRegisterInput:
    """Validated event input containing only an MCP server declaration."""

    name: str
    transport: McpTransport
    connection: McpStdioSpec | McpHttpSpec
    include_image_content: bool = False
    timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class McpRegisterValue:
    """Successful MCP server registration result."""

    server_id: str
    server_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.server_id, str) or not self.server_id:
            raise ValueError("server_id must be a nonempty string")
        if not isinstance(self.server_name, str) or not self.server_name:
            raise ValueError("server_name must be a nonempty string")


@dataclass(frozen=True, slots=True)
class McpRegisterOutput:
    """CORE result; exactly one of ``value`` and ``deny`` must be present."""

    value: McpRegisterValue | None = None
    deny: str | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.deny is None):
            raise ValueError("mcp.register output requires exactly one of value or deny")
        if self.value is not None and not isinstance(self.value, McpRegisterValue):
            raise TypeError("value must be McpRegisterValue")
        if self.deny is not None:
            if not isinstance(self.deny, str):
                raise TypeError("deny must be a string")
            if not self.deny.strip():
                raise ValueError("deny must not be empty")

    def validate(self) -> None:
        self.__post_init__()


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


def _snapshot_string_mapping(value: object, field_name: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping of strings")
    snapshot: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError(f"{field_name} must be a mapping of strings")
        snapshot[key] = item
    return MappingProxyType(snapshot)


def _prepare_stdio_spec(connection: object) -> McpStdioSpec:
    if not isinstance(connection, McpStdioSpec):
        raise TypeError("stdio transport requires McpStdioSpec")
    if not isinstance(connection.command, str) or not connection.command.strip():
        raise ValueError("stdio command must be a nonempty string")
    if not isinstance(connection.args, tuple) or any(not isinstance(item, str) for item in connection.args):
        raise TypeError("stdio args must be a tuple of strings")
    if connection.cwd is not None and not isinstance(connection.cwd, str):
        raise TypeError("stdio cwd must be a string or None")
    env = _snapshot_string_mapping(connection.env, "stdio env")
    env_credentials = _snapshot_string_mapping(connection.env_credentials, "stdio env_credentials")
    if env_credentials:
        raise ValueError("stdio env_credentials are not supported by the current runtime")
    return replace(
        connection,
        command=connection.command.strip(),
        args=tuple(connection.args),
        env=env,
        env_credentials=env_credentials,
    )


def _prepare_http_spec(connection: object) -> McpHttpSpec:
    if not isinstance(connection, McpHttpSpec):
        raise TypeError("HTTP transport requires McpHttpSpec")
    if not isinstance(connection.url, str) or not connection.url.strip():
        raise ValueError("MCP URL must be a nonempty string")
    parsed = urlparse(connection.url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("MCP URL must use http or https and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("MCP URL must not contain userinfo")
    if parsed.query:
        raise ValueError("MCP URL must not contain query parameters")
    headers = _snapshot_string_mapping(connection.headers, "HTTP headers")
    header_credentials = _snapshot_string_mapping(connection.header_credentials, "HTTP header_credentials")
    if header_credentials:
        raise ValueError("HTTP header_credentials are not supported by the current runtime")
    return replace(
        connection,
        url=connection.url.strip(),
        headers=headers,
        header_credentials=header_credentials,
    )


def prepare_mcp_register_input(data: McpRegisterInput) -> McpRegisterInput:
    if not isinstance(data, McpRegisterInput):
        raise TypeError("mcp.register requires McpRegisterInput")
    if not isinstance(data.name, str) or _MCP_NAME_PATTERN.fullmatch(data.name.strip()) is None:
        raise ValueError("invalid MCP server name")
    if data.transport not in MCP_TRANSPORT_CLIENT_TYPES:
        raise ValueError("unsupported MCP transport")
    if not isinstance(data.include_image_content, bool):
        raise TypeError("include_image_content must be a bool")
    if not timeout_is_valid(data.timeout_ms):
        raise ValueError("timeout_ms must be a positive integer or None")

    connection = (
        _prepare_stdio_spec(data.connection)
        if data.transport == "stdio"
        else _prepare_http_spec(data.connection)
    )
    return replace(data, name=data.name.strip(), connection=connection)


def build_mcp_register_input(spec: McpRegisterSpec) -> McpRegisterInput:
    if not isinstance(spec, McpRegisterSpec):
        raise TypeError("mcp.register requires McpRegisterSpec")
    return prepare_mcp_register_input(
        McpRegisterInput(
            name=spec.name,
            transport=spec.transport,
            connection=spec.connection,
            include_image_content=spec.include_image_content,
            timeout_ms=spec.timeout_ms,
        )
    )
