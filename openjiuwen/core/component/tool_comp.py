#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from dataclasses import dataclass
from typing import Union, List, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.security.exception_utils import ExceptionUtils
from openjiuwen.core.component.base import ComponentConfig, WorkflowComponent
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.executable import Executable, Input, Output
from openjiuwen.core.runtime.base import ComponentExecutable
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.utils.tool import constant
from openjiuwen.core.utils.tool.base import Tool
from openjiuwen.core.utils.tool.function.function import LocalFunction
from openjiuwen.core.utils.tool.param import Param


DEFAULT_EXCEPTION_ERROR_CODE = -1


@dataclass
class ToolComponentConfig(ComponentConfig):
    pass


class ToolComponentInput(BaseModel):
    model_config = ConfigDict(extra='allow')   # Allow any extra fields


class ToolComponentOutput(BaseModel):
    error_code: int = Field(default=0, alias=constant.ERR_CODE)
    error_message: str = Field(default="", alias=constant.ERR_MESSAGE)
    data: Any = Field(default="", alias=constant.RESTFUL_DATA)


class ToolExecutable(ComponentExecutable):

    def __init__(self, config: ToolComponentConfig):
        super().__init__()
        self._config = config
        self._tool: Union[Tool, None] = None

    @staticmethod
    def _validate_inputs(inputs) -> dict:
        try:
            return ToolComponentInput(**inputs).model_dump()
        except ValidationError as e:
            ExceptionUtils.raise_exception(StatusCode.TOOL_COMPONENT_INPUTS_ERROR,
                                           ExceptionUtils.format_validation_error(e))

    @staticmethod
    def _set_defaults_for_required_params(inputs, params):
        result = inputs
        for param in params:
            if param.required and param.name and inputs.get(param.name) is None:
                if param.default_value is not None:
                    result[param.name] = param.default_value
                else:
                    ExceptionUtils.raise_exception(StatusCode.TOOL_COMPONENT_CHECK_PARAM_ERROR,
                                                   f"Required parameter {param.name} is missing.")
        return result

    @staticmethod
    def _validate_inputs_type(inputs, params):
        for param in params:
            if param.name in inputs:
                value = inputs[param.name]
                if value is None or not isinstance(param.type, str):
                    ExceptionUtils.raise_exception(StatusCode.TOOL_COMPONENT_CHECK_PARAM_ERROR,
                                                   f"Parameter {param.name} or type is None.")

                if "integer" == param.type.lower():
                    try:
                        value = int(value)
                    except ValueError:
                        ExceptionUtils.raise_exception(StatusCode.TOOL_COMPONENT_CHECK_PARAM_ERROR,
                                                       f"Parameter {param.name} is not an integer.")
                elif "number" == param.type.lower():
                    try:
                        value = float(value)
                    except ValueError:
                        ExceptionUtils.raise_exception(StatusCode.TOOL_COMPONENT_CHECK_PARAM_ERROR,
                                                       f"Parameter {param.name} is not a float.")
                elif "boolean" == param.type.lower():
                    if value in [True, False, "true", "false", "True", "False"]:
                        value = bool(value)
                    else:
                        ExceptionUtils.raise_exception(StatusCode.TOOL_COMPONENT_CHECK_PARAM_ERROR,
                                                       f"Parameter {param.name} is not a boolean.")
                elif "string" == param.type.lower():
                    value = str(value)
                elif "object" == param.type.lower():
                    if not isinstance(value, dict):
                        ExceptionUtils.raise_exception(StatusCode.TOOL_COMPONENT_CHECK_PARAM_ERROR,
                                                       f"Parameter {param.name} is not an object.")
                elif "array" == param.type.lower():
                    if not isinstance(value, list):
                        ExceptionUtils.raise_exception(StatusCode.TOOL_COMPONENT_CHECK_PARAM_ERROR,
                                                       f"Parameter {param.name} is not an array.")
                else:
                    ExceptionUtils.raise_exception(StatusCode.TOOL_COMPONENT_CHECK_PARAM_ERROR,
                                                   f"Parameter {param.name}, {param.type} is not a valid type.")
                inputs[param.name] = value

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        if self._tool is None:
            ExceptionUtils.raise_exception(StatusCode.TOOL_COMPONENT_BIND_TOOL_FAILED)
        tool_inputs = self._validate_inputs(inputs)
        formatted_inputs = self._prepare_inputs(tool_inputs, self._get_tool_param())
        try:
            response = await self._tool.ainvoke(formatted_inputs)
            response = self._post_process_tool_result(response)
        except Exception as e:
            response = {constant.ERR_MESSAGE: "Failed to execute tool", constant.RESTFUL_DATA: "",
                        constant.ERR_CODE: e.code if hasattr(e, "code") else DEFAULT_EXCEPTION_ERROR_CODE}

        return self._create_output(response)

    def set_tool(self, tool: Tool):
        self._tool = tool
        return self

    def _create_output(self, response: dict):
        return ToolComponentOutput(**response).model_dump()

    def _get_tool_param(self) -> List[Param]:
        return self._tool.params if hasattr(self._tool, "params") else []

    def _prepare_inputs(self, tool_inputs, params: List[Param]) -> dict:
        result = tool_inputs
        result = self._set_defaults_for_required_params(result, params)
        self._validate_inputs_type(result, params)
        return result

    def _post_process_tool_result(self, tool_result):
        result = dict()
        if isinstance(self._tool, LocalFunction):
            result[constant.RESTFUL_DATA] = tool_result
        else:
            result.update(tool_result)
        return result


class ToolComponent(WorkflowComponent):

    def __init__(self, config: ToolComponentConfig):
        super().__init__()
        self._config = config
        self._tool = None

    def to_executable(self) -> Executable:
        return ToolExecutable(self._config).set_tool(self._tool)

    def bind_tool(self, tool: Tool):
        self._tool = tool
        return self
