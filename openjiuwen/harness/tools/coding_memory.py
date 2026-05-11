# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Harness runtime coding memory tools."""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.memory.lite import coding_memory_tool_ops
from openjiuwen.core.memory.lite.coding_memory_tool_context import CodingMemoryToolContext
from openjiuwen.core.memory.lite.config import create_memory_settings
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput


class CodingMemoryReadTool(Tool):
    def __init__(self, ctx: CodingMemoryToolContext, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(build_tool_card("coding_memory_read", "CodingMemoryReadTool", language, agent_id=agent_id))
        self._ctx = ctx


    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        path = inputs.get("path")
        if not path:
            return ToolOutput(success=False, error="path is required")
        result = await coding_memory_tool_ops.coding_memory_read_with_context(
            self._ctx,
            str(path),
            offset=inputs.get("offset"),
            limit=inputs.get("limit"),
        )
        success = bool(result.get("success", False))
        return ToolOutput(success=success, data=result, error=result.get("error"))


    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


class CodingMemoryWriteTool(Tool):
    def __init__(self, ctx: CodingMemoryToolContext, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(build_tool_card("coding_memory_write", "CodingMemoryWriteTool", language, agent_id=agent_id))
        self._ctx = ctx


    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        path = inputs.get("path")
        if not path:
            return ToolOutput(success=False, error="path is required")
        content = inputs.get("content")
        if content is None:
            return ToolOutput(success=False, error="content is required")
        result = await coding_memory_tool_ops.coding_memory_write_with_context(
            self._ctx,
            str(path),
            str(content),
        )
        success = bool(result.get("success", False))
        return ToolOutput(success=success, data=result, error=result.get("error"))


    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


class CodingMemoryEditTool(Tool):
    def __init__(self, ctx: CodingMemoryToolContext, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(build_tool_card("coding_memory_edit", "CodingMemoryEditTool", language, agent_id=agent_id))
        self._ctx = ctx


    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        path = inputs.get("path")
        if not path:
            return ToolOutput(success=False, error="path is required")
        old_text = inputs.get("old_text")
        if old_text is None:
            return ToolOutput(success=False, error="old_text is required")
        new_text = inputs.get("new_text")
        if new_text is None:
            return ToolOutput(success=False, error="new_text is required")
        result = await coding_memory_tool_ops.coding_memory_edit_with_context(
            self._ctx,
            str(path),
            str(old_text),
            str(new_text),
        )
        success = bool(result.get("success", False))
        return ToolOutput(success=success, data=result, error=result.get("error"))


    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


def create_coding_memory_tools(
    ctx: CodingMemoryToolContext,
    *,
    language: str = "cn",
    agent_id: Optional[str] = None,
) -> List[Tool]:
    """Create coding memory tools bound to a shared runtime context."""

    if ctx.workspace is not None:
        coding_memory_dir = str(ctx.workspace.get_node_path("coding_memory") or "")
        ctx.coding_memory_dir = coding_memory_dir
        if ctx.settings is None:
            ctx.settings = create_memory_settings(coding_memory_dir)
    ctx.node_name = "coding_memory"

    return [
        CodingMemoryReadTool(ctx, language=language, agent_id=agent_id),
        CodingMemoryWriteTool(ctx, language=language, agent_id=agent_id),
        CodingMemoryEditTool(ctx, language=language, agent_id=agent_id),
    ]


__all__ = [
    "CodingMemoryToolContext",
    "CodingMemoryReadTool",
    "CodingMemoryWriteTool",
    "CodingMemoryEditTool",
    "create_coding_memory_tools",
]
