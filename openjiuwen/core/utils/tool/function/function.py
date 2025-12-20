#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import inspect
from typing import Callable, List

from openjiuwen.core.utils.tool.schema import ToolInfo
from openjiuwen.core.utils.tool.base import Tool
from openjiuwen.core.utils.tool.constant import Input, Output
from openjiuwen.core.utils.tool.param import Param
from openjiuwen.core.utils.tool.param_util import ParamUtil


class LocalFunction(Tool):

    def __init__(self, name: str, description: str, params: List[Param] = None, func: Callable = None):
        super().__init__()
        self.name = name
        self.description = description
        self.params = params
        self.func = func

    def invoke(self, inputs: Input, **kwargs) -> Output:
        """invoke the tool"""
        inputs = ParamUtil.format_input_with_default_when_required(self.params, inputs)
        res = self.func(**inputs)
        return res

    async def ainvoke(self, inputs: Input, **kwargs) -> Output:
        """async invoke the tool"""
        inputs = ParamUtil.format_input_with_default_when_required(self.params, inputs)
        if inspect.iscoroutinefunction(self.func):
            res = await self.func(**inputs)
        else:
            res = self.func(**inputs)
        return res

    def get_tool_info(self) -> ToolInfo:
        tool_info_dict = Param.format_functions(self)
        tool_info = ToolInfo(**tool_info_dict)
        return tool_info
