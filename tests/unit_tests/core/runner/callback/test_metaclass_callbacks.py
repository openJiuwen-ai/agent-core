# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Tests for metaclass-applied callback stacking on Tool, Workflow, and BaseModelClient.

Verifies that:
- trigger_on_call fires for INPUT events with already-transformed args
- emits / emits_stream fires for OUTPUT events with already-transformed values
- trigger() skips transform-type callbacks (no double-execution)
- transform fires before trigger for the same event
"""

import pytest
import pytest_asyncio

from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import LLMCallEvents, ToolCallEvents, WorkflowEvents
from openjiuwen.core.workflow.workflow import Workflow


# === Echo implementations ===


class _EchoTool(Tool):
    async def invoke(self, inputs, **kwargs):
        return inputs

    async def stream(self, inputs, **kwargs):
        yield inputs


class _EchoWorkflow(Workflow):
    async def invoke(self, inputs, **kwargs):
        return inputs

    async def stream(self, inputs, **kwargs):
        yield inputs


class _EchoModelClient(BaseModelClient):
    def _validate_config(self):
        pass  # skip api_key / api_base check

    async def invoke(self, messages, **kwargs):
        return messages

    async def stream(self, messages, **kwargs):
        yield messages

    async def generate_image(self, messages, **kwargs):
        return None

    async def generate_speech(self, messages, **kwargs):
        return None

    async def generate_video(self, messages, **kwargs):
        return None


# === Helpers ===


def _make_tool() -> _EchoTool:
    return _EchoTool(ToolCard(id="echo-tool"))


def _make_workflow() -> _EchoWorkflow:
    return _EchoWorkflow()


def _make_model() -> _EchoModelClient:
    return _EchoModelClient(
        ModelRequestConfig(),
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="https://example.com",
        ),
    )


# === Fixtures ===


@pytest_asyncio.fixture(autouse=True)
async def cleanup_callbacks():
    """Unregister all event callbacks from Runner.callback_framework after each test."""
    yield
    fw = Runner.callback_framework
    for event in list(fw.callbacks.keys()):
        await fw.unregister_event(event)


# === trigger() skips transform callbacks ===


@pytest.mark.asyncio
async def test_trigger_skips_transform_callbacks():
    """trigger() returns [] when only transform-type callbacks are registered."""
    fw = Runner.callback_framework

    @fw.on_transform("test_event")
    async def t(result):
        return result + "-x"

    results = await fw.trigger("test_event", result="v")
    assert results == []


# === Tool.invoke stacking ===


@pytest.mark.asyncio
async def test_tool_invoke_trigger_on_call_with_transformed_input():
    """Normal INPUT callback receives already-transformed input args."""
    fw = Runner.callback_framework
    tool = _make_tool()
    received = []

    @fw.on_transform(ToolCallEvents.TOOL_INVOKE_INPUT)
    async def t_in(*args, **kwargs):
        return (("transformed",), {})

    @fw.on(ToolCallEvents.TOOL_INVOKE_INPUT)
    async def record_in(*args, **kwargs):
        received.append(args[0] if args else None)

    result = await tool.invoke("original")
    assert received == ["transformed"]
    assert result == "transformed"


@pytest.mark.asyncio
async def test_tool_invoke_emits_with_transformed_output():
    """Normal OUTPUT callback receives already-transformed result."""
    fw = Runner.callback_framework
    tool = _make_tool()
    received = []

    @fw.on_transform(ToolCallEvents.TOOL_INVOKE_OUTPUT)
    async def t_out(result):
        return result + "-out"

    @fw.on(ToolCallEvents.TOOL_INVOKE_OUTPUT)
    async def record_out(result):
        received.append(result)

    result = await tool.invoke("hello")
    assert result == "hello-out"
    assert received == ["hello-out"]


@pytest.mark.asyncio
async def test_tool_invoke_both_transforms_applied():
    """Both input and output transforms apply; trigger callbacks see the transformed values."""
    fw = Runner.callback_framework
    tool = _make_tool()
    in_received = []
    out_received = []

    @fw.on_transform(ToolCallEvents.TOOL_INVOKE_INPUT)
    async def t_in(*args, **kwargs):
        return (("x",), {})

    @fw.on_transform(ToolCallEvents.TOOL_INVOKE_OUTPUT)
    async def t_out(result):
        return result + "!"

    @fw.on(ToolCallEvents.TOOL_INVOKE_INPUT)
    async def record_in(*args, **kwargs):
        in_received.append(args[0] if args else None)

    @fw.on(ToolCallEvents.TOOL_INVOKE_OUTPUT)
    async def record_out(result):
        out_received.append(result)

    result = await tool.invoke("original")
    assert result == "x!"
    assert in_received == ["x"]
    assert out_received == ["x!"]


# === Tool.stream stacking ===


@pytest.mark.asyncio
async def test_tool_stream_trigger_on_call_with_transformed_input():
    """Normal INPUT callback on stream receives transformed input."""
    fw = Runner.callback_framework
    tool = _make_tool()
    received = []

    @fw.on_transform(ToolCallEvents.TOOL_STREAM_INPUT)
    async def t_in(*args, **kwargs):
        return (("stream-transformed",), {})

    @fw.on(ToolCallEvents.TOOL_STREAM_INPUT)
    async def record_in(*args, **kwargs):
        received.append(args[0] if args else None)

    items = []
    async for item in tool.stream("original"):
        items.append(item)

    assert received == ["stream-transformed"]
    assert items == ["stream-transformed"]


@pytest.mark.asyncio
async def test_tool_stream_emits_stream_per_item_with_transformed_output():
    """Normal OUTPUT callback fires per item with the transformed value."""
    fw = Runner.callback_framework
    tool = _make_tool()
    received = []

    @fw.on_transform(ToolCallEvents.TOOL_STREAM_OUTPUT)
    async def t_out(result):
        return result + "-item"

    @fw.on(ToolCallEvents.TOOL_STREAM_OUTPUT)
    async def record_out(result):
        received.append(result)

    items = []
    async for item in tool.stream("chunk"):
        items.append(item)

    assert items == ["chunk-item"]
    assert received == ["chunk-item"]


# === Workflow stacking ===


@pytest.mark.asyncio
async def test_workflow_invoke_callbacks():
    """Workflow invoke: transform and normal callbacks both fire with correct values."""
    fw = Runner.callback_framework
    wf = _make_workflow()
    out_received = []

    @fw.on_transform(WorkflowEvents.WORKFLOW_INVOKE_OUTPUT)
    async def t_out(result):
        return result + "-wf"

    @fw.on(WorkflowEvents.WORKFLOW_INVOKE_OUTPUT)
    async def record_out(result):
        out_received.append(result)

    result = await wf.invoke("data")
    assert result == "data-wf"
    assert out_received == ["data-wf"]


@pytest.mark.asyncio
async def test_workflow_stream_callbacks():
    """Workflow stream: transform and normal callbacks both fire with correct values."""
    fw = Runner.callback_framework
    wf = _make_workflow()
    received = []

    @fw.on_transform(WorkflowEvents.WORKFLOW_STREAM_OUTPUT)
    async def t_out(result):
        return result + "-wf"

    @fw.on(WorkflowEvents.WORKFLOW_STREAM_OUTPUT)
    async def record_out(result):
        received.append(result)

    items = []
    async for item in wf.stream("data"):
        items.append(item)

    assert items == ["data-wf"]
    assert received == ["data-wf"]


# === Model stacking ===


@pytest.mark.asyncio
async def test_model_invoke_callbacks():
    """Model invoke: transform and normal callbacks both fire with correct values."""
    fw = Runner.callback_framework
    model = _make_model()
    out_received = []

    @fw.on_transform(LLMCallEvents.LLM_INVOKE_OUTPUT)
    async def t_out(result):
        return [*result, "extra"]

    @fw.on(LLMCallEvents.LLM_INVOKE_OUTPUT)
    async def record_out(result):
        out_received.append(result)

    result = await model.invoke(["msg"])
    assert result == ["msg", "extra"]
    assert out_received == [["msg", "extra"]]


@pytest.mark.asyncio
async def test_model_stream_callbacks():
    """Model stream: transform and normal callbacks both fire per item with correct values."""
    fw = Runner.callback_framework
    model = _make_model()
    received = []

    @fw.on_transform(LLMCallEvents.LLM_STREAM_OUTPUT)
    async def t_out(result):
        return str(result) + "-chunk"

    @fw.on(LLMCallEvents.LLM_STREAM_OUTPUT)
    async def record_out(result):
        received.append(result)

    items = []
    async for item in model.stream(["msg"]):
        items.append(item)

    assert items == ["['msg']-chunk"]
    assert received == ["['msg']-chunk"]


# === Ordering: transform fires before trigger ===


@pytest.mark.asyncio
async def test_trigger_and_transform_fire_in_order():
    """transform callback fires before trigger callback; trigger sees the transformed value."""
    fw = Runner.callback_framework
    tool = _make_tool()
    order = []

    @fw.on_transform(ToolCallEvents.TOOL_INVOKE_OUTPUT)
    async def t_out(result):
        order.append(("transform", result))
        return result + "-T"

    @fw.on(ToolCallEvents.TOOL_INVOKE_OUTPUT)
    async def normal_out(result):
        order.append(("trigger", result))

    await tool.invoke("v")
    assert order[0] == ("transform", "v")
    assert order[1] == ("trigger", "v-T")
