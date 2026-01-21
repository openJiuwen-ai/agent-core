# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from typing import List, Tuple, Optional, Union, Callable, Awaitable

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.resources_manager.abstract_manager import AbstractManager
from openjiuwen.core.session.tracer import decorate_workflow_with_trace
from openjiuwen.core.foundation.tool import ToolInfo

WorkflowProvider = Union[Callable[[], "Workflow"], Callable[[], Awaitable["Workflow"]]]


class WorkflowMgr(AbstractManager["Workflow"]):
    def __init__(self):
        super().__init__()
        self._workflow_tool_infos: dict[str, ToolInfo] = {}

    def add_workflow(self, workflow_id: str, workflow: Union["Workflow", WorkflowProvider]) -> None:
        self._validate_id(workflow_id, StatusCode.SESSION_WORKFLOW_ADD_FAILED, "workflow")
        self._validate_resource(workflow, StatusCode.SESSION_WORKFLOW_ADD_FAILED,
                                "workflow is invalid, can not be None")

        # Define validation function for non-callable workflows
        def validate_workflow(workflow_obj):
            if workflow_obj.card.tool_info() is None:
                logger.warn(f"add a workflow without tool_info, workflow_id={workflow_id}")
            else:
                self._workflow_tool_infos[workflow_id] = workflow_obj.card.tool_info()
            return workflow_obj

        self._add_resource(workflow_id, workflow, StatusCode.SESSION_WORKFLOW_ADD_FAILED, validate_workflow)

    def add_workflows(self, workflows: List[Tuple[str, Union["Workflow", WorkflowProvider]]]):
        if not workflows:
            return
        for key, workflow in workflows:
            self.add_workflow(key, workflow)

    def get_workflow_sync(self, workflow_id: str, session=None) -> "Workflow":
        try:
            loop = asyncio.get_running_loop()
            return asyncio.run_coroutine_threadsafe(self.get_workflow(workflow_id, session), loop=loop).result()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(
                    self.get_workflow(workflow_id, session)
                )
            finally:
                loop.close()

    async def get_workflow(self, workflow_id: str, session=None):
        # Validate ID using base class method
        self._validate_id(workflow_id, StatusCode.SESSION_WORKFLOW_GET_FAILED, "workflow")
        try:
            resource = self._resources.get(workflow_id)
            if resource:
                return resource
            # Try to create from provider
            provider = self._providers.get(workflow_id)
            if provider is None:
                return None
            result = provider()
            if asyncio.iscoroutine(result):
                workflow = await result
            else:
                workflow = result
            if hasattr(workflow, "card"):
                self._workflow_tool_infos[workflow_id] = workflow.card.tool_info()
            else:
                raise TypeError(f"Workflow must have card")
            return decorate_workflow_with_trace(workflow, session)
        except Exception as e:
            self._handle_exception(e, StatusCode.SESSION_WORKFLOW_GET_FAILED, "get")

    def remove_workflow(self, workflow_id: str) -> Optional["Workflow"]:
        self._validate_id(workflow_id, StatusCode.SESSION_WORKFLOW_REMOVE_FAILED, "workflow")

        try:
            workflow = self._remove_resource(workflow_id, StatusCode.SESSION_WORKFLOW_REMOVE_FAILED)
            self._workflow_tool_infos.pop(workflow_id, None)
            return workflow
        except Exception as e:
            self._handle_exception(e, StatusCode.SESSION_WORKFLOW_REMOVE_FAILED, "remove")
            return None

    def get_tool_infos(self, workflow_ids: str | List[str] = None):
        try:
            if not workflow_ids:
                return [info for info in self._workflow_tool_infos.values() if info is not None]

            if isinstance(workflow_ids, str):
                self._validate_id(workflow_ids, StatusCode.SESSION_WORKFLOW_TOOL_INFO_GET_FAILED, "workflow")
                return self._workflow_tool_infos.get(workflow_ids)

            infos = []
            for workflow_id in workflow_ids:
                self._validate_id(workflow_id, StatusCode.SESSION_WORKFLOW_TOOL_INFO_GET_FAILED, "workflow")
                infos.append(self._workflow_tool_infos.get(workflow_id))
            return infos
        except JiuWenBaseException:
            raise
        except Exception as e:
            self._handle_exception(e, StatusCode.SESSION_WORKFLOW_TOOL_INFO_GET_FAILED, "get_tool_info")
            return []

    def get_all_workflows(self) -> dict[str, Union["Workflow", WorkflowProvider]]:
        """
        Get all registered workflows including both instances and providers.
        
        Returns:
            A dictionary containing all workflow instances and providers,
            where key is workflow_id and value is either a workflow instance or provider function.
        """
        result = {}
        result.update(self._resources)
        result.update(self._providers)
        return result
