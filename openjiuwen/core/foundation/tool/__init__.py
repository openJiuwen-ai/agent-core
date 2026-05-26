# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import TYPE_CHECKING

from openjiuwen.core.foundation.tool.base import Tool, ToolCard, Input, Output
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.foundation.tool.mcp.base import (
    MCPTool,
    McpToolCard, McpServerConfig,
)
from openjiuwen.core.foundation.tool.schema import ToolInfo
from openjiuwen.core.foundation.tool.service_api.restful_api import RestfulApi, RestfulApiCard
from openjiuwen.core.foundation.tool.tool import tool
from openjiuwen.core.foundation.tool.form_handler.form_handler_manager import FormHandler, FormHandlerManager

if TYPE_CHECKING:
    from openjiuwen.core.foundation.tool.mcp.client.mcp_client import McpClient
    from openjiuwen.core.foundation.tool.mcp.client.openapi_client import OpenApiClient
    from openjiuwen.core.foundation.tool.mcp.client.playwright_client import PlaywrightClient
    from openjiuwen.core.foundation.tool.mcp.client.sse_client import SseClient
    from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient
    from openjiuwen.core.foundation.tool.mcp.client.streamable_http_client import StreamableHttpClient

_LAZY_MCP_CLIENTS = {
    "McpClient": "openjiuwen.core.foundation.tool.mcp.client.mcp_client",
    "SseClient": "openjiuwen.core.foundation.tool.mcp.client.sse_client",
    "StdioClient": "openjiuwen.core.foundation.tool.mcp.client.stdio_client",
    "OpenApiClient": "openjiuwen.core.foundation.tool.mcp.client.openapi_client",
    "PlaywrightClient": "openjiuwen.core.foundation.tool.mcp.client.playwright_client",
    "StreamableHttpClient": "openjiuwen.core.foundation.tool.mcp.client.streamable_http_client",
}


def __getattr__(name: str):
    module_name = _LAZY_MCP_CLIENTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    attr = getattr(import_module(module_name), name)
    globals()[name] = attr
    return attr


__all__ = [
    # constants/alias/func
    "Input",
    "Output",
    "tool",
    # all tools
    "Tool",
    "LocalFunction",
    "RestfulApi",
    "MCPTool",
    # for tool info/tool call
    "ToolCard",
    "RestfulApiCard",
    "ToolInfo",
    # for mcp tool
    "McpToolCard",
    "McpServerConfig",
    # mcp client
    "McpClient",
    "SseClient",
    "StdioClient",
    "OpenApiClient",
    "PlaywrightClient",
    "StreamableHttpClient",
    # tool form handler and handler manager
    "FormHandler",
    "FormHandlerManager",
]
