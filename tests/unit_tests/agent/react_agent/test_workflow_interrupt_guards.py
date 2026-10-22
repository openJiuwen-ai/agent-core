# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression coverage for workflow interrupt condition guards."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.single_agent import AgentCard, ReActAgent
from openjiuwen.core.workflow import WorkflowExecutionState, WorkflowOutput


@pytest.fixture
def agent():
    return ReActAgent(card=AgentCard(id="workflow-interrupt-guards"))


def test_extract_workflow_component_ids_skips_incomplete_schemas(agent):
    schemas = [
        object(),
        SimpleNamespace(type="answer", payload=SimpleNamespace(id="ignore")),
        SimpleNamespace(type="__interaction__"),
        SimpleNamespace(type="__interaction__", payload=None),
        SimpleNamespace(type="__interaction__", payload=SimpleNamespace(id="b")),
        SimpleNamespace(type="__interaction__", payload=SimpleNamespace(id="a")),
    ]
    output = WorkflowOutput(result=schemas, state=WorkflowExecutionState.INPUT_REQUIRED)
    assert agent._extract_component_ids(output) == ["a", "b"]


def test_extract_list_component_ids_preserves_missing_id_fallback(agent):
    schemas = [
        object(),
        SimpleNamespace(type="answer", payload={"component_id": "ignore"}),
        SimpleNamespace(type="__interaction__"),
        SimpleNamespace(type="__interaction__", payload=None),
        SimpleNamespace(type="__interaction__", payload=SimpleNamespace(id="ignore")),
        SimpleNamespace(type="__interaction__", payload={"component_id": "b"}),
        SimpleNamespace(type="__interaction__", payload={}),
        SimpleNamespace(type="__interaction__", payload={"component_id": "a"}),
    ]
    assert agent._extract_component_ids(schemas) == ["", "a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("component_ids", [[], ["a", "b"]])
async def test_interrupt_stream_preserves_pending_component_filter(agent, component_ids):
    schemas = [
        object(),
        SimpleNamespace(payload=None),
        SimpleNamespace(payload=SimpleNamespace()),
        SimpleNamespace(payload=SimpleNamespace(id="b")),
        SimpleNamespace(payload=SimpleNamespace(id="a")),
    ]
    output = WorkflowOutput(result=schemas, state=WorkflowExecutionState.INPUT_REQUIRED)
    session = SimpleNamespace(write_stream=AsyncMock())
    await agent._write_invoke_result_to_stream(
        {"result_type": "interrupt", "workflow_execution_state": output, "component_ids": component_ids}, session
    )
    written = [call.args[0] for call in session.write_stream.await_args_list]
    assert written == (schemas if not component_ids else [schemas[-1]])


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow_state", [None, SimpleNamespace(result="not a list")])
async def test_interrupt_stream_ignores_missing_schemas(agent, workflow_state):
    session = SimpleNamespace(write_stream=AsyncMock())
    await agent._write_invoke_result_to_stream(
        {"result_type": "interrupt", "workflow_execution_state": workflow_state}, session
    )
    session.write_stream.assert_not_awaited()
