# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any, AsyncIterator

from pydantic import Field

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.core.foundation.tool.constant import Input, Output


class McpToolCard(ToolCard):
    input_schema: dict = Field(default_factory=dict)
    server_name: str = Field(default="")


class MCPTool(Tool):
    """MCP Tool class that wraps MCP server tools for LLM modules"""

    def __init__(self, mcp_client: Any, tool_info: McpToolCard):  # McpClient or its subclasses
        """
        Initialize MCP Tool

        Args:
            mcp_client: Instance of McpToolClient or its subclasses
            tool_name: Name of the MCP tool
            server_name: Name of the MCP server (for logging and identification)
        """
        super().__init__(tool_info)
        self.mcp_client = mcp_client

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        raise JiuWenBaseException(
            error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code,
            message=f"mcp tool not support stream mode",
        )

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        try:
            # Prepare arguments for MCP tool call
            arguments = inputs if isinstance(inputs, dict) else {}

            result = await self.mcp_client.call_tool(tool_name=self.name, arguments=arguments)
            return {"result": result}

        except Exception as e:
            raise JiuWenBaseException(
                error_code=StatusCode.PLUGIN_UNEXPECTED_ERROR.code, message=f"Tool invocation failed: {str(e)}"
            )
