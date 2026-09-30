# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the default model-facing rendering of tool results."""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from openjiuwen.core.foundation.tool import (
    LocalFunction,
    McpToolCard,
    McpToolResult,
    MCPTool,
    Tool,
    ToolCard,
    ToolOutput,
)
from openjiuwen.core.foundation.tool.base import (
    EMPTY_FAILURE_TEXT,
    EMPTY_SUCCESS_TEXT,
    render_payload_text,
    render_tool_output,
)


class _PlainTool(Tool):
    """Tool without a rendering override."""

    def __init__(self) -> None:
        super().__init__(ToolCard(id="plain_tool", name="plain_tool", description="plain"))

    async def invoke(self, inputs: Any, **kwargs: Any) -> Any:
        return inputs

    async def stream(self, inputs: Any, **kwargs: Any) -> AsyncIterator[Any]:
        yield inputs


class _Payload(BaseModel):
    name: str
    size: int


@pytest.mark.level0
def test_success_renders_content_only() -> None:
    output = ToolOutput(success=True, data={"content": "hello", "path": "/tmp/a.txt", "multimodal": [{"k": 1}]})

    assert _PlainTool().render_for_llm(output) == "hello"


@pytest.mark.level0
def test_failure_renders_error() -> None:
    output = ToolOutput(success=False, data={"content": "partial"}, error="boom")

    assert _PlainTool().render_for_llm(output) == "boom"


@pytest.mark.level1
def test_success_string_payload_is_used_as_is() -> None:
    assert render_tool_output(ToolOutput(success=True, data="plain text")) == "plain text"


@pytest.mark.level1
def test_success_payload_without_content_renders_readable_json() -> None:
    output = ToolOutput(success=True, data={"文件": "a.txt", "ok": True, "missing": None})

    assert render_tool_output(output) == '{"文件": "a.txt", "ok": true, "missing": null}'


@pytest.mark.level1
def test_non_string_content_and_models_render_as_json() -> None:
    assert render_payload_text({"content": ["a", "b"]}) == '["a", "b"]'
    assert render_payload_text([_Payload(name="x", size=1)]) == '[{"name": "x", "size": 1}]'


@pytest.mark.level1
@pytest.mark.parametrize("data", [None, {"content": ""}, {"content": None}, ""])
def test_empty_success_renders_placeholder(data: Any) -> None:
    assert render_tool_output(ToolOutput(success=True, data=data)) == EMPTY_SUCCESS_TEXT


@pytest.mark.level1
def test_failure_without_error_falls_back_to_payload_then_placeholder() -> None:
    assert render_tool_output(ToolOutput(success=False, data={"message": "cancel failed"})) == (
        '{"message": "cancel failed"}'
    )
    assert render_tool_output(ToolOutput(success=False, error="")) == EMPTY_FAILURE_TEXT


@pytest.mark.level1
def test_non_tool_output_result_renders_as_str() -> None:
    assert _PlainTool().render_for_llm({"a": 1}) == "{'a': 1}"
    assert _PlainTool().render_for_llm("text") == "text"


@pytest.mark.level1
def test_mcp_tool_renders_wrapped_result_value() -> None:
    tool = MCPTool(MagicMock(), McpToolCard(id="srv.echo", name="echo", server_name="srv"))

    assert tool.render_for_llm({"result": "London"}) == "London"
    assert tool.render_for_llm({"result": {"temp": 20}}) == '{"temp": 20}'
    assert tool.render_for_llm({"result": None}) == EMPTY_SUCCESS_TEXT
    assert tool.render_for_llm(McpToolResult(data={"content": "shot", "multimodal": [{"type": "image"}]})) == "shot"
    # The streamed structured result keeps its own shape, not ToolOutput's fields.
    assert set(McpToolResult(data={"content": "shot"}).model_dump()) == {"success", "data", "error"}


@pytest.mark.level1
def test_local_function_uses_render_argument_when_given() -> None:
    card = ToolCard(id="fn_tool", name="fn_tool", description="fn")
    output = ToolOutput(success=True, data={"count": 2})

    assert LocalFunction(card, lambda: output).render_for_llm(output) == '{"count": 2}'
    rendered = LocalFunction(card, lambda: output, render=lambda out: f"{out.data['count']} rows")
    assert rendered.render_for_llm(output) == "2 rows"
