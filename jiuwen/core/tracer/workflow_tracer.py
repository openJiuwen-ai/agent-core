#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional

from jiuwen.core.common.constants.constant import LOOP_ID, INDEX
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.runtime.utils import NESTED_PATH_SPLIT
from jiuwen.core.tracer.handler import TracerHandlerName


def _get_component_metadata(runtime) -> dict:
    executable_id = runtime.executable_id()
    state = runtime.state()
    component_metadata = {"component_type": executable_id}
    loop_id = state.get_global(LOOP_ID)
    if loop_id is None:
        return component_metadata

    index = state.get_global(loop_id + NESTED_PATH_SPLIT + INDEX)
    component_metadata.update({
        "loop_node_id": loop_id,
        "loop_index": index + 1
    })
    runtime.tracer().pop_workflow_span(executable_id, runtime.parent_id())
    return component_metadata


async def trace_inputs(runtime, inputs: Optional[dict]):
    tracer = runtime.tracer()
    if tracer is None:
        return
    executable_id = runtime.executable_id()
    parent_id = runtime.parent_id()
    await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_pre_invoke",
                         invoke_id=executable_id,
                         parent_node_id=parent_id,
                         inputs=inputs,
                         component_metadata=_get_component_metadata(runtime))
    runtime.state().update_trace(tracer.get_workflow_span(executable_id, parent_id))


async def trace_outputs(runtime, outputs: Optional[dict]):
    tracer = runtime.tracer()
    if tracer is None:
        return
    executable_id = runtime.executable_id()
    parent_id = runtime.parent_id()
    await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_post_invoke",
                         invoke_id=executable_id,
                         parent_node_id=parent_id,
                         outputs=outputs)
    runtime.state().update_trace(tracer.get_workflow_span(executable_id, parent_id))


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
    runtime.state().update_trace(tracer.get_workflow_span(invoke_id, parent_id))


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
    runtime.state().update_trace(tracer.get_workflow_span(invoke_id, parent_id))
