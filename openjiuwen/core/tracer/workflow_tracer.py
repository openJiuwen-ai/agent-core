# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional

from openjiuwen.core.common.constants.constant import LOOP_ID, INDEX
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.runtime.utils import NESTED_PATH_SPLIT
from openjiuwen.core.tracer.handler import TracerHandlerName


class TracerWorkflowUtils:
    @staticmethod
    def _get_workflow_metadata(runtime) -> dict:
        executable_id = runtime._workflow_id
        workflow_config = runtime.config().get_workflow_config(executable_id)
        workflow_metadata = workflow_config.metadata if workflow_config else None
        return {
            "workflow_id": executable_id,
            "workflow_version": workflow_metadata.version if workflow_metadata else '',
            "workflow_name": workflow_metadata.name if workflow_metadata else '',
        }

    @staticmethod
    def _get_component_metadata(runtime) -> dict:
        executable_id = runtime.executable_id()
        state = runtime.state()
        component_metadata = {
            "component_id": runtime.node_id(),
            "component_name": runtime.node_id(),
            "component_type": runtime.node_type(),
            "workflow_id": runtime.workflow_id()
        }
        loop_id = state.get_global(LOOP_ID)
        if loop_id is None:
            return component_metadata

        index = state.get_global(loop_id + NESTED_PATH_SPLIT + INDEX)
        component_metadata.update({
            "loop_node_id": loop_id,
            "loop_index": index
        })
        return component_metadata

    @staticmethod
    async def trace_workflow_start(runtime, inputs: Optional[dict]):
        tracer = runtime.tracer()
        if tracer is None:
            return
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, event_name="on_call_start",
                             invoke_id=runtime._workflow_id,
                             parent_node_id='',
                             metadata=TracerWorkflowUtils._get_workflow_metadata(runtime),
                             inputs=inputs,
                             need_send=True)

    @staticmethod
    async def trace_component_begin(runtime, source_ids: list = None):
        tracer = runtime.tracer()
        if tracer is None:
            return
        executable_id = runtime.executable_id()
        parent_id = runtime.parent_id()
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_call_start",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             source_ids=source_ids,
                             metadata=TracerWorkflowUtils._get_component_metadata(runtime))

    @staticmethod
    async def trace_component_inputs(runtime, inputs: Optional[dict], send: bool = True):
        tracer = runtime.tracer()
        if tracer is None:
            return
        executable_id = runtime.executable_id()
        parent_id = runtime.parent_id()
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_pre_invoke",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             inputs=inputs,
                             need_send=send,
                             component_metadata=TracerWorkflowUtils._get_component_metadata(runtime))

    @staticmethod
    async def trace_component_stream_input(runtime, chunk, send: bool = True):
        tracer = runtime.tracer()
        if tracer is None:
            return
        executable_id = runtime.executable_id()
        parent_id = runtime.parent_id()
        if isinstance(chunk, str):
            return
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_pre_stream",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             need_send=send,
                             chunk=dict(chunk))

    @staticmethod
    async def trace_component_outputs(runtime, outputs: Optional[dict]):
        tracer = runtime.tracer()
        if tracer is None:
            return
        executable_id = runtime.executable_id()
        parent_id = runtime.parent_id()
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_post_invoke",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             outputs=outputs)

    @staticmethod
    async def trace_component_stream_output(runtime, chunk):
        tracer = runtime.tracer()
        if tracer is None:
            return
        executable_id = runtime.executable_id()
        parent_id = runtime.parent_id()
        if isinstance(chunk, str):
            return
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_post_stream",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             chunk=dict(chunk))

    @staticmethod
    async def trace_workflow_done(runtime, outputs: Optional[dict]):
        tracer = runtime.tracer()
        if tracer is None:
            return
        executable_id = runtime._workflow_id
        parent_id = ""
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_call_done",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             outputs=outputs,
                             metadata=TracerWorkflowUtils._get_workflow_metadata(runtime))

    @staticmethod
    async def trace_component_done(runtime):
        tracer = runtime.tracer()
        if tracer is None:
            return
        executable_id = runtime.executable_id()
        parent_id = runtime.parent_id()
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_call_done",
                             invoke_id=executable_id,
                             parent_node_id=parent_id)
        state = runtime.state()
        loop_id = state.get_global(LOOP_ID)
        if loop_id is None:
            return
        runtime.tracer().pop_workflow_span(executable_id, runtime.parent_id())

    @staticmethod
    async def trace(runtime, data: dict = None):
        tracer = runtime.tracer()
        if tracer is None:
            return
        invoke_id = runtime.executable_id()
        parent_id = runtime.parent_id()
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_invoke",
                             invoke_id=invoke_id,
                             parent_node_id=parent_id,
                             on_invoke_data=data)

    @staticmethod
    async def trace_error(runtime, error: Exception):
        tracer = runtime.tracer()
        if tracer is None:
            return
        if error is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_TRACE_ERROR_FAILED.code,
                                      StatusCode.RUNTIME_TRACE_ERROR_FAILED.errmsg.format(reason="error is None"))
        invoke_id = runtime.executable_id()
        parent_id = runtime.parent_id()
        await runtime.tracer().trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_invoke",
                                       invoke_id=invoke_id,
                                       parent_node_id=parent_id,
                                       exception=error)
