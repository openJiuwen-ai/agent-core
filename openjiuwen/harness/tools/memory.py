# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Harness runtime memory tools.

These tools intentionally build ToolCard via the prompts registry
(``openjiuwen.harness.prompts.tools.build_tool_card``) so runtime cards stay
consistent with prompt-side schema/description.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.memory.lite.config import create_memory_settings
from openjiuwen.core.memory.lite.memory_tool_context import MemoryToolContext
from openjiuwen.core.memory.lite import memory_tool_ops
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput


class MemorySearchTool(Tool):
    def __init__(self, ctx: MemoryToolContext, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(build_tool_card("memory_search", "MemorySearchTool", language, agent_id=agent_id))
        self._ctx = ctx

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        query = inputs.get("query")
        if not query:
            return ToolOutput(success=False, error="query is required")
        result = await memory_tool_ops.memory_search_with_context(
            self._ctx,
            str(query),
            max_results=inputs.get("max_results"),
            min_score=inputs.get("min_score"),
            session_key=inputs.get("session_key"),
        )
        disabled = bool(result.get("disabled", False))
        return ToolOutput(success=not disabled, data=result, error=result.get("error"))

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


class MemoryGetTool(Tool):
    def __init__(self, ctx: MemoryToolContext, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(build_tool_card("memory_get", "MemoryGetTool", language, agent_id=agent_id))
        self._ctx = ctx

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        path = inputs.get("path")
        if not path:
            return ToolOutput(success=False, error="path is required")
        result = await memory_tool_ops.memory_get_with_context(
            self._ctx,
            str(path),
            from_line=inputs.get("from_line"),
            lines=inputs.get("lines"),
        )
        disabled = bool(result.get("disabled", False))
        return ToolOutput(success=not disabled, data=result, error=result.get("error"))

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


class ReadMemoryTool(Tool):
    def __init__(self, ctx: MemoryToolContext, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(build_tool_card("read_memory", "ReadMemoryTool", language, agent_id=agent_id))
        self._ctx = ctx

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        path = inputs.get("path")
        if not path:
            return ToolOutput(success=False, error="path is required")
        result = await memory_tool_ops.read_memory_with_context(
            self._ctx,
            str(path),
            offset=inputs.get("offset"),
            limit=inputs.get("limit"),
        )
        success = bool(result.get("success", False))
        return ToolOutput(success=success, data=result, error=result.get("error"))

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


class WriteMemoryTool(Tool):
    def __init__(self, ctx: MemoryToolContext, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(build_tool_card("write_memory", "WriteMemoryTool", language, agent_id=agent_id))
        self._ctx = ctx

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        path = inputs.get("path")
        if not path:
            return ToolOutput(success=False, error="path is required")
        content = inputs.get("content")
        if content is None:
            return ToolOutput(success=False, error="content is required")
        result = await memory_tool_ops.write_memory_with_context(
            self._ctx,
            str(path),
            str(content),
            append=bool(inputs.get("append", False)),
        )
        success = bool(result.get("success", False))
        return ToolOutput(success=success, data=result, error=result.get("error"))

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


class EditMemoryTool(Tool):
    def __init__(self, ctx: MemoryToolContext, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(build_tool_card("edit_memory", "EditMemoryTool", language, agent_id=agent_id))
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
        result = await memory_tool_ops.edit_memory_with_context(
            self._ctx,
            str(path),
            str(old_text),
            str(new_text),
        )
        success = bool(result.get("success", False))
        return ToolOutput(success=success, data=result, error=result.get("error"))

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


def create_memory_tools(
    ctx: MemoryToolContext,
    *,
    language: str = "cn",
    agent_id: Optional[str] = None,
) -> List[Tool]:
    """Create memory tools bound to a shared runtime context."""

    if ctx.settings is None and ctx.workspace is not None:
        memory_dir = str(ctx.workspace.get_node_path("memory") or "")
        ctx.settings = create_memory_settings(memory_dir)
    ctx.node_name = ctx.node_name or "memory"

    return [
        MemorySearchTool(ctx, language=language, agent_id=agent_id),
        MemoryGetTool(ctx, language=language, agent_id=agent_id),
        WriteMemoryTool(ctx, language=language, agent_id=agent_id),
        EditMemoryTool(ctx, language=language, agent_id=agent_id),
        ReadMemoryTool(ctx, language=language, agent_id=agent_id),
    ]


__all__ = [
    "MemoryToolContext",
    "MemorySearchTool",
    "MemoryGetTool",
    "WriteMemoryTool",
    "EditMemoryTool",
    "ReadMemoryTool",
    "create_memory_tools",
]
