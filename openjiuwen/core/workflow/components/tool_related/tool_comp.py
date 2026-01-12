# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from dataclasses import dataclass
from typing import Union, List, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.security.exception_utils import ExceptionUtils
from openjiuwen.core.workflow.components.base import ComponentConfig
from openjiuwen.core.workflow.components.component import ComponentComposable, ComponentExecutable
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Executable, Input, Output
from openjiuwen.core.session import Session
from openjiuwen.core.foundation.tool import constant
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.foundation.tool import LocalFunction


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
            ExceptionUtils.raise_exception(StatusCode.COMPONENT_TOOL_INPUT_PARAM_ERROR,
                                           ExceptionUtils.format_validation_error(e))


    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        if self._tool is None:
            ExceptionUtils.raise_exception(StatusCode.COMPONENT_TOOL_EXECUTION_ERROR)
        tool_inputs = self._validate_inputs(inputs)

        try:
            response = await self._tool.invoke(tool_inputs, skip_inputs_validate=False, skip_none_value=True)
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

    def _post_process_tool_result(self, tool_result):
        result = dict()
        if isinstance(self._tool, LocalFunction):
            result[constant.RESTFUL_DATA] = tool_result
        else:
            result.update(tool_result)
        return result


class ToolComponent(ComponentComposable):

    def __init__(self, config: ToolComponentConfig):
        super().__init__()
        self._config = config
        self._tool = None

    def to_executable(self) -> Executable:
        return ToolExecutable(self._config).set_tool(self._tool)

    def bind_tool(self, tool: Tool):
        self._tool = tool
        return self
