#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Union, Any

from openjiuwen.core.component.condition.condition import Condition
from openjiuwen.core.runtime.runtime import BaseRuntime
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.common.constants.constant import INDEX
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode


class NumberCondition(Condition):
    def __init__(self, limit: Union[str, int]):
        super().__init__(limit)
        self._limit = limit

    def invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        current_idx = runtime.state().get(INDEX)
        limit_num = inputs
        return current_idx < limit_num


class NumberConditionInRuntime(Condition):
    def __init__(self, limit: int):
        super().__init__()
        self._limit = limit

    def invoke(self, inputs: Input, runtime: BaseRuntime) -> Output:
        current_idx = runtime.state().get(INDEX)
        limit_num = self._limit
        if limit_num is None:
            raise JiuWenBaseException(
                StatusCode.NUMBER_CONDITION_ERROR.code,
                "loop_number variable not found or is None"
            )
            
        return current_idx < limit_num
