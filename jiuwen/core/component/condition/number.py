#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Union, Any

from jiuwen.core.component.condition.condition import Condition
from jiuwen.core.runtime.runtime import BaseRuntime
from jiuwen.core.graph.executable import Input, Output
from jiuwen.core.common.constants.constant import INDEX
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode


class NumberCondition(Condition):
    def __init__(self, limit: Union[str, int]):
        super().__init__(limit)
        self._limit = limit

    def invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        current_idx = runtime.state().get(INDEX) + 1
        limit_num = inputs
        return current_idx < limit_num


class NumberConditionInRuntime(Condition):
    def __init__(self, limit: int):
        super().__init__()
        self._limit = limit

    def invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        current_idx = runtime.state().get(INDEX) + 1
        limit_num = self._limit
        if limit_num is None:
            raise JiuWenBaseException(StatusCode.NUMBER_CONDITION_ERROR.code, "loop_number variable not found or is None")
            
        return current_idx < limit_num
