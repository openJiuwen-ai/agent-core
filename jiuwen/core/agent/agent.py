#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import Any, Iterator, Dict, List

from jiuwen.core.agent.task.task_context import AgentRuntime
from jiuwen.core.runtime.workflow_manager import generate_workflow_key
from jiuwen.core.runtime.config import Config
from jiuwen.core.utils.tool.base import Tool
from jiuwen.core.utils.tool.function.function import LocalFunction
from jiuwen.core.utils.tool.service_api.restful_api import RestfulApi
from jiuwen.core.workflow.base import Workflow


class Agent(ABC):
    """
    The top-level abstract class and the common base class for all Agents.
    Subclasses must implement:
        - invoke : synchronous one-time call
        - stream : streaming call
    """
    def __init__(self, config: Config) -> None:
        self._runtime = AgentRuntime(config=config)
        self._controller: "Controller | None" = self._init_controller()
        self._agent_handler: "AgentHandler | None" = self._init_agent_handler()

    def _init_controller(self) -> "Controller | None":
        return None

    def _init_agent_handler(self) -> "AgentHandler | None":
        return None

    @abstractmethod
    async def invoke(self, inputs: Dict) -> Dict:
        pass

    @abstractmethod
    async def stream(self, inputs: Dict) -> Iterator[Any]:
        pass

    def bind_workflows(self, workflows: List[Workflow]):
        self._runtime.add_workflows(
            [(generate_workflow_key(workflow.config().metadata.id, workflow.config().metadata.version), workflow) for
             workflow in
             workflows])

    def bind_tools(self, tools: List[Tool]):
        self._runtime.add_tools([(tool.name, tool) for tool in tools if (isinstance(tool, RestfulApi) or isinstance(tool, LocalFunction))])
