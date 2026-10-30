# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the structured-output tool a swarmflow worker calls.

Independent of any LLM: exercises ToolCard wiring and the capture contract the
worker backend relies on to read a worker's structured result back.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import jsonschema
import pytest

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


def test_invoke_raises_on_schema_violation():
    """A schema-violating submission raises instead of being acknowledged.

    The failed call leaves ``captured``/``called`` unset, so a corrected
    resubmission within the same turn is captured normally.
    """
    schema = {
        "type": "object",
        "required": ["answers"],
        "properties": {
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["q_id", "answer"],
                    "properties": {"q_id": {"type": "string"}, "answer": {"type": "string"}},
                },
            },
        },
    }
    tool = StructuredOutputTool(schema)
    with pytest.raises(jsonschema.ValidationError, match="q_id"):
        asyncio.run(tool.invoke({"answers": [{"answer": "missing q_id"}]}))
    assert tool.called is False
    assert tool.captured is None
    out = asyncio.run(tool.invoke({"answers": [{"q_id": "q1", "answer": "ok"}]}))
    assert out.success is True
    assert tool.called is True
    assert tool.captured == {"answers": [{"q_id": "q1", "answer": "ok"}]}


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


def test_card_declares_direct_exposure():
    """The submit tool must stay visible under progressive tool exposure.

    The exposure policy defers every tool whose card did not declare ``exposure``
    and whose name is not in the default-direct set; a deferred submit tool
    would make schema calls fail with "did not submit a structured result".
    """
    from openjiuwen.core.foundation.tool import ToolExposure

    tool = StructuredOutputTool(None)

    assert tool.card.exposure is ToolExposure.DIRECT
    assert "exposure" in tool.card.model_fields_set


def test_submit_tool_stays_direct_under_progressive_policy():
    """Under progressive exposure the policy must resolve the submit tool DIRECT
    (not DEFERRED) because the card declared it — the declared marker short-circuits
    the default-direct-name lookup."""
    from openjiuwen.core.foundation.tool import ToolExposure
    from openjiuwen.core.single_agent.ability_manager import AbilityManager

    manager = AbilityManager(owner_id="progressive-submit-test")
    manager.set_tool_exposure_policy(True, direct_tool_names=frozenset())
    tool = StructuredOutputTool(None)
    manager.add(tool.card)
    assert tool.card.exposure is ToolExposure.DIRECT
