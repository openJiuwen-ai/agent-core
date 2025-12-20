#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Callable, Union, Hashable, Any

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.component.base import WorkflowComponent
from openjiuwen.core.component.branch_router import BranchRouter
from openjiuwen.core.component.condition.condition import Condition
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.base import Graph
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.runtime.base import ComponentExecutable
from openjiuwen.core.runtime.runtime import Runtime


class BranchComponent(WorkflowComponent, ComponentExecutable):

    def __init__(self):
        super().__init__()
        self._router = BranchRouter(True)

    def add_branch(self, condition: Union[str, Callable[[], bool], Condition], target: Union[str, list[str]],
                   branch_id: str = None):
        self._validata_branch_param(condition)
        self._validata_branch_param(target)
        for item in target:
            self._validata_branch_param(item)
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

    def _validata_branch_param(self, param_value: Any):
        if not param_value:
            error_msg = f"{param_value} is invalid , can not be None or empty"
            raise JiuWenBaseException(
                StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.code,
                StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.errmsg.format(error_msg=error_msg),
            )
