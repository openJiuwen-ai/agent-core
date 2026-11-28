# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from contextlib import AsyncExitStack
from typing import Any, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpServerConfig, McpToolCard
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT, extract_mcp_tool_result_content
from openjiuwen.core.foundation.tool.mcp.client.mcp_client import McpClient


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
        """Resolve an operation bound: explicit > config timeout_s > 60s.

        Used by connect/list_tools, where a dead server must fail fast
        instead of hanging the caller.
        """
        if isinstance(timeout, (int, float)) and timeout > 0:
            return float(timeout)
        configured = self._params.get("timeout_s")
        if isinstance(configured, (int, float)) and configured > 0:
            return float(configured)
        return 60.0

    def _resolve_call_bound(self, timeout: float) -> Optional[float]:
        """Resolve the per-call bound for ``call_tool``: explicit > config, else unbounded.

        Unlike connect/list_tools, a tool call may legitimately run minutes
        (simulation solves, batch jobs), so ``NO_TIMEOUT`` without a
        configured ``timeout_s`` keeps the SDK default of no per-call
        ceiling — matching ``StreamableHttpClient`` and the pre-fix stdio
        behavior (issue #1976 review). Pass an explicit ``timeout`` or set
        ``params["timeout_s"]`` to bound the call.
        """
        if isinstance(timeout, (int, float)) and timeout > 0:
            return float(timeout)
        configured = self._params.get("timeout_s")
        if isinstance(configured, (int, float)) and configured > 0:
            return float(configured)
        return None

    async def connect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Establish Stdio connection to the tool server"""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        
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

            self._client = stdio_client(params)
            self._read, self._write = await self._exit_stack.enter_async_context(self._client)
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(self._read, self._write, sampling_callback=None))
            # An stdio wrapper may wait on an interactive flow (e.g. OAuth) and
            # never answer initialize(); bound it so the caller cannot hang.
            actual_timeout = self._resolve_timeout(timeout)
            async with asyncio.timeout(actual_timeout):
                await self._session.initialize()
            self._is_disconnected = False
            logger.info("Stdio client connected successfully")
            return True
        except BaseException as e:
            error = e
            if isinstance(e, TimeoutError):
                error = TimeoutError(
                    f"stdio handshake did not complete within {self._resolve_timeout(timeout):g}s"
                )
            logger.error(f"Stdio connection failed: {error}")
            self._last_connect_error = error
            try:
                await self.disconnect()
            except BaseException as cleanup_exc:  # noqa: BLE001 - teardown must not mask the cause
                logger.debug(f"Stdio connect cleanup failed: {cleanup_exc!r}")
            if not isinstance(e, Exception):
                raise
            return False

    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Close SSE connection"""
        if self._is_disconnected:
            logger.info("Stdio client disconnected successfully")
            return True
        try:
            await self._exit_stack.aclose()
            logger.info("Stdio client disconnected successfully")
            self._is_disconnected = True
            return True
        except (asyncio.CancelledError, RuntimeError):
            if self._client:
                await self._client.__aexit__(None, None, None)
            logger.info("Stdio client disconnected successfully")
            self._is_disconnected = True
            return True
        except Exception as e:
            logger.error(f"Stdio disconnection failed: {e}")
            return False
        finally:
            self._session = None
            self._client = None
            self._read = None
            self._write = None

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
        except TimeoutError as exc:
            raise TimeoutError(
                f"stdio list_tools did not complete within {actual_timeout:g}s"
            ) from exc
        except Exception as e:
            logger.error(f"Failed to list tools via Stdio: {e}")
            raise

    async def call_tool(self, tool_name: str, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        """Call tool via Stdio.

        The explicit ``timeout`` (seconds) bounds this single call via
        ``asyncio.timeout`` so long-running tools (e.g. minute-level
        simulation solves) complete when the caller raises the budget, while
        short tools still fail fast (issue #1976 / REQ-001). ``NO_TIMEOUT``
        without a configured ``params["timeout_s"]`` keeps the call
        unbounded, matching ``StreamableHttpClient`` and the SDK default.
        """
        if not self._session:
            raise RuntimeError("Not connected to Stdio server")

        try:
            logger.info(f"Calling tool '{tool_name}' via Stdio with arguments: {arguments}")
            actual_timeout = self._resolve_call_bound(timeout)
            async with asyncio.timeout(actual_timeout):
                tool_result = await self._session.call_tool(tool_name, arguments=arguments)
            result_content = extract_mcp_tool_result_content(
                tool_result,
                include_image_content=self._include_image_content,
                tool_name=tool_name,
            )
            logger.info(f"Tool '{tool_name}' call completed via Stdio")
            return result_content
        except TimeoutError as exc:
            if actual_timeout is None:
                # Unbounded call: the error came from the session/SDK, not from
                # our asyncio.timeout wrapper — keep the original message
                # (raising a reformatted one would lose its diagnostics).
                raise
            raise TimeoutError(
                f"stdio call_tool '{tool_name}' did not complete within "
                f"{actual_timeout:g}s"
            ) from exc
        except Exception as e:
            logger.error(f"Tool call failed via Stdio: {e}")
            raise

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
            response = await self._session.list_resources()
            return response.resources
        except Exception as e:
            logger.error(f"Failed to list resources via Stdio: {e}")
            raise

    async def read_resource(self, uri: str, *, timeout: float = NO_TIMEOUT) -> Any:
        """Read a resource by URI via Stdio"""
        if not self._session:
            raise RuntimeError("Not connected to Stdio server")
        try:
            response = await self._session.read_resource(uri)
            return response.contents
        except Exception as e:
            logger.error(f"Failed to read resource '{uri}' via Stdio: {e}")
            raise
