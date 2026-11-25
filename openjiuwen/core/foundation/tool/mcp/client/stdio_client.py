# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from contextlib import AsyncExitStack
from typing import Any, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpServerConfig, McpToolCard
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT, extract_mcp_tool_result_content
from openjiuwen.core.foundation.tool.mcp.client.mcp_client import (
    McpClient,
    coerce_mcp_connect_error,
    raise_mcp_operation_error,
)
from openjiuwen.core.foundation.tool.mcp.errors import McpConnectionError


class StdioClient(McpClient):
    """Stdio transport based MCP client"""
    __client_name__ = "stdio"

    def __init__(self, config: McpServerConfig):
        super().__init__(config)
        self._name = config.server_name
        self._client = None
        self._session = None
        self._read = None
        self._write = None
        self._params = config.params if config.params else {}
        self._exit_stack = AsyncExitStack()
        self._is_disconnected: bool = False

    def _resolve_timeout(self, timeout: float) -> float:
        """Resolve an operation bound: explicit > config timeout_s > 60s."""
        if isinstance(timeout, (int, float)) and timeout > 0:
            return float(timeout)
        configured = self._params.get("timeout_s")
        if isinstance(configured, (int, float)) and configured > 0:
            return float(configured)
        return 60.0

    async def connect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Establish Stdio connection to the tool server"""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        
        actual_timeout = self._resolve_timeout(timeout)
        try:
            # server_path should be StdioServerParameters for stdio client
            valid_handlers = {"strict", "ignore", "replace"}
            handler = self._params.get('encoding_error_handler', 'strict')
            if handler not in valid_handlers:
                handler = 'strict'
            params = StdioServerParameters(command=self._params.get('command'),
                                           args=self._params.get('args'),
                                           env=self._params.get('env'),
                                           cwd=self._params.get('cwd'),
                                           encoding_error_handler=handler
                                           )

            # Bound transport startup as well as initialize(): a blocked child
            # process can otherwise hang before the handshake deadline starts.
            async with asyncio.timeout(actual_timeout):
                self._client = stdio_client(params)
                self._read, self._write = await self._exit_stack.enter_async_context(self._client)
                self._session = await self._exit_stack.enter_async_context(
                    ClientSession(self._read, self._write, sampling_callback=None))
                await self._session.initialize()
            self._is_disconnected = False
            logger.info("Stdio client connected successfully")
            return True
        except BaseException as e:
            error = coerce_mcp_connect_error(
                e,
                server_name=self._name,
                timeout=actual_timeout,
            )
            self._last_connect_error = error
            logger.error("Stdio connection failed: %s", error)
            try:
                await self.disconnect()
            except BaseException as cleanup_exc:  # noqa: BLE001 - teardown must not mask the cause
                logger.debug("Stdio connect cleanup failed: %r", cleanup_exc)
            if isinstance(error, McpConnectionError) and isinstance(error, TimeoutError):
                raise error from e
            if not isinstance(e, Exception):
                raise
            return False

    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Close the Stdio connection without hiding caller cancellation."""
        if self._is_disconnected:
            logger.info("Stdio client disconnected successfully")
            return True

        actual_timeout = self._resolve_timeout(timeout)
        owner_task = asyncio.current_task()
        watchdog = None
        watchdog_fired = False
        cancelling_at_start = (
            owner_task.cancelling()
            if owner_task is not None and hasattr(owner_task, "cancelling")
            else 0
        )
        if owner_task is not None:
            loop = asyncio.get_running_loop()

            def _cancel_disconnect() -> None:
                nonlocal watchdog_fired
                if not owner_task.done():
                    watchdog_fired = True
                    owner_task.cancel()

            watchdog = loop.call_later(actual_timeout, _cancel_disconnect)

        try:
            # Exit in the connecting task: wait_for would move an anyio cancel
            # scope into another task and can raise during teardown.
            await self._exit_stack.aclose()
            self._session = None
            self._client = None
            self._read = None
            self._write = None
            self._exit_stack = AsyncExitStack()
            logger.info("Stdio client disconnected successfully")
            self._is_disconnected = True
            return True
        except asyncio.CancelledError:
            cancelling = getattr(owner_task, "cancelling", None)
            external_cancellation = (
                not watchdog_fired
                or (cancelling is not None and cancelling() > cancelling_at_start + 1)
            )
            if external_cancellation:
                raise
            uncancel = getattr(owner_task, "uncancel", None)
            if uncancel is not None:
                uncancel()
            logger.error("Stdio disconnection timed out after %ss", actual_timeout)
            return False
        except Exception as e:
            # aclose() is not re-entrant after a failed scope exit. Calling the
            # transport __aexit__ again can produce a second cancel-scope error.
            logger.error("Stdio disconnection failed: %s", e)
            return False
        finally:
            if watchdog is not None:
                watchdog.cancel()

    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        """List available tools via Stdio"""
        if not self._session:
            raise RuntimeError("Not connected to Stdio server")

        try:
            actual_timeout = self._resolve_timeout(timeout)
            async with asyncio.timeout(actual_timeout):
                tools_response = await self._session.list_tools()
            tools_list = [
                McpToolCard(
                    name=tool.name,
                    server_name=self._name,
                    description=getattr(tool, "description", ""),
                    input_params=getattr(tool, "inputSchema", {}),
                )
                for tool in tools_response.tools
            ]
            logger.info(f"Retrieved {len(tools_list)} tools from Stdio server")
            return tools_list
        except BaseException as exc:
            logger.error("Failed to list tools via Stdio: %s", exc)
            raise_mcp_operation_error(
                exc,
                operation="list_tools",
                server_name=self._name,
                timeout=actual_timeout,
            )

    async def call_tool(self, tool_name: str, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        """Call tool via Stdio"""
        if not self._session:
            raise RuntimeError("Not connected to Stdio server")

        try:
            logger.info(f"Calling tool '{tool_name}' via Stdio with arguments: {arguments}")
            actual_timeout = self._resolve_timeout(timeout)
            async with asyncio.timeout(actual_timeout):
                tool_result = await self._session.call_tool(tool_name, arguments=arguments)
            result_content = extract_mcp_tool_result_content(
                tool_result,
                include_image_content=self._include_image_content,
                tool_name=tool_name,
            )
            logger.info(f"Tool '{tool_name}' call completed via Stdio")
            return result_content
        except BaseException as exc:
            logger.error("Tool call failed via Stdio: %s", exc)
            raise_mcp_operation_error(
                exc, operation="call_tool", server_name=self._name, timeout=actual_timeout
            )

    async def get_tool_info(self, tool_name: str, *, timeout: float = NO_TIMEOUT) -> Optional[Any]:
        """Get specific tool info via Stdio"""
        tools = await self.list_tools(timeout=timeout)
        for tool in tools:
            if tool.name == tool_name:
                logger.debug(f"Found tool info for '{tool_name}' via Stdio")
                return tool
        logger.warning(f"Tool '{tool_name}' not found via Stdio")
        return None

    async def list_resources(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        """List available resources via Stdio"""
        if not self._session:
            raise RuntimeError("Not connected to Stdio server")
        try:
            actual_timeout = self._resolve_timeout(timeout)
            async with asyncio.timeout(actual_timeout):
                response = await self._session.list_resources()
            return response.resources
        except BaseException as exc:
            logger.error("Failed to list resources via Stdio: %s", exc)
            raise_mcp_operation_error(
                exc,
                operation="list_resources",
                server_name=self._name,
                timeout=actual_timeout,
            )

    async def read_resource(self, uri: str, *, timeout: float = NO_TIMEOUT) -> Any:
        """Read a resource by URI via Stdio"""
        if not self._session:
            raise RuntimeError("Not connected to Stdio server")
        try:
            actual_timeout = self._resolve_timeout(timeout)
            async with asyncio.timeout(actual_timeout):
                response = await self._session.read_resource(uri)
            return response.contents
        except BaseException as exc:
            logger.error("Failed to read resource '%s' via Stdio: %s", uri, exc)
            raise_mcp_operation_error(
                exc,
                operation="read_resource",
                server_name=self._name,
                timeout=actual_timeout,
            )
