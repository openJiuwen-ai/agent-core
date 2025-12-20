#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Callable, Union

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.component.condition.condition import Condition, FuncCondition
from jiuwen.core.component.condition.expression import ExpressionCondition
from jiuwen.core.runtime.runtime import Runtime, BaseRuntime
from jiuwen.core.tracer.workflow_tracer import trace_outputs, trace_inputs


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

    def trace_info(self, runtime: BaseRuntime = None) -> str:
        return self._condition.trace_info(runtime)


class BranchRouter:
    def __init__(self, report_trace: bool = False):
        super().__init__()
        self._branches: list[Branch] = []
        self._runtime: BaseRuntime = None
        self.report_trace = report_trace

    def add_branch(self, condition: Union[str, Callable[[], bool], Condition], target: Union[str, list[str]],
                   branch_id: str = None):
        if condition is None or target is None:
            raise JiuWenBaseException(StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.code,
                                      StatusCode.BRANCH_COMPONENT_ADD_BRANCH_ERROR.errmsg.format(
                                          error_msg="condition is None or target is None"))
        target = [target] if isinstance(target, str) else target
        self._branches.append(Branch(condition, target, branch_id))

    def set_runtime(self, runtime: Union[Runtime, BaseRuntime]):
        if isinstance(runtime, Runtime):
            self._runtime = runtime.base()
            return
        self._runtime = runtime

    async def __call__(self, *args, **kwargs) -> list[str]:
        runtime = self._runtime
        if self.report_trace:
            branches = []
            for branch in self._branches:
                branches.append({
                    "branch_id": branch.branch_id,
                    "condition": branch.trace_info(runtime)
                })
            await trace_inputs(runtime, {"branches": branches})
        for branch in self._branches:
            if branch.evaluate(runtime):
                if self.report_trace:
                    await trace_outputs(runtime, {"branch_id": branch.branch_id})
                return branch.target
        raise JiuWenBaseException(StatusCode.BRANCH_COMPONENT_BRANCH_NOT_FOUND_ERROR.code,
                                  StatusCode.BRANCH_COMPONENT_BRANCH_NOT_FOUND_ERROR.errmsg)
