# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import copy
import json
from abc import abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any

from dateutil.tz import tzlocal

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runtime.resources_manager.callback_manager import BaseHandler, trigger_event
from openjiuwen.core.stream.manager import StreamWriterManager
from openjiuwen.core.tracer.data import InvokeType, NodeStatus
from openjiuwen.core.tracer.span import Span, TraceAgentSpan, TraceWorkflowSpan
from openjiuwen.core.tracer.span import SpanManager
from openjiuwen.graph.pregel import GraphInterrupt


class TracerHandlerName(Enum):
    """
    Trigger handler name.
    """
    TRACE_AGENT = "tracer_agent"
    TRACER_WORKFLOW = "tracer_workflow"


class TraceBaseHandler(BaseHandler):
    def __init__(self, owner, stream_writer_manager: StreamWriterManager, spanManager: SpanManager):
        super().__init__(owner)
        self._stream_writer = stream_writer_manager.get_trace_writer()
        self._span_manager = spanManager

    async def emit_stream_writer(self, data):
        await self._emit_stream_writer(data)

    @abstractmethod
    def _format_data(self, span: Span) -> dict:
        return {"type": self.event_name(), "payload": span}

    async def _emit_stream_writer(self, span):
        if self._stream_writer is None:
            return
        await self._stream_writer.write(self._format_data(span))

    async def _send_data(self, span, exclude=None):
        if exclude:
            clean_dict = span.model_dump(exclude=exclude)
            await self.emit_stream_writer(type(span).model_validate(clean_dict))
            return
        await self.emit_stream_writer(copy.deepcopy(span))

    def _get_elapsed_time(self, start_time: datetime, end_time: datetime) -> str:
        """get elapsed time"""
        elapsed_time = end_time - start_time
        ms = elapsed_time.total_seconds() * 1000
        if ms < 1000:
            return f"{ms:.0f}ms"
        return f"{(ms / 1000):.2f}s"


class TraceAgentHandler(TraceBaseHandler):
    def __init__(self, owner, stream_writer_manager, spanManager):
        super().__init__(owner, stream_writer_manager, spanManager)

    def event_name(self):
        return TracerHandlerName.TRACE_AGENT.value

    def _format_data(self, span: TraceAgentSpan) -> dict:
        return {"type": self.event_name(), "payload": span.model_dump(by_alias=True)}

    def _get_tracer_agent_span(self, invoke_id: str) -> TraceAgentSpan:
        span = self._span_manager.get_span(invoke_id)
        if span is not None:
            return span
        return self._span_manager.create_agent_span(self._span_manager.last_span)

    def _update_start_trace_data(self, span: TraceAgentSpan, invoke_type: str, inputs: Any, instance_info: dict,
                                 **kwargs):
        try:
            meta_data = json.loads(
                json.dumps(instance_info, ensure_ascii=False,
                           default=lambda _obj: f"<<no-serializable: {type(_obj).__qualname__}>>")
            )
        except json.decoder.JSONDecodeError as err:
            logger.error("meta_data process error")
            raise ValueError(f"meta_data error: Decoder error") from err

        update_data = {
            "start_time": datetime.now(tz=tzlocal()).replace(tzinfo=None),
            "invoke_type": invoke_type,
            "inputs": inputs,
            "instance_info": instance_info,
            "name": instance_info["class_name"],
            "meta_data": meta_data
        }
        self._span_manager.update_span(span, update_data)

    def _update_end_trace_data(self, span: TraceAgentSpan, outputs, **kwargs):
        end_time = datetime.now(tz=tzlocal()).replace(tzinfo=None)
        elapsed_time = self._get_elapsed_time(span.start_time, end_time) if span.start_time else None
        update_data = {
            "end_time": end_time,
            "outputs": outputs
        }
        if elapsed_time is not None:
            update_data["elapsed_time"] = elapsed_time
        self._span_manager.update_span(span, update_data)

    def _update_error_trace_data(self, span: TraceAgentSpan, error, **kwargs):
        end_time = datetime.now(tz=tzlocal()).replace(tzinfo=None)
        if isinstance(error, JiuWenBaseException):
            error_info = {"error_code": error.error_code, "message": error.message}
        else:
            error_info = {"error_code": StatusCode.RUNTIME_TRACE_AGENT_UNDEFINED_FAILED.code,
                          "message": str(error)}
        elapsed_time = self._get_elapsed_time(span.start_time, end_time) if span.start_time else None
        update_data = {
            "end_time": end_time,
            "error": error_info
        }
        if elapsed_time is not None:
            update_data["elapsed_time"] = elapsed_time
        self._span_manager.update_span(span, update_data)

    @trigger_event
    async def on_chain_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.CHAIN.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_chain_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_chain_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_llm_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.LLM.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_llm_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_llm_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_prompt_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.PROMPT.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_prompt_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_prompt_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_plugin_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.PLUGIN.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_plugin_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_plugin_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_retriever_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.RETRIEVER.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_retriever_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_retriever_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_evaluator_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.EVALUATOR.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_evaluator_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_evaluator_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_workflow_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.WORKFLOW.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_workflow_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    @trigger_event
    async def on_workflow_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)


class TraceWorkflowHandler(TraceBaseHandler):
    def __init__(self, owner, stream_writer_manager, spanManager):
        super().__init__(owner, stream_writer_manager, spanManager)

    def event_name(self) -> str:
        return TracerHandlerName.TRACER_WORKFLOW.value

    def _format_data(self, span: TraceWorkflowSpan) -> dict:
        if span.status != NodeStatus.INTERRUPTED.value:
            span.status = self._get_node_status(span)
        result = span.model_dump(exclude_none=True, by_alias=True, exclude={"child_invokes_id", "llm_invoke_data"})
        return {"type": self.event_name(),
                "payload": result}

    def _get_node_status(self, span: TraceWorkflowSpan) -> str:
        if span.error:
            return NodeStatus.ERROR.value
        if span.on_invoke_data:
            return NodeStatus.RUNNING.value if not span.end_time else NodeStatus.FINISH.value
        if span.end_time:
            return NodeStatus.FINISH.value
        return NodeStatus.START.value

    def _get_tracer_workflow_span(self, invoke_id: str) -> TraceWorkflowSpan:
        span = self._span_manager.get_span(invoke_id)
        if span is not None:
            return span
        return self._span_manager.create_workflow_span(invoke_id, self._span_manager.last_span)

    @trigger_event
    async def on_call_start(self, invoke_id: str, metadata: dict = None, inputs: Any = None,
                            need_send: bool = False, source_ids: list = None,
                            **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)
        update_data = {
            "start_time": datetime.now(tz=tzlocal()).replace(tzinfo=None),
            "invoke_type": type,
            "on_invoke_data": [],
            "inputs": inputs,
            "outputs": None,
            "stream_outputs": [],
            "source_ids": source_ids,
            **metadata
        }
        self._span_manager.update_span(span, update_data)
        if need_send:
            await self._send_data(span)

    @trigger_event
    async def on_pre_invoke(self, invoke_id: str, inputs: Any, component_metadata: dict, need_send: bool = False,
                            **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)

        update_data = {
            "inputs": inputs,
            **component_metadata
        }
        self._span_manager.update_span(span, update_data)
        if need_send:
            await self._send_data(span, exclude={"outputs", "stream_outputs"})

    @trigger_event
    async def on_pre_stream(self, invoke_id: str, chunk, need_send: bool = False, **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)
        if chunk and isinstance(chunk, dict):
            span.append_stream_inputs(chunk)
        if need_send:
            await self._send_data(span, exclude={"outputs", "stream_outputs"})

    @trigger_event
    async def on_invoke(self, invoke_id: str, on_invoke_data: dict = None, exception: Exception = None, **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)
        update_data = {}
        end_time = datetime.now(tz=tzlocal()).replace(tzinfo=None)
        if exception is not None:
            if isinstance(exception, JiuWenBaseException):
                span.error = {"error_code": exception.error_code, "message": exception.message}
            elif isinstance(exception, GraphInterrupt):
                span.status = NodeStatus.INTERRUPTED.value
            else:
                span.error = {"error_code": StatusCode.WORKFLOW_EXECUTE_INNER_ERROR.code,
                              "message": StatusCode.WORKFLOW_EXECUTE_INNER_ERROR.errmsg.format(
                                  error=str(exception))}
            if on_invoke_data:
                span.on_invoke_data.append(on_invoke_data)
            update_data = {
                "end_time": end_time
            }
            elapsed_time = self._get_elapsed_time(span.start_time, end_time) if span.start_time else None
            if elapsed_time is not None:
                update_data["elapsed_time"] = elapsed_time
        else:
            if not isinstance(span.on_invoke_data, list):
                span.on_invoke_data = []
            span.on_invoke_data.append(on_invoke_data)
        self._span_manager.update_span(span, update_data)

        await self._send_data(span)
        if exception and span.component_type == "LLM":
            self._span_manager.update_span(span, {})

    @trigger_event
    async def on_post_stream(self, invoke_id: str, chunk, **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)
        span.append_stream_output(chunk)

    @trigger_event
    async def on_post_invoke(self, invoke_id: str, outputs, inputs=None, **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)
        update_data = {
            "outputs": outputs,
        }
        if inputs and span.component_type in ["End", "Message"]:
            span.inputs = inputs
        self._span_manager.update_span(span, update_data)

    @trigger_event
    async def on_call_done(self, invoke_id, outputs: Any = None, **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)
        end_time = datetime.now(tz=tzlocal()).replace(tzinfo=None)
        elapsed_time = self._get_elapsed_time(span.start_time, end_time) if span.start_time else None
        update_data = {"end_time": end_time, "outputs": outputs} if outputs is not None else {"end_time": end_time}
        if elapsed_time is not None:
            update_data["elapsed_time"] = elapsed_time
        self._span_manager.update_span(span, update_data)
        await self._send_data(span)
        if span.component_type == "End" and span.end_time:
            self._span_manager.update_span(span, {})
