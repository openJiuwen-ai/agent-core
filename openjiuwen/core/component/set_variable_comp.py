#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.component.base import WorkflowComponent
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.runtime.base import ComponentExecutable
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.runtime.workflow import NodeRuntime
from openjiuwen.core.runtime.utils import extract_origin_key, is_ref_path, NESTED_PATH_SPLIT


class SetVariableComponent(WorkflowComponent, ComponentExecutable):

    def __init__(self, variable_mapping: dict[str, Any]):
        super().__init__()
        if not variable_mapping:
            raise JiuWenBaseException(StatusCode.SET_VAR_COMPONENT_VAR_MAPPING_ERROR.code,
                                      StatusCode.SET_VAR_COMPONENT_VAR_MAPPING_ERROR.errmsg.format(
                                          error_msg=f'variable_mapping is None or empty'))
        self._variable_mapping = variable_mapping

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        root_runtime = runtime.base().parent()
        for left, right in self._variable_mapping.items():
            left_ref_str = extract_origin_key(left)
            keys = left_ref_str.split(NESTED_PATH_SPLIT)

            if len(keys) == 0:
                raise JiuWenBaseException(StatusCode.SET_VAR_COMPONENT_VAR_MAPPING_ERROR.code,
                                          StatusCode.SET_VAR_COMPONENT_VAR_MAPPING_ERROR.errmsg.format(
                                              error_msg=f'key[{left}] not supported format'))

            node_id = keys[0]
            node_runtime = NodeRuntime(root_runtime, node_id)
            node_runtime.state().set_outputs(SetVariableComponent.generate_output(
                keys[1:], SetVariableComponent.generate_value(runtime, right)
            ))
        return None

    @staticmethod
    def generate_value(runtime: Runtime, value: Any):
        if isinstance(value, str) and is_ref_path(value):
            ref_str = extract_origin_key(value)
            return runtime.get_global_state(ref_str)
        return value

    @staticmethod
    def generate_output(keys: list[str], value: Any):
        output = value
        for i in range(len(keys) - 1, -1, -1):
            key = keys[i]
            output = {key: output}

        return output
