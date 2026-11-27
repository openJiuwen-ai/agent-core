# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from contextlib import AsyncExitStack
from typing import Any, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpToolCard
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT, McpServerConfig, extract_mcp_tool_result_content
from openjiuwen.core.foundation.tool.mcp.client.mcp_client import (
    McpClient,
    coerce_mcp_connect_error,
    raise_mcp_operation_error,
)
from openjiuwen.core.foundation.tool.mcp.errors import McpConnectionError


class PlaywrightClient(McpClient):
    """Playwright browser session based MCP client"""
    __client_name__ = "playwright"

    def __init__(self, config: McpServerConfig):
        super().__init__(config)
        self._name = config.server_name
        self._client = None
        self._session = None
        self._read = None
        self._write = None
        self._exit_stack = AsyncExitStack()
        self._is_disconnected: bool = False

    def _resolve_timeout(self, timeout: float) -> float | None:
        """Apply a deadline only when the caller explicitly supplies one."""
        if isinstance(timeout, (int, float)) and timeout > 0:
            return float(timeout)
        return None

    async def connect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Establish connection to Playwright MCP server"""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.sse import sse_client
        from mcp.client.stdio import stdio_client

        actual_timeout = self._resolve_timeout(timeout)
        try:
            async with asyncio.timeout(actual_timeout):
                # Determine client type based on server_path type
                if isinstance(self._server_path, StdioServerParameters):
                    self._client = stdio_client(self._server_path)
                    self._read, self._write = await self._exit_stack.enter_async_context(self._client)
                    logger.debug("Using Stdio transport for Playwright client")
                elif isinstance(self._server_path, str) and self._server_path.startswith(("http://", "https://")):
                    self._client = sse_client(self._server_path)
                    self._read, self._write = await self._exit_stack.enter_async_context(self._client)
                    logger.debug("Using SSE transport for Playwright client")
                else:
                    raise ValueError(f"Unsupported server_path type: {type(self._server_path)}")
                self._session = await self._exit_stack.enter_async_context(
                    ClientSession(self._read, self._write, sampling_callback=None))
                await self._session.initialize()
            self._is_disconnected = False
            logger.info("Playwright client connected successfully")
            return True
        except BaseException as e:
            error = coerce_mcp_connect_error(
                e,
                server_name=self._name,
                timeout=actual_timeout,
            )
            self._last_connect_error = error
            logger.error("Playwright connection failed: %s", error)
            try:
                await self.disconnect()
            except BaseException as cleanup_exc:
                logger.debug("Playwright connect cleanup failed: %r", cleanup_exc)
            if isinstance(error, McpConnectionError) and isinstance(error, TimeoutError):
                raise error from e
            if not isinstance(e, Exception):
                raise
            return False

    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Close SSE connection"""
        if self._is_disconnected:
            logger.info("Playwright client disconnected successfully")
            return True
        try:
            await self._exit_stack.aclose()
            logger.info("Playwright client disconnected successfully")
            self._is_disconnected = True
            return True
        except (asyncio.CancelledError, RuntimeError):
            if self._client:
                await self._client.__aexit__(None, None, None)
            logger.info("Playwright client disconnected successfully")
            self._is_disconnected = True
            return True
        except Exception as e:
            logger.error(f"Playwright disconnection failed: {e}")
            return False
        finally:
            self._session = None
            self._client = None
            self._read = None
            self._write = None

    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        """List available browser tools"""
        if not self._session:
            raise RuntimeError("Not connected to Playwright server")

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
            logger.info(f"Retrieved {len(tools_list)} browser tools from Playwright server")
            return tools_list
        except BaseException as exc:
            logger.error("Failed to list browser tools: %s", exc)
            raise_mcp_operation_error(
                exc,
                operation="list_tools",
                server_name=self._name,
                timeout=actual_timeout,
            )

    async def call_tool(self, tool_name: str, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        """Call browser tool"""
        if not self._session:
            raise RuntimeError("Not connected to Playwright server")

        try:
            logger.info(f"Calling browser tool '{tool_name}' with arguments: {arguments}")
            actual_timeout = self._resolve_timeout(timeout)
            async with asyncio.timeout(actual_timeout):
                tool_result = await self._session.call_tool(tool_name, arguments=arguments)
            result_content = extract_mcp_tool_result_content(
                tool_result,
                include_image_content=self._include_image_content,
                tool_name=tool_name,
            )
            logger.info(f"Browser tool '{tool_name}' call completed")
            return result_content
        except BaseException as exc:
            logger.error("Browser tool call failed: %s", exc)
            raise_mcp_operation_error(
                exc,
                operation="call_tool",
                server_name=self._name,
                timeout=actual_timeout,
            )

    async def get_tool_info(self, tool_name: str, *, timeout: float = NO_TIMEOUT) -> Optional[Any]:
        """Get specific browser tool info"""
        tools = await self.list_tools(timeout=timeout)
        for tool in tools:
            if tool.name == tool_name:
                logger.debug(f"Found browser tool info for '{tool_name}'")
                return tool
        logger.warning(f"Browser tool '{tool_name}' not found")
        return None
