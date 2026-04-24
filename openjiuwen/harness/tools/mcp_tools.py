# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""LLM-callable tools for MCP resource listing and reading."""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.runner import Runner
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput


class ListMcpResourcesTool(Tool):
    """列出指定 MCP 服务器的可用资源。"""

    def __init__(self, language: str = "cn", agent_id: Optional[str] = None) -> None:
        super().__init__(build_tool_card("list_mcp_resources", "ListMcpResourcesTool", language, agent_id=agent_id))

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        server_id = inputs.get("server_id")
        if not server_id:
            return ToolOutput(success=False, error="server_id is required")
        try:
            resources = await Runner.resource_mgr.list_mcp_resources(server_id)
            data = [
                {
                    "uri": getattr(r, "uri", str(r)),
                    "name": getattr(r, "name", ""),
                    "mimeType": getattr(r, "mimeType", None),
                    "description": getattr(r, "description", None),
                }
                for r in (resources or [])
            ]
            return ToolOutput(success=True, data=data)
        except Exception as e:
            return ToolOutput(success=False, error=str(e))

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        raise NotImplementedError


class ReadMcpResourceTool(Tool):
    """读取指定 MCP 服务器上某个资源的内容。"""

    def __init__(self, language: str = "cn", agent_id: Optional[str] = None) -> None:
        super().__init__(build_tool_card("read_mcp_resource", "ReadMcpResourceTool", language, agent_id=agent_id))

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        server_id = inputs.get("server_id")
        uri = inputs.get("uri")
        if not server_id:
            return ToolOutput(success=False, error="server_id is required")
        if not uri:
            return ToolOutput(success=False, error="uri is required")
        try:
            contents = await Runner.resource_mgr.read_mcp_resource(server_id, uri)
            data = [
                {
                    "uri": getattr(c, "uri", str(c)),
                    "mimeType": getattr(c, "mimeType", None),
                    "text": getattr(c, "text", None),
                }
                for c in (contents or [])
            ]
            return ToolOutput(success=True, data=data)
        except Exception as e:
            return ToolOutput(success=False, error=str(e))

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        raise NotImplementedError
