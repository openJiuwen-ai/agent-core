# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
from typing import Any, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpToolCard
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT
from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient
from openjiuwen.harness.tools.browser_move.playwright_runtime.browser_logging import (
    browser_agent_log_info,
    browser_agent_log_warning,
)
from ..utils.parsing import sanitize_json_schema
from .logging_utils import summarize_tool_arguments_for_log


class BrowserMoveStdioClient(StdioClient):
    """browser_move extension of StdioClient.

    Adds retryable-error detection, auto-reconnect, and retry/timeout
    wrapping around list_tools and call_tool. Inherits owner-task actor
    pattern from base StdioClient for safe anyio CancelScope handling.
    """

    @staticmethod
    def _is_retryable_transport_error(error: Exception) -> bool:
        name = type(error).__name__.lower()
        text = str(error).lower()
        markers = (
            "closedresourceerror",
            "brokenresourceerror",
            "endofstream",
            "stream closed",
            "connection closed",
            "remoteprotocolerror",
            "readerror",
            "writeerror",
            "not connected",
            "broken pipe",
        )
        return any(marker in name or marker in text for marker in markers)

    async def _reconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        await self.disconnect(timeout=timeout)
        return await self.connect(timeout=timeout)

    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        """List available tools via Stdio, with auto-reconnect and timeout."""
        if not self._session:
            connected = await self._reconnect(timeout=timeout)
            if not connected:
                raise RuntimeError("Not connected to Stdio server")

        effective_timeout = self._resolve_timeout(timeout)
        for attempt in range(2):
            try:
                tools_response = await asyncio.wait_for(
                    self._session.list_tools(),
                    timeout=effective_timeout,
                )
                tools_list = [
                    McpToolCard(
                        name=tool.name,
                        server_name=self._name,
                        description=getattr(tool, "description", ""),
                        input_params=sanitize_json_schema(getattr(tool, "inputSchema", {})),
                    )
                    for tool in tools_response.tools
                ]
                logger.info(f"Retrieved {len(tools_list)} tools from Stdio server")
                return tools_list
            except asyncio.TimeoutError as e:
                if attempt == 0:
                    logger.warning(
                        f"Stdio list_tools timed out after {effective_timeout:.1f}s, retrying after reconnect"
                    )
                    connected = await self._reconnect(timeout=effective_timeout)
                    if connected:
                        continue
                logger.error(f"Stdio list_tools timed out after {effective_timeout:.1f}s")
                raise RuntimeError(
                    f"Stdio list_tools timed out after {effective_timeout:.1f}s"
                ) from e
            except Exception as e:
                if attempt == 0 and self._is_retryable_transport_error(e):
                    logger.warning(
                        f"Stdio list_tools retry after reconnect: type={type(e).__name__}, repr={e!r}"
                    )
                    connected = await self._reconnect(timeout=timeout)
                    if connected:
                        continue
                logger.error(f"Failed to list tools via Stdio: {e}")
                raise

    async def call_tool(self, tool_name: str, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        """Call tool via Stdio, with auto-reconnect, timeout, and multi-content extraction."""
        if not self._session:
            connected = await self._reconnect(timeout=timeout)
            if not connected:
                raise RuntimeError("Not connected to Stdio server")

        effective_timeout = self._resolve_timeout(timeout)
        for attempt in range(2):
            try:
                browser_agent_log_info(
                    f"Calling tool '{tool_name}' via Stdio with arguments_summary: "
                    f"{summarize_tool_arguments_for_log(tool_name, arguments)}"
                )
                tool_result = await asyncio.wait_for(
                    self._session.call_tool(tool_name, arguments=arguments),
                    timeout=effective_timeout,
                )
                result_content = None
                if tool_result.content and len(tool_result.content) > 0:
                    chunks = []
                    for item in tool_result.content:
                        text = getattr(item, "text", None)
                        if text:
                            chunks.append(text)
                            continue

                        uri = getattr(item, "uri", None)
                        if uri:
                            chunks.append(str(uri))
                            continue

                        data = getattr(item, "data", None)
                        if data is not None:
                            mime = (
                                getattr(item, "mimeType", None)
                                or getattr(item, "mime_type", None)
                                or "application/octet-stream"
                            )
                            chunks.append(f"[binary content: {mime}]")
                            continue

                        chunks.append(str(item))

                    if chunks:
                        result_content = "\n".join(chunks)
                browser_agent_log_info(f"Tool '{tool_name}' call completed via Stdio")
                return result_content
            except asyncio.TimeoutError as e:
                if attempt == 0:
                    browser_agent_log_warning(
                        f"Stdio tool call '{tool_name}' timed out after"
                        f" {effective_timeout:.1f}s, retrying after reconnect"
                    )
                    connected = await self._reconnect(timeout=effective_timeout)
                    if connected:
                        continue
                logger.error(
                    f"Tool call timed out via Stdio: tool='{tool_name}', timeout={effective_timeout:.1f}s"
                )
                raise RuntimeError(
                    f"Stdio tool call timed out for '{tool_name}' after {effective_timeout:.1f}s"
                ) from e
            except Exception as e:
                if attempt == 0 and self._is_retryable_transport_error(e):
                    browser_agent_log_warning(
                        f"Stdio tool call '{tool_name}' retry after reconnect: type={type(e).__name__}, repr={e!r}"
                    )
                    connected = await self._reconnect(timeout=timeout)
                    if connected:
                        continue
                logger.error(
                    f"Tool call failed via Stdio: type={type(e).__name__}, repr={e!r}",
                    exc_info=True,
                )
                raise RuntimeError(
                    f"Stdio tool call failed for '{tool_name}': {type(e).__name__}: {e!r}"
                ) from e

    async def get_tool_info(self, tool_name: str, *, timeout: float = NO_TIMEOUT) -> Optional[Any]:
        """Get specific tool info via Stdio."""
        tools = await self.list_tools(timeout=timeout)
        for tool in tools:
            if tool.name == tool_name:
                logger.debug(f"Found tool info for '{tool_name}' via Stdio")
                return tool
        logger.warning(f"Tool '{tool_name}' not found via Stdio")
        return None

    async def ping(self, *, timeout: float = 5.0) -> bool:
        """Return True if the stdio subprocess is still responsive."""
        if not self._session:
            return False
        try:
            await asyncio.wait_for(self._session.list_tools(), timeout=timeout)
            return True
        except Exception:
            return False
