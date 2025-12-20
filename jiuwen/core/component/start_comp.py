#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import TypedDict

from jiuwen.core.context_engine.base import Context
from jiuwen.core.runtime.base import ComponentExecutable
from jiuwen.core.runtime.runtime import Runtime
from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.component.base import WorkflowComponent
from jiuwen.core.graph.executable import Input, Output


class Start(ComponentExecutable, WorkflowComponent):
    def __init__(self, conf: dict = None):
        super().__init__()
        self._check_config(conf)
        self.conf = conf


    @staticmethod
    def _check_config(config):
        if not config:
            return

        defined_variables = config.get("inputs", {})
        if not isinstance(defined_variables, list):
            raise JiuWenBaseException(error_code=StatusCode.WORKFLOW_START_CREATE_VALUE.code,
                                      message=StatusCode.WORKFLOW_START_CREATE_VALUE.errmsg.format(
                                          reason="conf 'inputs' is not list"))
        for var in defined_variables:
            if not isinstance(var, dict):
                raise JiuWenBaseException(error_code=StatusCode.WORKFLOW_START_CREATE_VALUE.code,
                                      message=StatusCode.WORKFLOW_START_CREATE_VALUE.errmsg.format(
                                          reason="conf 'inputs' list item must be dict"))
            var_name = var.get("id")
            if not var_name:
                raise JiuWenBaseException(error_code=StatusCode.WORKFLOW_START_CREATE_VALUE.code,
                                          message=StatusCode.WORKFLOW_START_CREATE_VALUE.errmsg.format(
                                              reason="conf 'inputs' list item not contain `id`"))

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        self._validate_inputs(inputs)
        return self._fill_default_values(inputs)

    def _fill_default_values(self, inputs: Input):
        if not self.conf:
            return inputs
        defined_variables = self.conf.get("inputs", [])
        for var in defined_variables:
            var_name = var["id"]
            input_val = inputs.get(var_name)
            if input_val or input_val == False:
                continue
            default_value = var.get("default_value")
            if default_value or default_value is False:
                inputs[var_name] = default_value
        return inputs

    def _validate_inputs(self, inputs: Input):
        if not self.conf:
            return
        defined_variables = self.conf.get("inputs", {})
        variables_not_given = []
        for variable in defined_variables:
            if not variable.get("required"):
                continue
            variable_name = variable.get("id")
            input_val = inputs.get(variable_name)
            if input_val or input_val is False:
                continue
            variables_not_given.append(variable_name)
        if len(variables_not_given) > 0:
            raise JiuWenBaseException(error_code=StatusCode.WORKFLOW_START_MISSING_GLOBAL_VARIABLE_VALUE.code,
                                      message=StatusCode.WORKFLOW_START_MISSING_GLOBAL_VARIABLE_VALUE.errmsg.format(
                                          variable_name=variables_not_given))


class StartInputSchema(TypedDict):
    query: str
    dialogueHistory: list
    conversationHistory: list


class StartOutputSchema(TypedDict):
    query: str
    dialogueHistory: list
    conversationHistory: list
