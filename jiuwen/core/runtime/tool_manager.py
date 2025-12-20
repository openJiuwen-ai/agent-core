#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Optional, Tuple

from jiuwen.core.common.exception.exception import JiuWenBaseException
from jiuwen.core.common.exception.status_code import StatusCode
from jiuwen.core.tracer.decorator import decrate_tool_with_trace
from jiuwen.core.utils.llm.messages import ToolInfo, Function
from jiuwen.core.utils.tool.base import Tool
from jiuwen.core.runtime.thread_safe_dict import ThreadSafeDict


class ToolMgr:
    def __init__(self) -> None:
        self._tools: ThreadSafeDict[str, Tool] = ThreadSafeDict()
        self._tool_infos: ThreadSafeDict[str, ToolInfo] = ThreadSafeDict()

    def add_tool(self, tool_id: str, tool: Tool) -> None:
        if tool_id is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_TOOL_GET_FAILED.code,
                                      StatusCode.RUNTIME_TOOL_GET_FAILED.errmsg.format(
                                          reason="tool_id is invalid, can not be None"))
        if tool is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_TOOL_GET_FAILED.code,
                                      StatusCode.RUNTIME_TOOL_GET_FAILED.errmsg.format(
                                          reason="tool is invalid, can not be None"))
        self._tools[tool_id] = tool
        if hasattr(tool, "get_tool_info"):
            self._tool_infos[tool_id] = tool.get_tool_info()
        else:
            self._tool_infos[tool_id] = ToolInfo(function=Function())

    def add_tools(self, tools: List[Tuple[str, Tool]]):
        if not tools:
            return
        for id, tool in tools:
            self.add_tool(id, tool)

    def find_tool_by_name(self, name: str) -> Optional[Tool]:
        if name is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_TOOL_GET_FAILED.code,
                                      StatusCode.RUNTIME_TOOL_GET_FAILED.errmsg.format(
                                          reason="name is invalid, can not be None"))
        return self._tools.get(name)

    def get_tool(self, tool_id: str, runtime=None) -> Optional[Tool]:
        if tool_id is None:
            raise JiuWenBaseException(StatusCode.RUNTIME_TOOL_GET_FAILED.code,
                                      StatusCode.RUNTIME_TOOL_GET_FAILED.errmsg.format(
                                          reason="tool_id is invalid, can not be None"))
        tool = self._tools.get(tool_id)
        return decrate_tool_with_trace(tool, runtime)

    def remove_tool(self, tool_id: str) -> Optional[Tool]:
        if tool_id is None:
            return tool_id
        self._tool_infos.pop(tool_id, None)
        return self._tools.pop(tool_id, None)

    def get_tool_infos(self, tool_id: List[str]):
        if not tool_id:
            return [info for info in self._tool_infos.values()]
        infos = []
        for id in tool_id:
            if id is None:
                raise JiuWenBaseException(StatusCode.RUNTIME_TOOL_TOOL_INFO_GET_FAILED.code,
                                          StatusCode.RUNTIME_TOOL_GET_FAILED.errmsg.format(
                                              reason="tool_id is invalid, can not be None"))
            infos.append(self._tool_infos.get(id))
        return infos
