#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Callable, Union, Hashable

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.component.base import WorkflowComponent
from jiuwen.core.component.branch_router import BranchRouter
from jiuwen.core.component.condition.condition import Condition
from jiuwen.core.context_engine.base import Context
from jiuwen.core.graph.base import Graph
from jiuwen.core.graph.executable import Input, Output
from jiuwen.core.runtime.base import ComponentExecutable
from jiuwen.core.runtime.runtime import Runtime


class BranchComponent(WorkflowComponent, ComponentExecutable):

    def __init__(self):
        super().__init__()
        self._router = BranchRouter(True)

    def add_branch(self, condition: Union[str, Callable[[], bool], Condition], target: Union[str, list[str]],
                   branch_id: str = None):
        if not condition:
            raise JiuWenBaseException(StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.code,
                                      StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.errmsg.format(
                                          error_msg="condition is not invalid, can not be None"))
        if not target:
            raise JiuWenBaseException(StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.code,
                                      StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.errmsg.format(
                                          error_msg="target is not invalid, can not None or empty"))
        for item in target:
            if not item:
                raise JiuWenBaseException(StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.code,
                                          StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.errmsg.format(
                                              error_msg="target list item is not invalid, can not None or empty"))
        self._router.add_branch(condition, target, branch_id=branch_id)

    def router(self) -> Callable[..., Union[Hashable, list[Hashable]]]:
        return self._router

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        self._router.set_runtime(runtime)
        return {}

    def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False):
        graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)
        graph.add_conditional_edges(node_id, self.router())

    def skip_trace(self) -> bool:
        return True
