# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

import openjiuwen.core.workflow as workflow_api
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.graph.vertex import Vertex
from openjiuwen.core.session.tracer import TracerWorkflowUtils


def _build_workflow_without_loop(close_previous_loop):
    if close_previous_loop:

        async def finish():
            pass

        asyncio.run(finish())
    with pytest.raises(RuntimeError, match="no current event loop"):
        asyncio.get_event_loop()
    workflow = workflow_api.Workflow()
    workflow.set_start_comp("start", workflow_api.Start(), inputs_schema={"value": "${value}"})
    workflow.set_end_comp("end", workflow_api.End(), inputs_schema={"value": "${start.value}"})
    workflow.add_connection("start", "end")
    return workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("close_previous_loop", [False, True])
async def test_workflow_built_in_worker_runs_repeatedly(close_previous_loop):
    workflow = await anyio.to_thread.run_sync(_build_workflow_without_loop, close_previous_loop)

    for value in (1, 2):
        result = await workflow.invoke({"value": value}, workflow_api.create_workflow_session())
        assert result.result == {"output": {"value": value}}


@pytest.mark.asyncio
async def test_vertex_built_without_loop_can_reset_before_streaming():
    vertex = await anyio.to_thread.run_sync(Vertex, "unused")

    await vertex.reset()
    await vertex.reset()

    assert vertex.is_done()
    assert vertex._stream_done is None


@pytest.mark.asyncio
@pytest.mark.parametrize("initialized", [False, True])
async def test_stream_completion_uses_execution_loop_and_can_reset(initialized):
    vertex = Vertex("stream")
    if initialized:
        vertex._session = MagicMock()
        vertex._session.tracer.return_value = None
        vertex._component_ability = []

    previous_completion = None
    for _ in range(2):
        errors = []
        await vertex.stream_call(asyncio.Event(), errors.append)
        completion = vertex._stream_done
        assert completion is not previous_completion
        assert completion.get_loop() is asyncio.get_running_loop()
        if initialized:
            assert completion.result() is True
            assert errors == []
        else:
            assert len(errors) == 1
            assert isinstance(errors[0], BaseError)
            assert errors[0].code == StatusCode.GRAPH_VERTEX_STREAM_CALL_ERROR.code
            assert completion.result() is errors[0]
        await vertex.reset()
        assert vertex._stream_done is None
        assert vertex.is_done()
        previous_completion = completion


@pytest.mark.asyncio
async def test_reset_cancels_pending_stream_completion():
    vertex = Vertex("stream")
    completion = asyncio.get_running_loop().create_future()
    vertex._stream_done = completion

    await vertex.reset()

    assert completion.cancelled()
    assert vertex._stream_done is None


@pytest.mark.asyncio
@pytest.mark.parametrize("has_stream_call", [False, True])
async def test_input_tracing_before_stream_after_completion_and_after_reset(monkeypatch, has_stream_call):
    executable = MagicMock()
    executable.skip_trace.return_value = False
    vertex = Vertex("trace", executable)
    vertex._session = MagicMock()
    vertex._component_ability = []
    vertex._has_stream_call = has_stream_call
    vertex._has_call = True
    trace_inputs = AsyncMock()
    monkeypatch.setattr(TracerWorkflowUtils, "trace_component_inputs", trace_inputs)
    monkeypatch.setattr(TracerWorkflowUtils, "trace_component_stream_input", AsyncMock())

    await vertex.__trace_component_inputs__({"value": 1})
    assert trace_inputs.call_args.kwargs["send"] is (not has_stream_call)

    await vertex.stream_call(asyncio.Event(), MagicMock())
    await vertex.__trace_component_inputs__({"value": 2})
    assert trace_inputs.call_args.kwargs["send"] is True

    await vertex.reset()
    await vertex.__trace_component_inputs__({"value": 3})
    assert trace_inputs.call_args.kwargs["send"] is (not has_stream_call)
