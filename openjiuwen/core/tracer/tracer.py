#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import uuid

from openjiuwen.core.tracer.handler import TraceAgentHandler, TraceWorkflowHandler, TracerHandlerName
from openjiuwen.core.tracer.span import SpanManager


class Tracer:
    def __init__(self):
        self._trace_id = str(uuid.uuid4())
        self.tracer_agent_span_manager = SpanManager(self._trace_id)
        self.tracer_workflow_span_manager_dict = {}
        self._callback_manager = None
        self._stream_writer_manager = None

    def init(self, stream_writer_manager, callback_manager):
        trace_agent_handler = TraceAgentHandler(callback_manager, stream_writer_manager,
                                                self.tracer_agent_span_manager)
        parent_tracer_workflow_span_manager = SpanManager(self._trace_id)
        trace_workflow_handler = TraceWorkflowHandler(callback_manager, stream_writer_manager,
                                                      parent_tracer_workflow_span_manager)
        self.tracer_workflow_span_manager_dict[""] = parent_tracer_workflow_span_manager
        callback_manager.register_handler({TracerHandlerName.TRACE_AGENT.value: trace_agent_handler})
        callback_manager.register_handler({TracerHandlerName.TRACER_WORKFLOW.value: trace_workflow_handler})
        self._callback_manager = callback_manager
        self._stream_writer_manager = stream_writer_manager

    def register_workflow_span_manager(self, parent_node_id: str):
        tracer_workflow_span_manager = SpanManager(self._trace_id, parent_node_id=parent_node_id)
        self.tracer_workflow_span_manager_dict[parent_node_id] = tracer_workflow_span_manager
        trace_workflow_handler = TraceWorkflowHandler(self._callback_manager, self._stream_writer_manager,
                                                      tracer_workflow_span_manager)
        self._callback_manager.register_handler(
            {TracerHandlerName.TRACER_WORKFLOW.value + "." + parent_node_id: trace_workflow_handler})

    def get_workflow_span(self, invoke_id: str, parent_node_id: str):
        workflow_span_manager = self.tracer_workflow_span_manager_dict.get(parent_node_id, None)
        if workflow_span_manager is None:
            return None
        return workflow_span_manager.get_span(invoke_id)

    async def trigger(self, handler_class_name: str, event_name: str, **kwargs):
        parent_node_id = kwargs.get("parent_node_id", None)
        if parent_node_id is not None:
            handler_class_name += "." + parent_node_id if parent_node_id != "" else ""
        await self._callback_manager.trigger(handler_class_name, event_name, **kwargs)

    def sync_trigger(self, handler_class_name: str, event_name: str, **kwargs):
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(self.trigger(handler_class_name, event_name, **kwargs), loop)
        else:
            loop.run_until_complete(self.trigger(handler_class_name, event_name, **kwargs))

    def pop_workflow_span(self, invoke_id: str, parent_node_id: str):
        if parent_node_id not in self.tracer_workflow_span_manager_dict:
            return
        self.tracer_workflow_span_manager_dict.get(parent_node_id).pop_span(invoke_id)
