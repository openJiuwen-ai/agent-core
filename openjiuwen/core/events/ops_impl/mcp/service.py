# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run ``mcp.*`` events against servers registered in the resource manager."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.ops_impl.common import (
    OpsError,
    is_timeout_error,
    resolve_timeout_ms,
)
from openjiuwen.core.events.ops_impl.mcp.models import (
    DEFAULT_MCP_CALL_TIMEOUT_MS,
    DEFAULT_MCP_CONNECT_TIMEOUT_MS,
    DEFAULT_MCP_REGISTER_TIMEOUT_MS,
    MCP_TRANSPORT_CLIENT_TYPES,
    McpCallInput,
    McpCallOutput,
    McpConnectInput,
    McpConnectOutput,
    McpRegisterInput,
    McpRegisterOutput,
    McpRegisterValue,
    McpStatus,
    McpToolDescriptor,
    prepare_mcp_register_input,
)
from openjiuwen.core.events.ops_impl.mcp.runtime import require_resource_mgr, resolve_ability_manager
from openjiuwen.core.events.ops_impl.runtime import build_context, resolve_agent
from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig, McpToolResult

if TYPE_CHECKING:
    from openjiuwen.core.extension.schema.mcp import McpServerSpec


def _build_mcp_register_config(data: McpRegisterInput) -> McpServerConfig:
    client_type = MCP_TRANSPORT_CLIENT_TYPES[data.transport]
    if data.transport == "stdio":
        connection = data.connection
        params: dict[str, Any] = {
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
    return McpServerConfig(
        server_name=data.name,
        server_path=connection.url,
        client_type=client_type,
        auth_headers=dict(connection.headers),
        include_image_content=data.include_image_content,
    )


def _require_mcp_add_success(result: object) -> None:
    is_ok = getattr(result, "is_ok", None)
    if callable(is_ok) and is_ok():
        return
    failure = result.msg() if callable(getattr(result, "msg", None)) else None
    if isinstance(failure, BaseException):
        raise RuntimeError("MCP server registration failed") from failure
    raise RuntimeError("MCP server registration failed")


async def _cleanup_failed_registration(
    config: McpServerConfig,
    ability_manager: Any,
    resource_mgr: Any,
    *,
    resource_attempted: bool,
    ability_added: bool,
) -> None:
    cleanup_errors: list[BaseException] = []
    if ability_added:
        try:
            ability_manager.remove(config.server_name)
        except BaseException as error:
            cleanup_errors.append(error)

    if resource_attempted:
        try:
            result = await resource_mgr.remove_mcp_server(
                server_id=config.server_id,
                skip_if_tag_not_exists=True,
                ignore_exception=True,
            )
            results = result if isinstance(result, list) else [result]
            if any(item is not None and getattr(item, "is_err", lambda: False)() for item in results):
                cleanup_errors.append(RuntimeError("MCP server cleanup returned an error"))
        except BaseException as error:
            cleanup_errors.append(error)

    if cleanup_errors:
        raise RuntimeError("MCP server registration cleanup failed") from cleanup_errors[0]


class McpService:
    """Run one ``mcp.*`` request against the caller's resource manager."""

    def __init__(self, resource_mgr: Any, ability_manager: Any | None) -> None:
        """Bind the resource manager and the caller's agent tools."""

        self._resource_mgr = resource_mgr
        self._ability_manager = ability_manager

    @classmethod
    def from_ops(cls, ops: EventHandlerOps) -> McpService:
        """Resolve the resource manager and agent for the calling handler."""

        context = build_context(ops)
        return cls(require_resource_mgr(), resolve_ability_manager(resolve_agent(context)))

    async def register(self, request: McpRegisterInput) -> McpRegisterOutput:
        """Register one server, then bind its tools to the caller's agent."""

        data = prepare_mcp_register_input(request)
        manager = self._ability_manager
        if manager is None:
            return McpRegisterOutput(deny="mcp.register requires an agent AbilityManager")

        config = _build_mcp_register_config(data)
        if manager.get(config.server_name) is not None:
            return McpRegisterOutput(deny=f"MCP server '{config.server_name}' is already registered")

        resource_attempted = False
        ability_added = False
        try:
            resource_attempted = True
            timeout = resolve_timeout_ms(data.timeout_ms, DEFAULT_MCP_REGISTER_TIMEOUT_MS) / 1000
            result = await self._resource_mgr.add_mcp_server(config, timeout=timeout)
            _require_mcp_add_success(result)
            added = manager.add(config)
            if not getattr(added, "added", False):
                raise RuntimeError("MCP ability registration failed")
            ability_added = True
        except BaseException:
            try:
                await _cleanup_failed_registration(
                    config,
                    manager,
                    self._resource_mgr,
                    resource_attempted=resource_attempted,
                    ability_added=ability_added,
                )
            except BaseException as cleanup_error:
                raise RuntimeError("MCP server registration failed and cleanup failed") from cleanup_error
            raise

        return McpRegisterOutput(
            value=McpRegisterValue(server_id=config.server_id, server_name=config.server_name)
        )

    async def connect(self, request: McpConnectInput) -> McpConnectOutput:
        """Open a declared server when needed, then publish it to the agent.

        A server already registered by the host is only described and
        published. A package declaration is registered on first use, which is
        the point where the MCP connection is actually created.
        """

        config = self._server_config(request.server_id) if request.server_id else None
        if config is None:
            spec = self._resolve_spec(request)
            if spec is None:
                return McpConnectOutput(
                    status=McpStatus.SERVER_NOT_FOUND,
                    server_id=request.server_id,
                    error=OpsError(kind="server_not_found", message=_not_found_message(request)),
                )
            config = self._server_config(spec.server_id)
            if config is None:
                config = McpServerConfig(**spec.config_kwargs())
                failure = await self._open(config, request.timeout_ms, spec.package_id)
                if failure is not None:
                    return failure

        tool_ids = self._resource_mgr.get_mcp_tool_ids(config.server_id)
        try:
            infos = await self._resource_mgr.get_tool_infos(tool_id=tool_ids) if tool_ids else []
        except Exception as error:
            return McpConnectOutput(
                status=McpStatus.ERROR,
                server_id=config.server_id,
                server_name=config.server_name,
                error=OpsError(kind="tool_list_failed", message=str(error) or type(error).__name__),
            )
        tools = tuple(McpToolDescriptor.from_info(info) for info in _as_list(infos) if info is not None)
        return McpConnectOutput(
            status=McpStatus.OK,
            server_id=config.server_id,
            server_name=config.server_name,
            connected=True,
            agent_visible=self._publish(config),
            tools=tools,
            tool_count=len(tools),
        )

    async def call(self, request: McpCallInput) -> McpCallOutput:
        """Invoke one tool of a registered server."""

        if self._server_config(request.server_id) is None:
            return McpCallOutput(
                status=McpStatus.SERVER_NOT_FOUND,
                error=OpsError(kind="server_not_found", message=f"unknown MCP server: {request.server_id}"),
            )
        tools = await self._resource_mgr.get_mcp_tool(
            name=request.tool,
            server_id=request.server_id,
            ignore_exception=True,
        )
        tool = _select_tool(tools, request.tool)
        if tool is None:
            return McpCallOutput(
                status=McpStatus.TOOL_NOT_FOUND,
                error=OpsError(kind="tool_not_found", message=f"unknown MCP tool: {request.tool}"),
            )

        try:
            arguments = self._format_arguments(tool, request.arguments)
        except Exception as error:
            return McpCallOutput(
                status=McpStatus.INVALID_INPUT,
                error=OpsError(kind="invalid_arguments", message=str(error) or type(error).__name__),
            )
        invoke_kwargs: dict[str, Any] = {
            "skip_inputs_validate": True,
            "timeout": resolve_timeout_ms(request.timeout_ms, DEFAULT_MCP_CALL_TIMEOUT_MS) / 1000,
        }
        try:
            result = await tool.invoke(arguments, **invoke_kwargs)
        except Exception as error:
            if is_timeout_error(error):
                return McpCallOutput(
                    status=McpStatus.TIMEOUT,
                    error=OpsError(kind="timeout", message="mcp.call exceeded its budget"),
                )
            return McpCallOutput(
                status=McpStatus.ERROR,
                error=OpsError(kind="call_failed", message=str(error) or type(error).__name__),
            )
        return _normalize_result(result)

    def _server_config(self, server_id: str) -> McpServerConfig | None:
        """Return one registered server config; unknown ids read as absent."""

        try:
            return self._resource_mgr.get_mcp_server_config(server_id)
        except Exception:
            return None

    async def _open(self, config: McpServerConfig, timeout_ms: int | None, package_id: str) -> McpConnectOutput | None:
        """Register one declaration with the resource manager.

        Returns a failure output, or None when the server is ready to use.
        """

        timeout = resolve_timeout_ms(timeout_ms, DEFAULT_MCP_CONNECT_TIMEOUT_MS) / 1000
        try:
            result = await self._resource_mgr.add_mcp_server(config, tag=package_id, timeout=timeout)
        except Exception as error:
            if is_timeout_error(error):
                return McpConnectOutput(
                    status=McpStatus.TIMEOUT,
                    server_id=config.server_id,
                    server_name=config.server_name,
                    error=OpsError(kind="timeout", message="mcp.connect exceeded its budget"),
                )
            return McpConnectOutput(
                status=McpStatus.ERROR,
                server_id=config.server_id,
                server_name=config.server_name,
                error=OpsError(kind="connect_failed", message=str(error) or type(error).__name__),
            )
        if getattr(result, "is_err", lambda: False)():
            error = result.msg()
            if is_timeout_error(error):
                return McpConnectOutput(
                    status=McpStatus.TIMEOUT,
                    server_id=config.server_id,
                    server_name=config.server_name,
                    error=OpsError(kind="timeout", message="mcp.connect exceeded its budget"),
                )
            return McpConnectOutput(
                status=McpStatus.ERROR,
                server_id=config.server_id,
                server_name=config.server_name,
                error=OpsError(kind="connect_failed", message=str(error) or type(error).__name__),
            )
        return None

    def _resolve_spec(self, request: McpConnectInput) -> McpServerSpec | None:
        """Find the package declaration a request points at."""

        from openjiuwen.core.extension.schema.mcp import declarations

        if request.server_id:
            return declarations.resolve_server_id(request.server_id)
        if request.package_id and request.server_name:
            return declarations.resolve(request.package_id, request.server_name)
        return None

    def _publish(self, config: McpServerConfig) -> bool:
        """Make the server's tools visible to the caller's agent.

        A changed declaration reuses the same ``server_id``, so an older
        config with that id is replaced instead of keeping the first mount.
        """

        manager = self._ability_manager
        if manager is None:
            return False
        try:
            existing = self._published_config(config.server_id)
            if existing is not None and not _same_config(existing, config):
                manager.remove(config.server_name)
                existing = None
            if existing is None:
                manager.add(config)
            return self._published_config(config.server_id) is not None
        except Exception as error:
            # The connection is live; a publish failure only affects model visibility.
            logger.warning("mcp.connect publish failed: server_id=%s error=%s", config.server_id, error)
            return False

    def _published_config(self, server_id: str) -> McpServerConfig | None:
        """Return the config mounted with this server id, if any."""

        manager = self._ability_manager
        if manager is None:
            return None
        for ability in manager.list():
            if isinstance(ability, McpServerConfig) and ability.server_id == server_id:
                return ability
        return None

    @staticmethod
    def _format_arguments(tool: Any, arguments: Any) -> dict[str, Any]:
        """Fill defaults and drop unset values using the registered tool schema."""

        data = dict(arguments)
        schema = getattr(getattr(tool, "card", None), "input_params", None)
        if not schema:
            return data
        formatted = SchemaUtils.format_with_schema(data, schema, skip_none_value=False)
        return SchemaUtils.remove_none_values(formatted) or {}


def _not_found_message(request: McpConnectInput) -> str:
    """Describe which server or declaration the caller asked for."""

    if request.server_id:
        return f"unknown MCP server: {request.server_id}"
    return f"unknown MCP declaration: {request.package_id}/{request.server_name}"


def _same_config(left: McpServerConfig, right: McpServerConfig) -> bool:
    """Return whether two mounts carry the same declaration."""

    return left.model_dump() == right.model_dump()


def _select_tool(tools: Any, tool_name: str) -> Any | None:
    """Pick one tool from the resource manager's lookup result."""

    if isinstance(tools, list):
        for tool in tools:
            card = getattr(tool, "card", None)
            if tool is not None and getattr(card, "name", None) == tool_name:
                return tool
        return None
    card = getattr(tools, "card", None)
    if card is not None and getattr(card, "name", None) == tool_name:
        return tools
    return None


def _as_list(value: Any) -> list[Any]:
    """Normalize a single-or-list resource lookup result."""

    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _normalize_result(result: Any) -> McpCallOutput:
    """Map an MCP tool result onto the event output."""

    if isinstance(result, McpToolResult):
        return McpCallOutput(
            status=McpStatus.OK,
            result=result.data,
            is_error=not result.success,
            error=OpsError(kind="mcp_tool_error", message=result.error or "MCP tool reported an error")
            if not result.success
            else None,
        )
    if isinstance(result, dict) and set(result) == {"result"}:
        result = result["result"]
    return McpCallOutput(status=McpStatus.OK, result=result)
