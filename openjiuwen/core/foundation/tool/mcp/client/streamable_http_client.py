# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

import httpx

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpServerConfig, McpToolCard
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT, extract_mcp_tool_result_content
from openjiuwen.core.foundation.tool.mcp.client.mcp_client import McpClient
from openjiuwen.core.foundation.tool.auth.auth import ToolAuthConfig, ToolAuthResult
from openjiuwen.core.runner.callback.events import ToolCallEvents


class StreamableHttpClient(McpClient):
    """Streamable HTTP transport based MCP client."""
    __client_name__ = ["streamable-http", "streamable_http"]

    def __init__(
        self,
        config: McpServerConfig | str,
        name: Optional[str] = None,
        auth_headers: Optional[Dict[str, str]] = None,
        auth_query_params: Optional[Dict[str, str]] = None,
    ):
        resolved_config = self._normalize_config(
            config,
            name=name,
            auth_headers=auth_headers,
            auth_query_params=auth_query_params,
        )
        super().__init__(resolved_config)
        self._name = resolved_config.server_name
        self._client = None
        self._session = None
        self._read = None
        self._write = None
        self._exit_stack = AsyncExitStack()
        self._is_disconnected = False
        self._auth_headers = resolved_config.auth_headers
        self._auth_query_params = resolved_config.auth_query_params
        self._server_id = resolved_config.server_id
        self._auth_provider = None

    @staticmethod
    def _normalize_config(
        config: McpServerConfig | str,
        *,
        name: Optional[str],
        auth_headers: Optional[Dict[str, str]],
        auth_query_params: Optional[Dict[str, str]],
    ) -> McpServerConfig:
        if isinstance(config, McpServerConfig):
            return config
        resolved_name = (name or "").strip() or "streamable-http"
        return McpServerConfig(
            server_id=resolved_name,
            server_name=resolved_name,
            server_path=config,
            client_type="streamable-http",
            auth_headers=auth_headers or {},
            auth_query_params=auth_query_params or {},
        )

    @property
    def server_path(self) -> str:
        return self._server_path

    @property
    def name(self) -> str:
        return self._name

    async def connect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        from mcp import ClientSession
        from mcp.client import streamable_http as streamable_http_module

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
            if isinstance(auth_result, list):
                for item in auth_result:
                    if item and isinstance(item, ToolAuthResult) and item.success:
                        self._auth_provider = item.auth_data.get("auth_provider")
                        break
            actual_timeout = timeout if timeout != NO_TIMEOUT else 60.0
            streamable_http_client = getattr(streamable_http_module, "streamablehttp_client", None)
            if streamable_http_client is None:
                streamable_http_client = getattr(streamable_http_module, "streamable_http_client")
            self._client = streamable_http_client(
                self._server_path,
                timeout=actual_timeout,
                auth=self._auth_provider
            )
            client_tuple = await self._exit_stack.enter_async_context(self._client)
            self._read, self._write, *_ = client_tuple
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(self._read, self._write, sampling_callback=None)
            )
            await self._session.initialize()
            self._is_disconnected = False
            logger.info(f"Streamable-http client connected successfully to {self._server_path}")
            return True
        except Exception as e:
            logger.error(f"Streamable-http connection failed to {self._server_path}: {e}")
            await self.disconnect()
            return False

    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Close streamable-http connection."""
        try:
            await self._exit_stack.aclose()
            self._session = None
            self._client = None
            self._read = None
            self._write = None
            self._is_disconnected = True
            logger.info("Streamable-http client disconnected successfully")
            return True
        except Exception as e:
            logger.error(f"Streamable-http disconnection failed: {e}")
            return False

    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        """List available tools via streamable-http."""
        if not self._session:
            raise RuntimeError("Not connected to streamable-http server")

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
            logger.info(f"Retrieved {len(tools_list)} tools from streamable-http server")
            return tools_list
        except Exception as e:
            logger.error(f"Failed to list tools via streamable-http: {e}")
            raise

    async def call_tool(self, tool_name: str, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        """Call tool via streamable-http."""
        if not self._session:
            raise RuntimeError("Not connected to streamable-http server")

        try:
            logger.info(f"Calling tool '{tool_name}' via streamable-http with arguments: {arguments}")
            tool_result = await self._session.call_tool(tool_name, arguments=arguments)
            result_content = extract_mcp_tool_result_content(tool_result)
            logger.info(f"Tool '{tool_name}' call completed via streamable-http")
            return result_content
        except Exception as e:
            logger.error(f"Tool call failed via streamable-http: {e}")
            raise

    async def get_tool_info(self, tool_name: str, *, timeout: float = NO_TIMEOUT) -> Optional[Any]:
        """Get specific tool info via streamable-http."""
        tools = await self.list_tools(timeout=timeout)
        for tool in tools:
            if tool.name == tool_name:
                logger.debug(f"Found tool info for '{tool_name}' via streamable-http")
                return tool
        logger.warning(f"Tool '{tool_name}' not found via streamable-http")
        return None
