# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""AbilityManager builds tool-result messages through ``Tool.render_for_llm``."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import Tool, ToolCard, ToolOutput
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import (
    AbilityExecutionError,
    AbilityManager,
    resolve_tool_message,
    resolve_tool_result_text,
)
from openjiuwen.core.single_agent.rail.base import ToolCallInputs


class _EchoTool(Tool):
    """Returns a fixed structured result."""

    def __init__(self, output: ToolOutput) -> None:
        super().__init__(ToolCard(id="echo_tool", name="echo_tool", description="echo"))
        self._output = output

    async def invoke(self, inputs: Any, **kwargs: Any) -> ToolOutput:
        return self._output

    async def stream(self, inputs: Any, **kwargs: Any) -> AsyncIterator[ToolOutput]:
        yield self._output


class _CustomRenderTool(_EchoTool):
    def render_for_llm(self, output: ToolOutput) -> str:
        return f"rendered {output.data['count']} item(s)"


class _BrokenRenderTool(_EchoTool):
    def render_for_llm(self, output: ToolOutput) -> str:
        return output.data["missing"]


async def _execute(monkeypatch: pytest.MonkeyPatch, tool: Tool) -> tuple[Any, Any]:
    manager = AbilityManager(owner_id="render-test")
    manager.add(tool.card)
    monkeypatch.setattr(Runner.resource_mgr, "get_tool", lambda **_kwargs: tool)
    call = ToolCall(id="call-1", type="function", name="echo_tool", arguments="{}")
    return await manager._execute_single_tool_call(call, session=None)


@pytest.mark.level0
@pytest.mark.asyncio
async def test_tool_message_uses_default_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    output = ToolOutput(success=True, data={"content": "file body", "path": "/tmp/a.txt"})

    result, message = await _execute(monkeypatch, _EchoTool(output))

    assert result is output
    assert message.content == "file body"
    assert message.tool_call_id == "call-1"


@pytest.mark.level0
@pytest.mark.asyncio
async def test_tool_message_uses_tool_override_and_keeps_structured_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = ToolOutput(success=True, data={"count": 3})

    result, message = await _execute(monkeypatch, _CustomRenderTool(output))

    assert result is output
    assert message.content == "rendered 3 item(s)"


@pytest.mark.level1
@pytest.mark.asyncio
async def test_failing_override_falls_back_to_default_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    output = ToolOutput(success=True, data={"content": "done"})

    result, message = await _execute(monkeypatch, _BrokenRenderTool(output))

    assert result is output
    assert message.content == "done"


@pytest.mark.level0
def test_resolve_tool_result_text_reads_the_message_rails_see() -> None:
    message = ToolMessage(content="rendered text", tool_call_id="call-1")
    inputs = ToolCallInputs(tool_name="echo_tool", tool_result=ToolOutput(success=True), tool_msg=message)

    assert resolve_tool_message(inputs, None) is message
    assert resolve_tool_result_text(inputs, None) == "rendered text"


@pytest.mark.level1
def test_resolve_tool_result_text_uses_the_execution_error_message_when_the_tool_raised() -> None:
    error_message = ToolMessage(content="Tool execution error: boom", tool_call_id="call-1")
    error = AbilityExecutionError(StatusCode.AGENT_TOOL_EXECUTION_ERROR, msg="boom", tool_message=error_message)
    inputs = ToolCallInputs(tool_name="echo_tool")

    assert resolve_tool_result_text(inputs, error) == "Tool execution error: boom"
    assert resolve_tool_result_text(inputs, RuntimeError("no message")) is None


@pytest.mark.level1
def test_resolve_tool_result_text_serializes_non_text_content() -> None:
    message = ToolMessage(content=[{"type": "text", "text": "a"}], tool_call_id="call-1")

    assert resolve_tool_result_text(ToolCallInputs(tool_msg=message), None) == '[{"type": "text", "text": "a"}]'
