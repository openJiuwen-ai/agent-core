# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import openjiuwen.core.workflow
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, ExecutionError, build_error
from openjiuwen.core.graph.pregel import GraphInterrupt
from openjiuwen.core.graph.vertex import Vertex
from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.internal.workflow import WorkflowSession
from openjiuwen.core.session.state.workflow_state import InMemoryState
from openjiuwen.core.session.tracer.workflow_tracer import TracerWorkflowUtils
from openjiuwen.core.workflow.components.base import ComponentAbility


class _StubExecutable:
    def __init__(self):
        self.invoke_fn = AsyncMock(return_value=None)
        self._skip_trace = False
        self._graph_invoker = False
        self._post_commit = True

    async def on_invoke(self, inputs, session=None, **kwargs):
        return await self.invoke_fn(inputs, session=session, **kwargs)

    def skip_trace(self):
        return self._skip_trace

    def graph_invoker(self):
        return self._graph_invoker

    def post_commit(self):
        return self._post_commit

    def component_type(self):
        return ""


def _make_session(node_id: str, max_retries: int = 0):
    session = WorkflowSession(workflow_id="test_wf")
    config = Config()
    from openjiuwen.core.workflow.workflow_config import WorkflowConfig, WorkflowSpec, NodeSpec, CompIOConfig
    from openjiuwen.core.workflow.base import WorkflowCard

    spec = WorkflowSpec(
        comp_configs={
            node_id: NodeSpec(
                io_configs=CompIOConfig(),
                abilities=[ComponentAbility.INVOKE],
                max_retries=max_retries,
            )
        }
    )
    wf_config = WorkflowConfig(card=WorkflowCard(name="test"), spec=spec)
    config.add_workflow_config("test_wf", wf_config)
    session._config = config
    session._state = InMemoryState()
    return session


def _make_vertex(node_id: str = "test_node", max_retries: int = 0):
    executable = _StubExecutable()
    vertex = Vertex(node_id, executable=executable)
    session = _make_session(node_id, max_retries)
    vertex.init(session)
    return vertex, executable, session


def _patch_tracer_utils():
    all_async_methods = [
        "trace_component_begin",
        "trace_component_inputs",
        "trace_component_stream_input",
        "trace_component_outputs",
        "trace_component_stream_output",
        "trace_component_done",
        "trace_workflow_start",
        "trace_workflow_done",
        "trace_component_interactive_inputs",
        "trace",
        "trace_error",
    ]
    return [patch.object(TracerWorkflowUtils, m, new_callable=AsyncMock) for m in all_async_methods]


@pytest.mark.asyncio
async def test_call_succeeds_without_retry():
    vertex, executable, _ = _make_vertex(max_retries=0)
    executable.invoke_fn = AsyncMock(return_value={"result": "ok"})

    await vertex.call()

    assert executable.invoke_fn.call_count == 1


@pytest.mark.asyncio
async def test_call_raises_when_not_initialized():
    vertex = Vertex("uninit_node")
    with pytest.raises(BaseError) as exc_info:
        await vertex.call()
    assert exc_info.value.code == StatusCode.GRAPH_VERTEX_EXECUTION_ERROR.code


@pytest.mark.asyncio
async def test_call_retries_on_exception_and_succeeds():
    vertex, executable, _ = _make_vertex(max_retries=2)

    call_count = 0

    async def _flaky_invoke(inputs, session=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("transient failure")
        return {"result": "ok"}

    executable.invoke_fn = _flaky_invoke

    await vertex.call()

    assert call_count == 3


@pytest.mark.asyncio
async def test_call_retries_exhausted_raises():
    vertex, executable, _ = _make_vertex(max_retries=1)

    executable.invoke_fn = AsyncMock(side_effect=RuntimeError("persistent failure"))

    with pytest.raises(BaseError) as exc_info:
        await vertex.call()
    assert exc_info.value.code == StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR.code
    assert executable.invoke_fn.call_count == 2


@pytest.mark.asyncio
async def test_call_no_retry_when_max_retries_zero():
    vertex, executable, _ = _make_vertex(max_retries=0)

    executable.invoke_fn = AsyncMock(side_effect=RuntimeError("fail"))

    with pytest.raises(BaseError) as exc_info:
        await vertex.call()
    assert executable.invoke_fn.call_count == 1


@pytest.mark.asyncio
async def test_call_graph_interrupt_not_retried():
    vertex, executable, _ = _make_vertex(max_retries=3)

    executable.invoke_fn = AsyncMock(side_effect=GraphInterrupt())

    with pytest.raises(GraphInterrupt):
        await vertex.call()

    assert executable.invoke_fn.call_count == 1


@pytest.mark.asyncio
async def test_call_base_error_retried():
    vertex, executable, _ = _make_vertex(max_retries=2)

    call_count = 0

    async def _failing_invoke(inputs, session=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ExecutionError(StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR, reason="retryable")
        return {"result": "ok"}

    executable.invoke_fn = _failing_invoke

    await vertex.call()

    assert call_count == 2


@pytest.mark.asyncio
async def test_inner_error_traced_on_retry_with_runtime_error():
    patches = _patch_tracer_utils()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
        mock_trace = TracerWorkflowUtils.trace
        mock_trace_error = TracerWorkflowUtils.trace_error

        vertex, executable, session = _make_vertex(max_retries=2)
        session.set_tracer(MagicMock())

        call_count = 0

        async def _flaky_invoke(inputs, session=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient failure")
            return {"result": "ok"}

        executable.invoke_fn = _flaky_invoke

        await vertex.call()

        trace_calls = mock_trace.call_args_list
        inner_error_calls = [c for c in trace_calls
                             if c.kwargs.get("data", {}).get("inner_error") is not None]
        assert len(inner_error_calls) == 2

        for call in inner_error_calls:
            data = call.kwargs["data"]
            assert data["inner_error"]["error_code"] == StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR.code
            assert "transient failure" in data["inner_error"]["message"]
            assert "current_time" in data


@pytest.mark.asyncio
async def test_inner_error_traced_on_retry_with_base_error():
    patches = _patch_tracer_utils()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
        mock_trace = TracerWorkflowUtils.trace

        vertex, executable, session = _make_vertex(max_retries=2)
        session.set_tracer(MagicMock())

        call_count = 0

        async def _flaky_invoke(inputs, session=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ExecutionError(StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR, reason="retryable")
            return {"result": "ok"}

        executable.invoke_fn = _flaky_invoke

        await vertex.call()

        trace_calls = mock_trace.call_args_list
        inner_error_calls = [c for c in trace_calls
                             if c.kwargs.get("data", {}).get("inner_error") is not None]
        assert len(inner_error_calls) == 1

        data = inner_error_calls[0].kwargs["data"]
        assert data["inner_error"]["error_code"] == StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR.code
        assert "retryable" in data["inner_error"]["message"]


@pytest.mark.asyncio
@patch("openjiuwen.core.graph.vertex.trigger", new_callable=AsyncMock)
async def test_trace_error_no_inner_error_when_max_retries_gt_zero(mock_trigger):
    patches = _patch_tracer_utils()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
        mock_trace_error = TracerWorkflowUtils.trace_error

        executable = _StubExecutable()
        executable._post_commit = False
        executable.invoke_fn = AsyncMock(side_effect=RuntimeError("persistent failure"))
        vertex = Vertex("test_node", executable=executable)
        session = _make_session("test_node", max_retries=1)
        session.set_tracer(MagicMock())
        vertex.init(session)

        with pytest.raises(BaseError):
            await vertex({"source_node_id": []}, None)

        mock_trace_error.assert_called_once()
        call_kwargs = mock_trace_error.call_args.kwargs
        assert call_kwargs.get("inner_error") is None


@pytest.mark.asyncio
@patch("openjiuwen.core.graph.vertex.trigger", new_callable=AsyncMock)
async def test_trace_error_no_inner_error_when_max_retries_zero(mock_trigger):
    patches = _patch_tracer_utils()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
        mock_trace_error = TracerWorkflowUtils.trace_error

        executable = _StubExecutable()
        executable._post_commit = False
        executable.invoke_fn = AsyncMock(side_effect=RuntimeError("fail"))
        vertex = Vertex("test_node", executable=executable)
        session = _make_session("test_node", max_retries=0)
        session.set_tracer(MagicMock())
        vertex.init(session)

        with pytest.raises(BaseError):
            await vertex({"source_node_id": []}, None)

        mock_trace_error.assert_called_once()
        call_kwargs = mock_trace_error.call_args.kwargs
        assert call_kwargs.get("inner_error") is None


@pytest.mark.asyncio
async def test_no_inner_error_traced_when_skip_trace():
    patches = _patch_tracer_utils()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
        mock_trace = TracerWorkflowUtils.trace
        mock_trace_error = TracerWorkflowUtils.trace_error

        vertex, executable, session = _make_vertex(max_retries=2)
        session.set_tracer(MagicMock())
        executable._skip_trace = True

        call_count = 0

        async def _flaky_invoke(inputs, session=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient failure")
            return {"result": "ok"}

        executable.invoke_fn = _flaky_invoke

        await vertex.call()

        mock_trace.assert_not_called()
        mock_trace_error.assert_not_called()


@pytest.mark.asyncio
async def test_inner_error_current_time_recorded_without_end_time():
    patches = _patch_tracer_utils()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
        mock_trace = TracerWorkflowUtils.trace

        vertex, executable, session = _make_vertex(max_retries=2)
        session.set_tracer(MagicMock())

        call_count = 0

        async def _flaky_invoke(inputs, session=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient failure")
            return {"result": "ok"}

        executable.invoke_fn = _flaky_invoke

        await vertex.call()

        trace_calls = mock_trace.call_args_list
        inner_error_calls = [c for c in trace_calls
                             if c.kwargs.get("data", {}).get("inner_error") is not None]
        assert len(inner_error_calls) > 0

        for call in inner_error_calls:
            data = call.kwargs["data"]
            assert "current_time" in data
            assert "end_time" not in data


@pytest.mark.asyncio
@patch("openjiuwen.core.graph.vertex.trigger", new_callable=AsyncMock)
async def test_trace_error_no_inner_error_with_base_error(mock_trigger):
    patches = _patch_tracer_utils()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
        mock_trace_error = TracerWorkflowUtils.trace_error

        executable = _StubExecutable()
        executable._post_commit = False
        executable.invoke_fn = AsyncMock(
            side_effect=ExecutionError(StatusCode.WORKFLOW_COMPONENT_EXECUTION_ERROR, reason="base error")
        )
        vertex = Vertex("test_node", executable=executable)
        session = _make_session("test_node", max_retries=1)
        session.set_tracer(MagicMock())
        vertex.init(session)

        with pytest.raises(BaseError):
            await vertex({"source_node_id": []}, None)

        mock_trace_error.assert_called_once()
        assert mock_trace_error.call_args.kwargs.get("inner_error") is None


@pytest.mark.asyncio
async def test_graph_interrupt_no_inner_error_traced():
    patches = _patch_tracer_utils()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
        mock_trace = TracerWorkflowUtils.trace
        mock_trace_error = TracerWorkflowUtils.trace_error

        vertex, executable, session = _make_vertex(max_retries=3)
        session.set_tracer(MagicMock())

        executable.invoke_fn = AsyncMock(side_effect=GraphInterrupt())

        with pytest.raises(GraphInterrupt):
            await vertex.call()

        inner_error_calls = [c for c in mock_trace.call_args_list
                             if c.kwargs.get("data", {}).get("inner_error") is not None]
        assert len(inner_error_calls) == 0
        mock_trace_error.assert_not_called()


@pytest.mark.asyncio
@patch("openjiuwen.core.graph.vertex.trigger", new_callable=AsyncMock)
async def test_trace_error_not_called_when_retry_succeeds(mock_trigger):
    patches = _patch_tracer_utils()
    with patches[0], patches[1], patches[2], patches[3], patches[4], \
         patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
        mock_trace_error = TracerWorkflowUtils.trace_error

        executable = _StubExecutable()
        executable._post_commit = False
        call_count = 0

        async def _flaky_invoke(inputs, session=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("transient failure")
            return {"result": "ok"}

        executable.invoke_fn = _flaky_invoke
        vertex = Vertex("test_node", executable=executable)
        session = _make_session("test_node", max_retries=2)
        session.set_tracer(MagicMock())
        vertex.init(session)

        await vertex({"source_node_id": []}, None)

        mock_trace_error.assert_not_called()
