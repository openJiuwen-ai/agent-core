# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import AsyncIterator

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.workflow.components.component import WorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.base import INPUTS_KEY, CONFIG_KEY
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session import Session
from openjiuwen.core.workflow.workflow import Workflow


SUB_WORKFLOW_COMPONENT = "sub_workflow"


class SubWorkflowComponent(WorkflowComponent):
    def __init__(self, sub_workflow: Workflow):
        super().__init__()
        if sub_workflow is None:
            raise JiuWenBaseException(StatusCode.SUB_WORKFLOW_COMPONENT_INIT_ERROR.code,
                                      StatusCode.SUB_WORKFLOW_COMPONENT_INIT_ERROR.errmsg.format(
                                          error_msg="sub_workflow is None"))
        self._sub_workflow = sub_workflow

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        return await self._sub_workflow.invoke(inputs.get(INPUTS_KEY), session.base(), context, config=inputs.get(CONFIG_KEY), is_sub=True)

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        async for value in self._sub_workflow.stream(inputs.get(INPUTS_KEY),
                                                         session.base(), config=inputs.get(CONFIG_KEY), is_sub=True):
            yield value

    def graph_invoker(self) -> bool:
        return True

    def component_type(self) -> str:
        return SUB_WORKFLOW_COMPONENT

    @property
    def sub_workflow(self) -> Workflow:
        return self._sub_workflow
