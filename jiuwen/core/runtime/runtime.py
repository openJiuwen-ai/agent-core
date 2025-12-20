#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from typing import Any, Union, Optional, List, TypeVar, Tuple

from jiuwen.core.context_engine.base import Context
from jiuwen.core.runtime.callback_manager import CallbackManager
from jiuwen.core.runtime.config import Config
from jiuwen.core.runtime.state import State
from jiuwen.core.stream.manager import StreamWriterManager
from jiuwen.core.stream.base import OutputSchema
from jiuwen.core.stream.writer import StreamWriter
from jiuwen.core.utils.llm.base import BaseChatModel
from jiuwen.core.utils.llm.messages import ToolInfo
from jiuwen.core.utils.prompt.template.template import Template
from jiuwen.core.utils.tool.base import Tool

ResourceManager = TypeVar("ResourceManager", contravariant=True)

class BaseRuntime(ABC):
    @abstractmethod
    def config(self) -> Config:
        pass

    @abstractmethod
    def state(self) -> State:
        pass

    @abstractmethod
    def tracer(self) -> Any:
        pass

    @abstractmethod
    def stream_writer_manager(self) -> StreamWriterManager:
        pass

    @abstractmethod
    def callback_manager(self) -> CallbackManager:
        pass

    @abstractmethod
    def controller_context_manager(self):
        pass

    @abstractmethod
    def session_id(self) -> str:
        pass

    @abstractmethod
    def resource_manager(self) -> ResourceManager:
        pass

    @abstractmethod
    def context(self) -> Context:
        pass

    @abstractmethod
    def checkpointer(self):
        pass


Workflow = TypeVar("Workflow", contravariant=True)


class Runtime(ABC):
    @abstractmethod
    def executable_id(self) -> str:
        pass

    @abstractmethod
    def session_id(self) -> str:
        pass

    @abstractmethod
    def update_state(self, data: dict):
        pass

    @abstractmethod
    def get_state(self, key: Union[str, list, dict] = None) -> Any:
        pass

    @abstractmethod
    def update_global_state(self, data: dict):
        pass

    @abstractmethod
    def get_global_state(self, key: Union[str, list, dict] = None) -> Any:
        pass

    @abstractmethod
    def stream_writer(self) -> Optional[StreamWriter]:
        pass

    @abstractmethod
    def custom_writer(self) -> Optional[StreamWriter]:
        pass

    @abstractmethod
    async def write_stream(self, data: Union[dict, OutputSchema]):
        pass

    @abstractmethod
    async def write_custom_stream(self, data: dict):
        pass

    @abstractmethod
    async def trace(self, data: dict):
        pass

    @abstractmethod
    async def trace_error(self, error: Exception):
        pass

    @abstractmethod
    async def interact(self, value):
        pass

    @abstractmethod
    def add_prompt(self, template_id: str, template: Template):
        pass

    @abstractmethod
    def add_prompts(self, templates: List[Tuple[str, Template]]):
        pass

    @abstractmethod
    def remove_prompt(self, template_id: str) -> Optional[Template]:
        pass

    @abstractmethod
    def get_prompt(self, template_id: str) -> Optional[Template]:
        pass

    @abstractmethod
    def add_model(self, model_id: str, model: BaseChatModel):
        pass

    @abstractmethod
    def add_models(self, models: List[Tuple[str, BaseChatModel]]):
        pass

    @abstractmethod
    def remove_model(self, model_id: str) -> Optional[BaseChatModel]:
        pass

    @abstractmethod
    def get_model(self, model_id: str) -> Optional[BaseChatModel]:
        pass

    @abstractmethod
    def add_workflow(self, workflow_id: str, workflow: Workflow):
        pass

    @abstractmethod
    def add_workflows(self, workflows: List[Tuple[str, Workflow]]):
        pass

    @abstractmethod
    def remove_workflow(self, workflow_id: str) -> Optional[Workflow]:
        pass

    @abstractmethod
    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        pass

    @abstractmethod
    def add_tool(self, tool_id: str, tool: Tool) -> Optional[Tool]:
        pass

    @abstractmethod
    def add_tools(self, tools: List[Tuple[str, Tool]]):
        pass

    @abstractmethod
    def remove_tool(self, tool_id: str) -> Optional[Tool]:
        pass

    @abstractmethod
    def get_tool(self, tool_id: str) -> Optional[Tool]:
        pass

    @abstractmethod
    def get_tool_info(self, tool_id: List[str] = None, workflow_id: List[str] = None) -> List[ToolInfo]:
        pass

    @abstractmethod
    def get_workflow_config(self, workflow_id):
        pass

    @abstractmethod
    def get_agent_config(self):
        pass

    @abstractmethod
    def base(self) -> BaseRuntime:
        pass

    async def post_run(self):
        pass

    async def pre_run(self, **kwargs):
        pass

    async def release(self, session_id: str):
        pass


class ProxyRuntime(BaseRuntime):
    def __init__(self, stub: BaseRuntime = None):
        self._stub = stub

    def set_runtime(self, stub: BaseRuntime):
        self._stub = stub

    def config(self) -> Config:
        return self._stub.config()

    def state(self) -> State:
        return self._stub.state()

    def tracer(self) -> Any:
        return self._stub.tracer()

    def stream_writer_manager(self) -> StreamWriterManager:
        return self._stub.stream_writer_manager()

    def callback_manager(self) -> CallbackManager:
        return self._stub.callback_manager()

    def controller_context_manager(self):
        return self._stub.controller_context_manager()

    def resource_manager(self):
        return self._stub.resource_manager()

    def context(self) -> Context:
        pass

    def session_id(self) -> str:
        return self._stub.session_id()

    def checkpointer(self):
        return self._stub.checkpointer()
