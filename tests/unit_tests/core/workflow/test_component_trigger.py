# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Tests for workflow component trigger before and after invoke.

Verifies that triggers registered on COMPONENT_BATCH_INPUT, COMPONENT_BATCH_OUTPUT,
and COMPONENT_STREAM_OUTPUT events fire at the correct timing during workflow component execution.
"""

import pytest
import pytest_asyncio

from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.runner.callback.events import WorkflowEvents
from openjiuwen.core.workflow import Workflow, WorkflowCard, Start, End
from openjiuwen.core.workflow import create_workflow_session
from openjiuwen.core.workflow.components import Session
from openjiuwen.core.workflow.components.base import ComponentAbility
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.workflow.components.component import WorkflowComponent


class SimpleComponent(WorkflowComponent):
    """A simple component that returns its input unchanged."""

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return inputs

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        yield inputs


@pytest.fixture
def callback_framework():
    return Runner.callback_framework


@pytest_asyncio.fixture(autouse=True)
async def cleanup(callback_framework):
    yield
    await callback_framework.unregister_event(WorkflowEvents.COMPONENT_BATCH_INPUT)
    await callback_framework.unregister_event(WorkflowEvents.COMPONENT_BATCH_OUTPUT)
    await callback_framework.unregister_event(WorkflowEvents.COMPONENT_STREAM_OUTPUT)


def _create_simple_workflow():
    """Helper to create a simple workflow: start -> comp -> end."""
    flow = Workflow(card=WorkflowCard(id="test_wf", name="test", version="1.0"))
    flow.set_start_comp("start", Start())
    flow.add_workflow_comp("comp", SimpleComponent(), inputs_schema={"value": "${start.value}"})
    flow.set_end_comp("end", End({"responseTemplate": "{{output}}"}), inputs_schema={"output": "${comp.value}"})
    flow.add_connection("start", "comp")
    flow.add_connection("comp", "end")
    return flow


@pytest.mark.asyncio
async def test_component_batch_input_trigger_fires(callback_framework):
    """COMPONENT_BATCH_INPUT trigger fires during component invoke."""
    triggered = []

    @callback_framework.on_transform(WorkflowEvents.COMPONENT_BATCH_INPUT)
    async def before_trigger(*args, **kwargs):
        triggered.append("before")
        return (args, kwargs)

    flow = _create_simple_workflow()
    await flow.invoke({"value": "test"}, create_workflow_session())

    assert len(triggered) > 0, "COMPONENT_BATCH_INPUT trigger should fire"


@pytest.mark.asyncio
async def test_component_batch_output_trigger_fires(callback_framework):
    """COMPONENT_BATCH_OUTPUT trigger fires during component invoke."""
    triggered = []

    @callback_framework.on_transform(WorkflowEvents.COMPONENT_BATCH_OUTPUT)
    async def after_trigger(result, **kwargs):
        triggered.append("after")
        return result

    flow = _create_simple_workflow()
    await flow.invoke({"value": "test"}, create_workflow_session())

    assert len(triggered) > 0, "COMPONENT_BATCH_OUTPUT trigger should fire"


@pytest.mark.asyncio
async def test_trigger_fires_in_correct_order(callback_framework):
    """INPUT trigger fires before OUTPUT trigger for each component."""
    sequence = []

    @callback_framework.on_transform(WorkflowEvents.COMPONENT_BATCH_INPUT)
    async def input_trigger(*args, **kwargs):
        sequence.append("input")
        return (args, kwargs)

    @callback_framework.on_transform(WorkflowEvents.COMPONENT_BATCH_OUTPUT)
    async def output_trigger(result, **kwargs):
        sequence.append("output")
        return result

    flow = _create_simple_workflow()
    await flow.invoke({"value": "test"}, create_workflow_session())

    # For each component, input should appear before output
    for i in range(0, len(sequence), 2):
        assert sequence[i] == "input"
        if i + 1 < len(sequence):
            assert sequence[i + 1] == "output"


@pytest.mark.asyncio
async def test_component_stream_output_trigger_fires(callback_framework):
    """COMPONENT_STREAM_OUTPUT trigger fires during component stream execution."""
    triggered = []

    @callback_framework.on_transform(WorkflowEvents.COMPONENT_STREAM_OUTPUT)
    async def stream_output_trigger(result, **kwargs):
        triggered.append("stream_output")
        return result

    # Create workflow with STREAM ability to trigger COMPONENT_STREAM_OUTPUT
    flow = Workflow(card=WorkflowCard(id="stream_test_wf", name="stream_test", version="1.0"))
    flow.set_start_comp("start", Start())
    flow.add_workflow_comp(
        "comp",
        SimpleComponent(),
        inputs_schema={"value": "${start.value}"},
        comp_ability=[ComponentAbility.STREAM]  # Enable stream mode
    )
    flow.set_end_comp("end", End({"responseTemplate": "{{output}}"}), inputs_schema={"output": "${comp.value}"})
    flow.add_connection("start", "comp")
    flow.add_connection("comp", "end")

    # Execute workflow with stream to trigger COMPONENT_STREAM_OUTPUT
    async for _ in flow.stream({"value": "test"}, create_workflow_session()):
        pass

    assert len(triggered) > 0, "COMPONENT_STREAM_OUTPUT trigger should fire"