# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The team tool factory wrapper logs only; rendering stays on the tool."""

from __future__ import annotations

from typing import Any

import pytest

from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.agent_teams.tools.tool_factory import _wrap_invoke_with_logging
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput


class _CountTool(TeamTool):
    def __init__(self) -> None:
        super().__init__(ToolCard(id="count_tool", name="count_tool", description="count"))

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data={"count": inputs["count"]})

    def render_for_llm(self, output: ToolOutput) -> str:
        return f"{output.data['count']} item(s)"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_wrapped_invoke_returns_the_structured_result_unchanged() -> None:
    tool = _CountTool()
    _wrap_invoke_with_logging(tool)

    result = await tool.invoke({"count": 3})

    assert type(result) is ToolOutput
    assert result.data == {"count": 3}
    assert tool.render_for_llm(result) == "3 item(s)"
