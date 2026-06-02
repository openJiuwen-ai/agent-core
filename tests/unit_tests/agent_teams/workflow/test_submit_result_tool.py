# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the structured-output tool a swarmflow worker calls.

Independent of any LLM: exercises ToolCard wiring and the capture contract the
worker backend relies on to read a worker's structured result back.
"""
from __future__ import annotations

import asyncio

from openjiuwen.agent_teams.workflow.backends.submit_result_tool import SubmitResultTool


def test_input_params_mirror_requested_schema():
    """The requested JSON Schema becomes the tool's input_params verbatim."""
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "score": {"type": "integer"}},
        "required": ["answer"],
    }
    tool = SubmitResultTool(schema, tool_id="swarmflow.submit_result.t1")
    assert tool.card.name == "submit_result"
    assert tool.card.id == "swarmflow.submit_result.t1"
    assert tool.card.input_params == schema
    assert tool.called is False
    assert tool.captured is None


def test_invoke_captures_arguments():
    """Calling the tool latches the structured arguments for the backend."""
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    tool = SubmitResultTool(schema)
    out = asyncio.run(tool.invoke({"answer": "42"}))
    assert out.success is True
    assert tool.called is True
    assert tool.captured == {"answer": "42"}


def test_default_schema_when_none():
    """Constructing without a schema yields a well-formed generic fallback."""
    tool = SubmitResultTool(None)
    params = tool.card.input_params
    assert params["type"] == "object"
    assert params["required"] == ["result"]
