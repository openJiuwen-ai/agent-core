#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import TypeVar

from openjiuwen.core.runtime.resources_manager.tool_manager import ToolMgr
from openjiuwen.core.runtime.resources_manager.workflow_manager import WorkflowMgr
from openjiuwen.core.runtime.resources_manager.prompt_manager import PromptMgr
from openjiuwen.core.runtime.resources_manager.model_manager import ModelMgr

Workflow = TypeVar("Workflow", contravariant=True)


class ResourceManager(ABC):
    @abstractmethod
    def tool(self) -> ToolMgr:
        pass

    @abstractmethod
    def prompt(self) -> PromptMgr:
        pass

    @abstractmethod
    def model(self) -> ModelMgr:
        pass

    @abstractmethod
    def workflow(self) -> WorkflowMgr:
        pass


class ResourceMgr(ResourceManager):
    """
    Resource Manager for Model, Workflow, Prompt, Tool
    """

    def __init__(self) -> None:
        self._tool_mgr = ToolMgr()
        self._workflow_mgr = WorkflowMgr()
        self._prompt_mgr = PromptMgr()
        self._model_mgr = ModelMgr()

    def tool(self) -> ToolMgr:
        return self._tool_mgr

    def prompt(self) -> PromptMgr:
        return self._prompt_mgr

    def model(self) -> ModelMgr:
        return self._model_mgr

    def workflow(self) -> WorkflowMgr:
        return self._workflow_mgr