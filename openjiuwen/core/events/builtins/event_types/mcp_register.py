# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The ``mcp.register`` event and its MCP-server CORE handler."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import Event, MatchPattern, SessionContext
from openjiuwen.core.foundation.tool import McpServerConfig

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus

MCP_REGISTER = "mcp.register"
McpTransport = Literal["stdio", "sse", "streamable_http"]

_MCP_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
_TRANSPORT_CLIENT_TYPES = {
    "stdio": "stdio",
    "sse": "sse",
    "streamable_http": "streamable-http",
}


def _empty_mapping() -> Mapping[str, str]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class McpStdioSpec:
    """Pure-data stdio declaration; it contains no process or client object."""

    command: str
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=_empty_mapping)
    env_credentials: Mapping[str, str] = field(default_factory=_empty_mapping)


@dataclass(frozen=True, slots=True)
class McpHttpSpec:
    """Pure-data HTTP declaration; it contains no client or live connection."""

    url: str
    headers: Mapping[str, str] = field(default_factory=_empty_mapping)
    header_credentials: Mapping[str, str] = field(default_factory=_empty_mapping)


@dataclass(frozen=True, slots=True)
class McpRegisterSpec:
    """Input accepted by ``ops.mcp.register``."""

    name: str
    transport: McpTransport
    connection: McpStdioSpec | McpHttpSpec
    include_image_content: bool = False
    timeout_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class McpRegisterInput:
    """Validated event input containing only an MCP server declaration."""

    name: str
    transport: McpTransport
    connection: McpStdioSpec | McpHttpSpec
    include_image_content: bool = False
    timeout_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class McpRegisterValue:
    """Successful MCP server registration result."""

    server: str

    def __post_init__(self) -> None:
        if not isinstance(self.server, str) or not self.server:
            raise ValueError("server must be a nonempty string")


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
        """Validate the output after a handler has possibly rewritten it."""
        self.__post_init__()


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
        cwd=connection.cwd,
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
    """Validate and snapshot a pure-data MCP registration request."""
    if not isinstance(data, McpRegisterInput):
        raise TypeError("mcp.register requires McpRegisterInput")
    if not isinstance(data.name, str) or _MCP_NAME_PATTERN.fullmatch(data.name.strip()) is None:
        raise ValueError("invalid MCP server name")
    name = data.name.strip()
    if data.transport not in _TRANSPORT_CLIENT_TYPES:
        raise ValueError("unsupported MCP transport")
    if type(data.include_image_content) is not bool:
        raise TypeError("include_image_content must be a bool")
    if data.timeout_seconds is not None and (
        isinstance(data.timeout_seconds, bool)
        or not isinstance(data.timeout_seconds, int)
        or data.timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a positive integer or None")

    connection = (
        _prepare_stdio_spec(data.connection)
        if data.transport == "stdio"
        else _prepare_http_spec(data.connection)
    )
    return replace(data, name=name, connection=connection)


def build_mcp_register_input(spec: McpRegisterSpec) -> McpRegisterInput:
    """Convert an Ops spec to an event input without creating runtime objects."""
    if not isinstance(spec, McpRegisterSpec):
        raise TypeError("mcp.register requires McpRegisterSpec")
    return prepare_mcp_register_input(
        McpRegisterInput(
            name=spec.name,
            transport=spec.transport,
            connection=spec.connection,
            include_image_content=spec.include_image_content,
            timeout_seconds=spec.timeout_seconds,
        )
    )


def _build_mcp_server_config(data: McpRegisterInput) -> McpServerConfig:
    """Create the native runtime config only inside the CORE handler."""
    client_type = _TRANSPORT_CLIENT_TYPES[data.transport]
    if data.transport == "stdio":
        connection = data.connection
        if not isinstance(connection, McpStdioSpec):
            raise TypeError("stdio transport requires McpStdioSpec")
        params = {
            "command": connection.command,
            "args": list(connection.args),
        }
        if connection.cwd is not None:
            params["cwd"] = connection.cwd
        if connection.env:
            params["env"] = dict(connection.env)
        return McpServerConfig(
            server_name=data.name,
            server_path="",
            client_type=client_type,
            params=params,
            include_image_content=data.include_image_content,
        )

    connection = data.connection
    if not isinstance(connection, McpHttpSpec):
        raise TypeError("HTTP transport requires McpHttpSpec")
    return McpServerConfig(
        server_name=data.name,
        server_path=connection.url,
        client_type=client_type,
        auth_headers=dict(connection.headers),
        include_image_content=data.include_image_content,
    )


def _require_session_context(ops: EventHandlerOps | None) -> SessionContext:
    if ops is None or ops.session_context is None or not ops.session_context.session_id:
        raise RuntimeError("mcp.register requires a session")
    return ops.session_context


def _require_mcp_add_success(result: object) -> None:
    is_ok = getattr(result, "is_ok", None)
    if not callable(is_ok):
        raise TypeError("resource manager returned an invalid MCP registration result")
    if is_ok():
        return
    failure = result.msg() if callable(getattr(result, "msg", None)) else None
    if isinstance(failure, BaseException):
        raise RuntimeError("MCP server registration failed") from failure
    raise RuntimeError("MCP server registration failed")


async def _add_mcp_server(config: McpServerConfig, timeout_seconds: int | None) -> object:
    from openjiuwen.core.runner import Runner

    operation = Runner.resource_mgr.add_mcp_server(config)
    if timeout_seconds is None:
        return await operation
    return await asyncio.wait_for(operation, timeout=timeout_seconds)


async def _cleanup_failed_registration(
    config: McpServerConfig,
    agent: object,
    *,
    resource_attempted: bool,
    ability_added: bool,
) -> None:
    """Remove only resources created by this failed registration attempt."""
    cleanup_errors: list[BaseException] = []
    if ability_added:
        try:
            agent.ability_manager.remove(config.server_name)
        except BaseException as error:  # noqa: BLE001
            cleanup_errors.append(error)

    if resource_attempted:
        try:
            from openjiuwen.core.runner import Runner

            result = await Runner.resource_mgr.remove_mcp_server(
                server_id=config.server_id,
                skip_if_tag_not_exists=True,
                ignore_exception=True,
            )
            if hasattr(result, "is_err") and result.is_err():
                cleanup_errors.append(RuntimeError("MCP server cleanup returned an error"))
        except BaseException as error:  # noqa: BLE001
            cleanup_errors.append(error)

    if cleanup_errors:
        raise RuntimeError("MCP server registration cleanup failed") from cleanup_errors[0]


def match_pattern_func(value: object, pattern: MatchPattern) -> bool:
    """Match valid MCP registration requests with an empty pattern."""
    return isinstance(value, McpRegisterInput) and not pattern


def _is_valid_input(value: object) -> bool:
    if not isinstance(value, McpRegisterInput):
        return False
    try:
        prepare_mcp_register_input(value)
    except (TypeError, ValueError):
        return False
    return True


def _is_valid_output(value: object) -> bool:
    if not isinstance(value, McpRegisterOutput):
        return False
    try:
        value.validate()
    except (TypeError, ValueError):
        return False
    return True


async def core_handler(
    event: Event,
    ops: EventHandlerOps | None,
    event_next: EventNext,
) -> McpRegisterOutput:
    """Connect one MCP server and bind its config to the current DeepAgent."""
    del event_next
    session = _require_session_context(ops)
    data = prepare_mcp_register_input(event.event_input)

    from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
    from openjiuwen.harness.deep_agent import DeepAgent

    agent = get_agent_runtime().get(session.session_id)
    if agent is None:
        raise RuntimeError("session agent is not bound or has been released")
    if not isinstance(agent, DeepAgent):
        raise RuntimeError("mcp.register requires a bound DeepAgent")

    config = _build_mcp_server_config(data)
    if agent.ability_manager.get(config.server_name) is not None:
        return McpRegisterOutput(deny=f"MCP server '{config.server_name}' is already registered")

    resource_attempted = False
    ability_added = False
    try:
        resource_attempted = True
        result = await _add_mcp_server(config, data.timeout_seconds)
        _require_mcp_add_success(result)
        added = agent.ability_manager.add(config)
        if not getattr(added, "added", False):
            raise RuntimeError("MCP ability registration failed")
        ability_added = True
    except BaseException as error:  # noqa: BLE001
        try:
            await _cleanup_failed_registration(
                config,
                agent,
                resource_attempted=resource_attempted,
                ability_added=ability_added,
            )
        except BaseException as cleanup_error:  # noqa: BLE001
            raise RuntimeError("MCP server registration failed and cleanup failed") from cleanup_error
        raise

    return McpRegisterOutput(value=McpRegisterValue(server=config.server_name))


def register(bus: ExtensionEventBus) -> None:
    """Register ``mcp.register`` and its MCP-server CORE handler on ``bus``."""
    bus.register_event_type(
        MCP_REGISTER,
        match_pattern_func,
        core_handler,
        input_validator=_is_valid_input,
        output_validator=_is_valid_output,
    )
