# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The single model-visible tool used for progressive tool discovery."""

from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput


class ToolSearchInput(BaseModel):
    query: str = Field(..., description="Query text used to find relevant tools")
    limit: int = Field(default=5, description="Maximum number of tools to return")


class ToolSearchTool(Tool):
    """Search deferred tools and return complete schemas for direct calls."""

    TOOL_NAME = "tool_search"
    TOOL_ID = "ToolSearchTool"

    def __init__(
        self,
        search_tools: Callable[..., Awaitable[List[Dict[str, Any]]]],
        language: str = "cn",
        agent_id: Optional[str] = None,
    ):
        super().__init__(
            build_tool_card(self.TOOL_NAME, self.TOOL_ID, language, agent_id=agent_id)
        )
        self._search_tools = search_tools

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        session = kwargs.get("session")
        try:
            parsed = ToolSearchInput(**(inputs or {}))
            limit = max(1, min(parsed.limit, 20))
            results = await self._search_tools(parsed.query, limit, session)

            logger.info(
                "[ProgressiveToolRail] tool_search invoked | query=%s | "
                "limit=%s | match_count=%s | matched=%s",
                parsed.query,
                limit,
                len(results),
                [item.get("name", "") for item in results],
            )

            return ToolOutput(
                success=True,
                data={
                    "query": parsed.query,
                    "results": results,
                    "count": len(results),
                },
            )
        except Exception as exc:
            logger.warning(
                "[ProgressiveToolRail] tool_search invoke failed | error=%s",
                str(exc),
            )
            return ToolOutput(success=False, error=str(exc))

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None
