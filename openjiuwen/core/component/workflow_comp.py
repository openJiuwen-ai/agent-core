#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import AsyncIterator

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.component.base import WorkflowComponent
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.base import INPUTS_KEY, CONFIG_KEY, Graph
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.runtime.base import ComponentExecutable
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.workflow.base import Workflow
from openjiuwen.graph.visualization.drawable_graph import DrawableGraph


SUB_WORKFLOW_COMPONENT = "sub_workflow"


class SubWorkflowComponent(WorkflowComponent, ComponentExecutable):
    def __init__(self, sub_workflow: Workflow):
        super().__init__()
        if sub_workflow is None:
            raise JiuWenBaseException(StatusCode.SUB_WORKFLOW_COMPONENT_INIT_ERROR.code,
                                      StatusCode.SUB_WORKFLOW_COMPONENT_INIT_ERROR.errmsg.format(
                                          error_msg="sub_workflow is None"))
        self._sub_workflow = sub_workflow

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        return await self._sub_workflow.sub_invoke(inputs.get(INPUTS_KEY), runtime.base(), inputs.get(CONFIG_KEY))

    async def stream(self, inputs: Input, runtime: Runtime, context: Context) -> AsyncIterator[Output]:
        async for value in self._sub_workflow.sub_stream(inputs.get(INPUTS_KEY),
                                                         runtime.base(), inputs.get(CONFIG_KEY)):
            yield value

    def graph_invoker(self) -> bool:
        return True

    def component_type(self) -> str:
        return SUB_WORKFLOW_COMPONENT

    @property
    def sub_workflow(self) -> Workflow:
        return self._sub_workflow
