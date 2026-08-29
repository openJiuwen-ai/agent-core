# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Fixed model-visible wrapper for executing discovered deferred tools."""

from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput


class ToolCallInput(BaseModel):
    """Arguments accepted by the fixed ``tool_call`` wrapper."""

    name: str = Field(..., description="Exact tool name returned by tool_search")
    args: Dict[str, Any] = Field(
        ...,
        description="Arguments matching the schema returned by tool_search",
    )


class ToolCallTool(Tool):
    """Execute a deferred tool without changing the model-visible tool list.

    The actual execution callback is supplied by ``ProgressiveToolRail``.  It
    receives the callback context from ``AbilityManager`` so the target call can
    reuse the normal tool-rail lifecycle instead of invoking a resource directly.
    """

    TOOL_NAME = "tool_call"
    TOOL_ID = "ToolCallTool"
    accepts_tool_callback_context = True

    def __init__(
        self,
        call_tool: Callable[..., Awaitable[Any]],
        language: str = "cn",
        agent_id: Optional[str] = None,
    ):
        super().__init__(
            build_tool_card(self.TOOL_NAME, self.TOOL_ID, language, agent_id=agent_id)
        )
        self._call_tool = call_tool

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        session = kwargs.get("session")
        callback_context = kwargs.get("_tool_callback_context")
        try:
            parsed = ToolCallInput(**(inputs or {}))
            if callback_context is None:
                raise RuntimeError("tool_call requires an active agent callback context")

            result = await self._call_tool(
                parsed.name,
                parsed.args,
                session,
                callback_context,
            )
            if isinstance(result, ToolOutput):
                return result
            return ToolOutput(
                success=True,
                data={
                    "name": parsed.name,
                    "result": result,
                },
            )
        except Exception as exc:
            logger.warning(
                "[ProgressiveToolRail] tool_call invoke failed | error=%s",
                str(exc),
            )
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None
