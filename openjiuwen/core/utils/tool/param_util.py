#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List

from openjiuwen.core.utils.tool.types import ValueTypeEnum
from openjiuwen.core.utils.tool.param import Param


class ParamUtil:
    """Plugin parameter util"""

    @staticmethod
    def _basic_type_inputs(param: Param, inputs: dict):
        if ValueTypeEnum.is_nested_array(param.type):
            if not inputs.get(param.name) and inputs.get(param.name) is not False and param.default_value:
                inputs[param.name] = param.default_value
        else:
            if not inputs.get(param.name) and inputs.get(param.name) is not False:
                if param.default_value or param.default_value is False:
                    inputs[param.name] = param.default_value
        return inputs

    @staticmethod
    def _gen_new_inputs(param, inputs):
        if not ValueTypeEnum.is_nested_array(param.type):
            param_value = inputs.get(param.name)
            param_value = {} if not param_value else param_value
            new_inputs = [param_value]
        else:
            new_inputs = inputs.get(param.name, [{}])
            new_inputs = [{}] if not new_inputs else new_inputs
        return new_inputs

    @staticmethod
    def _assign_format_default_value(params: List[Param], inputs: dict):
        for param in params:
            if ValueTypeEnum.is_object(param.type):
                if not inputs.get(param.name) and param.default_value:
                    inputs[param.name] = param.default_value
                    continue
                if inputs.get(param.name) and param.default_value:
                    continue
                new_inputs = ParamUtil._gen_new_inputs(param, inputs)
                temp_inputs = [ParamUtil._assign_format_default_value(param.schema, item) for item in new_inputs]
                if not ValueTypeEnum.is_nested_array(param.type):
                    if not temp_inputs[0]:
                        continue
                    if not inputs:
                        inputs = {}
                    inputs[param.name] = temp_inputs[0]
                else:
                    if any(temp_inputs):
                        inputs[param.name] = temp_inputs
            else:
                inputs = ParamUtil._basic_type_inputs(param, inputs)
        return inputs

    @staticmethod
    def format_input_with_default_when_required(params: List[Param], inputs: dict):
        """format input with default"""
        inputs = ParamUtil._assign_format_default_value(params, inputs)
        return inputs
