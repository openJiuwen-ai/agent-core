# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
from typing import Callable, Union

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.component.condition.condition import Condition, FuncCondition
from openjiuwen.core.component.condition.expression import ExpressionCondition
from openjiuwen.core.runtime.runtime import Runtime, BaseRuntime
from openjiuwen.core.tracer.workflow_tracer import TracerWorkflowUtils
from openjiuwen.graph.visualization.drawable_edge import DrawableBranchRouter


WORKFLOW_DRAWABLE = "WORKFLOW_DRAWABLE"


class Branch:
    def __init__(self, condition: Union[str, Callable[[], bool], Condition], target: list[str],
                 branch_id: str = None):
        super().__init__()
        self.branch_id = branch_id
        if isinstance(condition, Condition):
            self._condition = condition
        elif isinstance(condition, str):
            self._condition = ExpressionCondition(condition)
        elif isinstance(condition, Callable):
            self._condition = FuncCondition(condition)
        else:
            raise JiuWenBaseException(StatusCode.BRANCH_COMPONENT_BRANCH_CONDITION_TYPE_ERROR.code,
                                      StatusCode.BRANCH_COMPONENT_BRANCH_CONDITION_TYPE_ERROR.errmsg)
        self.target = target

    def evaluate(self, runtime: BaseRuntime) -> bool:
        return self._condition(runtime)

    def trace_info(self, runtime: BaseRuntime) -> str:
        return self._condition.trace_info(runtime)


class BranchRouter:
    def __init__(self, report_trace: bool = False):
        super().__init__()
        self._branches: list[Branch] = []
        self._runtime: BaseRuntime = None
        self.report_trace = report_trace
        self._drawable_branch_router = None
        if os.environ.get(WORKFLOW_DRAWABLE, "false").lower() == "true":
            self._drawable_branch_router = DrawableBranchRouter(targets=[], datas=[])

    def add_branch(self, condition: Union[str, Callable[[], bool], Condition], target: Union[str, list[str]],
                   branch_id: str = None):
        if condition is None or target is None:
            raise JiuWenBaseException(StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.code,
                                      StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.errmsg.format(
                                          error_msg="condition is None or target is None"))
        target = [target] if isinstance(target, str) else target
        if self._drawable_branch_router:
            branch_data = branch_id if branch_id else ""
            if isinstance(condition, str):
                branch_data = condition
            for t in target:
                self._drawable_branch_router.targets.append(t)
                self._drawable_branch_router.datas.append(branch_data)
        self._branches.append(Branch(condition, target, branch_id))

    def get_drawable_branch_router(self):
        return self._drawable_branch_router

    def set_runtime(self, runtime: Union[Runtime, BaseRuntime]):
        if isinstance(runtime, Runtime):
            self._runtime = runtime.base()
            return
        if isinstance(runtime, BaseRuntime):
            self._runtime = runtime
            return
        raise JiuWenBaseException(
            StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.code,
            StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.errmsg.format(error_msg="runtime type is wrong"),
        )

    async def __call__(self, *args, **kwargs) -> list[str]:
        runtime = self._runtime
        if self.report_trace:
            branches = []
            for branch in self._branches:
                branches.append({
                    "branch_id": branch.branch_id,
                    "condition": branch.trace_info(runtime)
                })
            await TracerWorkflowUtils.trace_component_inputs(runtime, {"branches": branches})
        for branch in self._branches:
            if branch.evaluate(runtime):
                if self.report_trace:
                    await TracerWorkflowUtils.trace_component_outputs(runtime, {"branch_id": branch.branch_id})
                    await TracerWorkflowUtils.trace_component_done(runtime)
                return branch.target
        raise JiuWenBaseException(StatusCode.BRANCH_COMPONENT_BRANCH_NOT_FOUND_ERROR.code,
                                  StatusCode.BRANCH_COMPONENT_BRANCH_NOT_FOUND_ERROR.errmsg)
