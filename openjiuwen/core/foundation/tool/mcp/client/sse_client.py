# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from contextlib import AsyncExitStack
from typing import Any, List, Optional, Dict, AsyncGenerator

import httpx

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpToolCard
from openjiuwen.core.foundation.tool.auth.auth import ToolAuthConfig, ToolAuthResult
from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig, NO_TIMEOUT, extract_mcp_tool_result_content
from openjiuwen.core.foundation.tool.mcp.client.mcp_client import McpClient
from openjiuwen.core.runner.callback.events import ToolCallEvents


class SseClient(McpClient):
    """SSE (Server-Sent Events) transport based MCP client"""
    __client_name__ = "sse"

    def __init__(self, config: McpServerConfig):
        super().__init__(config)
        self._name = config.server_name
        self._client = None
        self._session = None
        self._read = None
        self._write = None
        self._exit_stack = AsyncExitStack()
        self._is_disconnected: bool = False
        self._auth_headers = config.auth_headers
        self._auth_query_params = config.auth_query_params
        self._server_id = config.server_id
        self._auth_provider = None

    @staticmethod
    def _extract_auth_provider(auth_result: Any) -> Any:
        if auth_result is None:
            return None
        if isinstance(auth_result, (list, tuple)):
            auth_items = list(auth_result)
        else:
            auth_items = [auth_result]

        for item in reversed(auth_items):
            if item is None:
                continue
            if isinstance(item, ToolAuthResult) and not item.success:
                continue
            auth_data = getattr(item, "auth_data", None)
            if isinstance(auth_data, dict) and "auth_provider" in auth_data:
                return auth_data.get("auth_provider")
        return None

    async def connect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        try:
            from openjiuwen.core.foundation.tool.auth.auth_callback import AuthType
            from openjiuwen.core.runner import Runner
            framework = Runner.callback_framework
            auth_result = await framework.trigger(
                ToolCallEvents.TOOL_AUTH,
                auth_config=ToolAuthConfig(
                    auth_type=AuthType.HEADER_AND_QUERY,
                    config={
                        "auth_headers": self._auth_headers,
                        "auth_query_params": self._auth_query_params,
                    },
                    tool_type=self._name,
                    tool_id=self._server_id
                ),
            )
            self._auth_provider = self._extract_auth_provider(auth_result)
            actual_timeout = timeout if timeout != NO_TIMEOUT else 60.0
            self._client = sse_client(self._server_path, timeout=actual_timeout, auth=self._auth_provider)
            self._read, self._write = await self._exit_stack.enter_async_context(self._client)
            self._session = await self._exit_stack.enter_async_context(ClientSession(
                self._read, self._write, sampling_callback=None
            ))
            await self._session.initialize()
            self._is_disconnected = False
            logger.info(f"SSE client connected successfully to {self._server_path}")
            return True

        except Exception as e:
            logger.error(f"SSE connection failed to {self._server_path}: {e}")
            await self.disconnect()
            return False

    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Close SSE connection"""
        try:
            if self._session:
                await self._session.__aexit__(None, None, None)
                self._session = None

            if self._client:
                await self._client.__aexit__(None, None, None)
                self._client = None
                self._read = None
                self._write = None

            logger.info("SSE client disconnected successfully")
            return True
        except Exception as e:
            logger.error(f"SSE disconnection failed: {e}")
            return False

    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        """List available tools via SSE"""
        if not self._session:
            raise RuntimeError("Not connected to SSE server")

        try:
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
            logger.info(f"Retrieved {len(tools_list)} tools from SSE server")
            return tools_list
        except Exception as e:
            logger.error(f"Failed to list tools via SSE: {e}")
            raise

    async def call_tool(self, tool_name: str, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        """Call tool via SSE"""
        if not self._session:
            raise RuntimeError("Not connected to SSE server")

        try:
            logger.info(f"Calling tool '{tool_name}' via SSE with arguments: {arguments}")
            tool_result = await self._session.call_tool(tool_name, arguments=arguments)
            result_content = extract_mcp_tool_result_content(tool_result)
            logger.info(f"Tool '{tool_name}' call completed via SSE")
            return result_content
        except Exception as e:
            logger.error(f"Tool call failed via SSE: {e}")
            raise

    async def get_tool_info(self, tool_name: str, *, timeout: float = NO_TIMEOUT) -> Optional[Any]:
        """Get specific tool info via SSE"""
        tools = await self.list_tools(timeout=timeout)
        for tool in tools:
            if tool.name == tool_name:
                logger.debug(f"Found tool info for '{tool_name}' via SSE")
                return tool
        logger.warning(f"Tool '{tool_name}' not found via SSE")
        return None
