# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the structured-output tool a swarmflow worker calls.

Independent of any LLM: exercises ToolCard wiring and the capture contract the
worker backend relies on to read a worker's structured result back.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openjiuwen.agent_teams.tools.structured_output_tool import (
    StructuredOutputFinishRail,
    StructuredOutputTool,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)


def _ctx(inputs) -> AgentCallbackContext:
    """Build a minimal callback context carrying the given event inputs."""
    return AgentCallbackContext(agent=SimpleNamespace(), inputs=inputs)


def test_input_params_mirror_requested_schema():
    """The requested JSON Schema becomes the tool's input_params verbatim."""
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "score": {"type": "integer"}},
        "required": ["answer"],
    }
    tool = StructuredOutputTool(schema, tool_id="swarmflow.structured_output.t1")
    assert tool.card.name == "structured_output"
    assert tool.card.id == "swarmflow.structured_output.t1"
    assert tool.card.input_params == schema
    assert tool.called is False
    assert tool.captured is None


def test_invoke_captures_arguments():
    """Calling the tool latches the structured arguments for the backend."""
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    tool = StructuredOutputTool(schema)
    out = asyncio.run(tool.invoke({"answer": "42"}))
    assert out.success is True
    assert tool.called is True
    assert tool.captured == {"answer": "42"}


def test_default_schema_when_none():
    """Constructing without a schema yields a well-formed generic fallback."""
    tool = StructuredOutputTool(None)
    params = tool.card.input_params
    assert params["type"] == "object"
    assert params["required"] == ["result"]


def test_finish_rail_force_finishes_on_structured_output():
    """The rail ends the round when the captured tool is structured_output."""
    rail = StructuredOutputFinishRail()
    ctx = _ctx(ToolCallInputs(tool_name="structured_output", tool_args={"answer": "ok"}))
    asyncio.run(rail.after_tool_call(ctx))
    assert ctx.has_force_finish_request is True


def test_finish_rail_ignores_other_tools():
    """A non-structured_output tool call leaves the round running."""
    rail = StructuredOutputFinishRail()
    ctx = _ctx(ToolCallInputs(tool_name="read_file", tool_args={"path": "a.txt"}))
    asyncio.run(rail.after_tool_call(ctx))
    assert ctx.has_force_finish_request is False


def test_finish_rail_ignores_non_tool_inputs():
    """A non-ToolCallInputs event (e.g. wrong wiring) is a no-op, not a crash."""
    rail = StructuredOutputFinishRail()
    ctx = _ctx({"unexpected": "shape"})
    asyncio.run(rail.after_tool_call(ctx))
    assert ctx.has_force_finish_request is False


def test_finish_rail_skips_force_finish_on_tool_exception():
    """A failed structured_output call must not force-finish.

    When the call fails (e.g. malformed JSON arguments), the error
    tool_message needs to flow back to the model so it can self-correct.
    Force-finishing here would swallow the error and end the round with
    no structured result captured.
    """
    rail = StructuredOutputFinishRail()
    ctx = _ctx(
        ToolCallInputs(tool_name="structured_output", tool_args={"bad": "json"})
    )
    ctx.exception = ValueError("Invalid tool arguments JSON")
    asyncio.run(rail.after_tool_call(ctx))
    assert ctx.has_force_finish_request is False
